# Shared agent package + store-safe env for every Hermes entrypoint.
#
# Official agent container execs current-package/bin/hermes (the wrapper)
# which already --set HERMES_BUNDLED_*. WebUI does not exec that wrapper,
# so the share map is injected into extraEnvironment. environment{} also
# gets it so native gateway / .env see the same store paths.
#
# Do not put store-path HERMES_BUNDLED_* on container.extraOptions:
# official identity hashes extraOptions, so a package rebuild would
# recreate the jail and wipe the writable apt/uv layer. Remapped jail
# paths (profile, token, /data/toolbox PATH) stay on extraOptions
# because those strings are stable.
#
# extras: forward whatever the consumer set on services.hermes-agent.
# Do not override the official package when both extra lists are empty
# (that would replace `full` with []).
{
  config,
  lib,
  pkgs,
  ...
}:

let
  inherit (lib)
    mkDefault
    mkIf
    mkMerge
    mkOption
    optionalAttrs
    types
    ;

  pnp = config.services.hermesPnP;
  agent = config.services.hermes-agent;

  extrasNonEmpty =
    agent.extraPythonPackages != [ ] || agent.extraDependencyGroups != [ ];

  officialPkg = pnp.internal.officialAgentPackageFor pkgs.stdenv.hostPlatform.system;

  makeBase =
    extraPythonPackages: extraDependencyGroups:
    if extraPythonPackages == [ ] && extraDependencyGroups == [ ] then
      officialPkg
    else
      officialPkg.override {
        inherit extraPythonPackages extraDependencyGroups;
      };

  silenceOverlay =
    hermesVenv:
    pkgs.runCommand "hermes-gateway-silence-fix" { } ''
      src=""
      for cand in ${hermesVenv}/lib/python*/site-packages/gateway; do
        if [ -d "$cand" ]; then src="$cand"; break; fi
      done
      if [ -z "$src" ]; then
        echo "silence fix: gateway package not found in hermesVenv" >&2
        exit 1
      fi
      mkdir -p "$out/site-packages/gateway"
      ${pkgs.rsync}/bin/rsync -a --copy-links --chmod=Du+w,Fu+w \
        "$src/" "$out/site-packages/gateway/"
      if grep -q 'return _canonical_silence_candidate(line) in LIVE_GATEWAY_SILENT_MARKERS' \
          "$out/site-packages/gateway/response_filters.py"; then
        ${pkgs.gnused}/bin/sed \
          's/return _canonical_silence_candidate(line) in LIVE_GATEWAY_SILENT_MARKERS/return any(c in LIVE_GATEWAY_SILENT_MARKERS for c in _canonical_silence_candidates(line))/' \
          "$out/site-packages/gateway/response_filters.py" \
          > "$out/site-packages/gateway/response_filters.py.new"
        mv "$out/site-packages/gateway/response_filters.py.new" \
          "$out/site-packages/gateway/response_filters.py"
      fi
      if ! grep -q '_canonical_silence_candidates(line)' \
          "$out/site-packages/gateway/response_filters.py"; then
        echo "silence fix: expected _canonical_silence_candidates usage missing" >&2
        exit 1
      fi
    '';

  wrapPackage =
    extraPythonPackages: extraDependencyGroups:
    let
      base = makeBase extraPythonPackages extraDependencyGroups;
      overlay =
        if pnp.packageFixes.silenceMarkers && (base ? hermesVenv) then
          silenceOverlay base.hermesVenv
        else
          null;
    in
    if overlay == null then
      base
    else
      pkgs.symlinkJoin {
        name = "hermes-agent-silence-fix";
        paths = [ base ];
        nativeBuildInputs = [ pkgs.makeWrapper ];
        postBuild = ''
          for bin in hermes hermes-agent hermes-acp; do
            if [ -e "$out/bin/$bin" ]; then
              wrapProgram "$out/bin/$bin" \
                --prefix PYTHONPATH : "${overlay}/site-packages"
            fi
          done
        '';
        passthru = (base.passthru or { }) // {
          silenceFixedGateway = overlay;
          unfixed = base;
        }
        // lib.optionalAttrs (base ? hermesVenv) {
          hermesVenv = base.hermesVenv;
        };
      };

  wrapped = lib.makeOverridable (
    {
      extraPythonPackages ? [ ],
      extraDependencyGroups ? [ ],
    }:
    wrapPackage extraPythonPackages extraDependencyGroups
  ) {
    extraPythonPackages = agent.extraPythonPackages;
    extraDependencyGroups = agent.extraDependencyGroups;
  };

  pkg = agent.package;
  share = "${pkg}/share/hermes-agent";

  silencePythonpath =
    if pnp.packageFixes.silenceMarkers && (pkg ? silenceFixedGateway) then
      "${pkg.silenceFixedGateway}/site-packages"
    else if pnp.packageFixes.silenceMarkers && (pkg ? hermesVenv) then
      "${silenceOverlay pkg.hermesVenv}/site-packages"
    else
      null;

  hermesRuntimeEnv = {
    HERMES_BUNDLED_PLUGINS = "${share}/plugins";
    HERMES_BUNDLED_SKILLS = "${share}/skills";
    HERMES_OPTIONAL_SKILLS = "${share}/optional-skills";
    HERMES_BUNDLED_LOCALES = "${share}/locales";
    HERMES_OPTIONAL_MCPS = "${share}/optional-mcps";
    HERMES_WEB_DIST = "${share}/web_dist";
    HERMES_TUI_DIR = "${pkg}/ui-tui";
  }
  // optionalAttrs (silencePythonpath != null) {
    PYTHONPATH = silencePythonpath;
  };
in
{
  options.services.hermesPnP = {
    packageFixes.silenceMarkers = mkOption {
      type = types.bool;
      default = true;
      description = ''
        Patch gateway silence-token matching via PYTHONPATH.
        Turn off when upstream ships the plural form.
      '';
    };

    internal.officialAgentPackageFor = mkOption {
      type = types.functionTo types.package;
      internal = true;
      default = system: throw "hermesPnP package wrap requires nixosModules.default (official agent package not wired for ${system})";
      defaultText = lib.literalExpression "system: throw \"…\"";
      description = "system → official hermes-agent package. Set by the composer flake.";
    };
  };

  config = mkIf pnp.enable (mkMerge [
    {
      services.hermes-agent.environment = lib.mapAttrs (_: mkDefault) hermesRuntimeEnv;

      services.hermes-webui.extraEnvironment = mkIf pnp.webui.enable (
        lib.mapAttrs (_: mkDefault) hermesRuntimeEnv
      );
    }
    (mkIf (pnp.packageFixes.silenceMarkers || extrasNonEmpty) {
      # mkDefault so a consumer (or eval check) package assignment wins
      # and this branch is the only place officialPkg is referenced.
      services.hermes-agent.package = mkDefault wrapped;
    })
  ]);
}
