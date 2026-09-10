# Same agent derivation for gateway and WebUI.
# HERMES_BUNDLED_* and optional PYTHONPATH go on environment{} and WebUI
# extraEnvironment. The official wrapper --set those for the jailed
# gateway. extraOptions is only for stable remapped paths — official
# identity hashes it.
# Forward extraPythonPackages / extraDependencyGroups. Leave package
# alone when both lists are empty and pythonExtras is empty.
# pythonExtras names resolve against the hermes-agent flake's Python
# (same interpreter as the sealed venv). Transitive dists already in
# the venv are dropped at wrap time so the upstream collision check
# never fires. The overlay is on the Nix-wrapped hermes binary, so it
# applies to native systemd, Home Manager user units, and the Ubuntu
# jail (/nix/store:ro).
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

  pythonExtrasNonEmpty = pnp.pythonExtras != [ ];

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

  resolvePythonExtras =
    let
      pyPkgs = pnp.internal.officialPythonPackagesFor pkgs.stdenv.hostPlatform.system;
      leaves = map (
        name:
        pyPkgs.${name}
          or (throw "services.hermesPnP.pythonExtras: '${name}' is not an attr of hermes-agent python312Packages")
      ) pnp.pythonExtras;
    in
    if leaves == [ ] then [ ] else map (p: p.out or p) (pyPkgs.requiredPythonModules leaves);

  extrasOverlay =
    hermesVenv: extraPkgs:
    pkgs.runCommand "hermes-pnp-python-extras" { } ''
      mkdir -p "$out/site-packages"
      venv_sp=""
      for cand in ${hermesVenv}/lib/python*/site-packages; do
        if [ -d "$cand" ]; then venv_sp="$cand"; break; fi
      done
      if [ -z "$venv_sp" ]; then
        echo "python extras: hermesVenv site-packages not found" >&2
        exit 1
      fi
      extras=()
      ${lib.concatMapStringsSep "\n" (p: ''
        extras+=("${p.out or p}")
      '') extraPkgs}
      ${pkgs.python3}/bin/python3 ${./python_extras_filter.py} \
        --venv-site "$venv_sp" \
        --dest "$out/site-packages" \
        "''${extras[@]}"
    '';

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
        rf="$out/site-packages/gateway/response_filters.py"
        if grep -q 'return _canonical_silence_candidate(line) in LIVE_GATEWAY_SILENT_MARKERS' "$rf"; then
          ${pkgs.gnused}/bin/sed \
            's/return _canonical_silence_candidate(line) in LIVE_GATEWAY_SILENT_MARKERS/return any(c in LIVE_GATEWAY_SILENT_MARKERS for c in _canonical_silence_candidates(line))/' \
            "$rf" > "$rf.new"
          mv "$rf.new" "$rf"
        fi
        if ! grep -qE '_canonical_silence_candidates\(' "$rf"; then
          echo "silence fix: neither old line nor _canonical_silence_candidates() present" >&2
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

  hooksOverlay = pkgs.runCommand "hermes-pnp-hooks" { } ''
    mkdir -p "$out/site-packages"
    cp ${./hermes_pnp_hooks.py} "$out/site-packages/hermes_pnp_hooks.py"
    printf '%s\n' 'import hermes_pnp_hooks; hermes_pnp_hooks.install()' > "$out/site-packages/hermes_pnp_hooks.pth"
  '';

  wrapPackage =
    extraPythonPackages: extraDependencyGroups:
    let
      base = makeBase extraPythonPackages extraDependencyGroups;
      silenceOverlay =
        if
          (pnp.packageFixes.silenceMarkers || pnp.packageFixes.missingPyModules)
          && (base ? hermesVenv)
        then
          pnpOverlay base.hermesVenv
        else
          null;
      extrasPkgs = if pythonExtrasNonEmpty then resolvePythonExtras else [ ];
      extrasPythonpath =
        if extrasPkgs == [ ] || !(base ? hermesVenv) then
          null
        else
          "${extrasOverlay base.hermesVenv extrasPkgs}/site-packages";
      silencePythonpath =
        if silenceOverlay == null then null else "${silenceOverlay}/site-packages";
      pythonpath = lib.concatStringsSep ":" (
        lib.filter (p: p != null) [
          "${hooksOverlay}/site-packages"
          extrasPythonpath
          silencePythonpath
        ]
      );
    in
    if pythonpath == "" then
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
                --prefix PYTHONPATH : "${pythonpath}"
            fi
          done
        '';
        passthru = (base.passthru or { }) // {
          silenceFixedGateway = silenceOverlay;
          pythonExtrasOverlay = extrasPythonpath;
          hooksOverlay = "${hooksOverlay}/site-packages";
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

  overlayPythonpath = lib.concatStringsSep ":" (
    lib.filter (p: p != null) [
      "${hooksOverlay}/site-packages"
      (
        if pkg ? pythonExtrasOverlay && pkg.pythonExtrasOverlay != null then
          pkg.pythonExtrasOverlay
        else
          null
      )
      (
        if pkg ? silenceFixedGateway && pkg.silenceFixedGateway != null then
          "${pkg.silenceFixedGateway}/site-packages"
        else
          null
      )
    ]
  );

  hermesRuntimeEnv = {
    HERMES_BUNDLED_PLUGINS = "${share}/plugins";
    HERMES_BUNDLED_SKILLS = "${share}/skills";
    HERMES_OPTIONAL_SKILLS = "${share}/optional-skills";
    HERMES_BUNDLED_LOCALES = "${share}/locales";
    HERMES_OPTIONAL_MCPS = "${share}/optional-mcps";
    HERMES_WEB_DIST = "${share}/web_dist";
    HERMES_TUI_DIR = "${pkg}/ui-tui";
  }
  // optionalAttrs (overlayPythonpath != null && overlayPythonpath != "") {
    PYTHONPATH = overlayPythonpath;
  };
in
{
  options.services.hermesPnP = {
    pythonExtras = mkOption {
      type = types.listOf types.str;
      default = [ ];
      example = [
        "google-cloud-pubsub"
        "redis"
      ];
      description = ''
        Extra python312Packages attrs sealed into the gateway PYTHONPATH.

        Names resolve against the hermes-agent flake's nixpkgs (the same
        Python 3.12 that built the uv2nix venv). Host
        pkgs.python312Packages is the wrong interpreter and is silently
        dropped by requiredPythonModules.

        Transitive dists already in the sealed venv are omitted at wrap
        time, so overlapping trees (Pub/Sub, grpc, protobuf, google-api-core)
        do not trip the upstream extraPythonPackages collision assertion.

        Prefer this over services.hermes-agent.extraPythonPackages for any
        library that is not a pyproject extra. Pyproject extras still use
        extraDependencyGroups (uv2nix, no PYTHONPATH).

        The wrap is the Nix hermes binary. Native systemd, Home Manager
        user units, and the Ubuntu jail all execute it from /nix/store.
      '';
    };

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
      default = system: throw "hermesPnP package wrap requires the hermes-pnp flake module (official agent package not wired for ${system})";
      defaultText = lib.literalExpression "system: throw \"…\"";
      description = "system → official hermes-agent package. Set by the composer flake.";
    };

    internal.officialAgentSrc = mkOption {
      type = types.nullOr types.path;
      internal = true;
      default = null;
      description = "hermes-agent flake source (for missing py-modules overlay).";
    };

    internal.officialPythonPackagesFor = mkOption {
      type = types.functionTo types.raw;
      internal = true;
      default = system: throw "hermesPnP pythonExtras requires the hermes-pnp flake module (hermes-agent python312Packages not wired for ${system})";
      defaultText = lib.literalExpression "system: throw \"…\"";
      description = "system → hermes-agent flake python312Packages (same interpreter as hermesVenv).";
    };

    internal.runtimeEnv = mkOption {
      type = types.attrsOf types.str;
      internal = true;
      default = { };
      description = "Store-safe BUNDLED_*/PYTHONPATH map. Gateway and WebUI both consume this.";
    };
  };

  config = mkMerge [
    {
      assertions = [
        {
          assertion = pnp.pythonExtras == [ ] || pnp.enable;
          message = "services.hermesPnP.pythonExtras requires services.hermesPnP.enable = true";
        }
      ];
    }
    (mkIf pnp.enable (mkMerge [
      {
        services.hermesPnP.internal.runtimeEnv = hermesRuntimeEnv;
        services.hermes-agent.environment = lib.mapAttrs (_: mkDefault) hermesRuntimeEnv;
      }
      {
        # Always wrap: Vertex publisher prefix + Nix doctor skip live on PYTHONPATH.
        services.hermes-agent.package = mkDefault wrapped;
      }
    ]))
  ];
}
