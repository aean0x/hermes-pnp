# Same agent derivation for gateway and WebUI.
# HERMES_BUNDLED_* and optional PYTHONPATH go on environment{} and WebUI
# extraEnvironment. The official wrapper --set those for the jailed
# gateway. extraOptions is only for stable remapped paths — official
# identity hashes it.
# Forward extraPythonPackages / extraDependencyGroups. Leave package
# alone when both lists are empty.
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

  agentSrc = pnp.internal.officialAgentSrc;

  pnpOverlay =
    hermesVenv:
    pkgs.runCommand "hermes-pnp-pythonpath" { } ''
      mkdir -p "$out/site-packages"
      ${lib.optionalString pnp.packageFixes.silenceMarkers ''
        gw=""
        for cand in ${hermesVenv}/lib/python*/site-packages/gateway; do
          if [ -d "$cand" ]; then gw="$cand"; break; fi
        done
        if [ -z "$gw" ]; then
          echo "silence fix: gateway package not found in hermesVenv" >&2
          exit 1
        fi
        mkdir -p "$out/site-packages/gateway"
        ${pkgs.rsync}/bin/rsync -a --copy-links --chmod=Du+w,Fu+w \
          "$gw/" "$out/site-packages/gateway/"
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
      ''}
      ${lib.optionalString (pnp.packageFixes.missingPyModules && agentSrc != null) ''
        # uv2nix sealed venv only ships [tool.setuptools] py-modules. New
        # top-level hermes_*.py files (e.g. hermes_state_holders) crash the
        # gateway until upstream lists them.
        for f in ${agentSrc}/hermes_*.py; do
          [ -f "$f" ] || continue
          base=$(basename "$f")
          present=0
          for cand in ${hermesVenv}/lib/python*/site-packages/"$base"; do
            if [ -e "$cand" ]; then present=1; break; fi
          done
          if [ "$present" = 0 ]; then
            cp "$f" "$out/site-packages/"
          fi
        done
      ''}
    '';

  wrapPackage =
    extraPythonPackages: extraDependencyGroups:
    let
      base = makeBase extraPythonPackages extraDependencyGroups;
      overlay =
        if
          (pnp.packageFixes.silenceMarkers || pnp.packageFixes.missingPyModules)
          && (base ? hermesVenv)
        then
          pnpOverlay base.hermesVenv
        else
          null;
    in
    if overlay == null then
      base
    else
      pkgs.symlinkJoin {
        name = "hermes-agent-pnp-fix";
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

  overlayPythonpath =
    if pkg ? silenceFixedGateway then
      "${pkg.silenceFixedGateway}/site-packages"
    else if
      (pnp.packageFixes.silenceMarkers || pnp.packageFixes.missingPyModules) && (pkg ? hermesVenv)
    then
      "${pnpOverlay pkg.hermesVenv}/site-packages"
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
  // optionalAttrs (overlayPythonpath != null) {
    PYTHONPATH = overlayPythonpath;
  };
in
{
  options.services.hermesPnP = {
    packageFixes.silenceMarkers = mkOption {
      type = types.bool;
      default = true;
      description = ''
        Patch autonomous gateway silence matching via PYTHONPATH.
      '';
    };

    packageFixes.missingPyModules = mkOption {
      type = types.bool;
      default = true;
      description = ''
        Copy top-level hermes_*.py files from the hermes-agent source that
        the uv2nix sealed venv omitted (py-modules list drift).
      '';
    };

    internal.officialAgentPackageFor = mkOption {
      type = types.functionTo types.package;
      internal = true;
      default = system: throw "hermesPnP package wrap requires nixosModules.default (official agent package not wired for ${system})";
      defaultText = lib.literalExpression "system: throw \"…\"";
      description = "system → official hermes-agent package. Set by the composer flake.";
    };

    internal.officialAgentSrc = mkOption {
      type = types.nullOr types.path;
      internal = true;
      default = null;
      description = "hermes-agent flake source (for missing py-modules overlay).";
    };
  };

  config = mkIf pnp.enable (mkMerge [
    {
      services.hermes-agent.environment = lib.mapAttrs (_: mkDefault) hermesRuntimeEnv;

      services.hermes-webui.extraEnvironment = mkIf pnp.webui.enable (
        lib.mapAttrs (_: mkDefault) hermesRuntimeEnv
      );
    }
    (mkIf (pnp.packageFixes.silenceMarkers || pnp.packageFixes.missingPyModules || extrasNonEmpty) {
      # mkDefault so a consumer package assignment wins.
      services.hermes-agent.package = mkDefault wrapped;
    })
  ]);
}
