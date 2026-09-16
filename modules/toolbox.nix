# Shared toolbox options and package derivations.
{
  config,
  lib,
  pkgs,
  options,
  ...
}:

let
  inherit (lib)
    concatStringsSep
    filter
    getExe
    literalExpression
    mkDefault
    mkIf
    mkOption
    types
    ;

  pnp = config.services.hermesPnP;
  agent = config.services.hermes-agent;
  cfg = pnp.toolbox;

  stateDir = agent.stateDir or "${config.home.homeDirectory or "/home/${agent.user or "hermes"}"}/.local/state/hermes-agent";
  home = "${stateDir}/home";
  hermesHome = agent.hermesHome or "${stateDir}/.hermes";

  toolboxDir = "${stateDir}/toolbox/bin";
  containerToolboxDir = "/data/toolbox/bin";

  sysPathTail = [
    "/run/current-system/sw/bin"
    "/usr/local/sbin"
    "/usr/local/bin"
    "/usr/sbin"
    "/usr/bin"
    "/sbin"
    "/bin"
  ];

  hostVenv = "${home}/.venv";
  containerVenv = "/data/home/.venv";

  containerPath = concatStringsSep ":" (
    [
      "${containerVenv}/bin"
      "/data/home/.npm-global/bin"
      "/data/home/.bun/bin"
      containerToolboxDir
    ]
    ++ sysPathTail
  );

  hostPath = concatStringsSep ":" (
    [
      "${hostVenv}/bin"
      toolboxDir
      "${home}/.bun/bin"
      "${home}/.npm-global/bin"
      "${home}/.local/bin"
      "/etc/profiles/per-user/${agent.user or "hermes"}/bin"
    ]
    ++ sysPathTail
  );

  pythonEnv = pkgs.python3.withPackages cfg.pythonPackages;

  pythonBins = pkgs.runCommand "hermes-python" { } ''
    mkdir -p "$out/bin"
    ln -s ${pythonEnv}/bin/python3 "$out/bin/python3"
    ln -s ${pythonEnv}/bin/python3 "$out/bin/python"
  '';

  wrapGh =
    (options.services.hermesPnP ? git)
    && pnp.git.credentialHelper.enable;

  ghForToolbox =
    if wrapGh then
      pkgs.writeShellApplication {
        name = "gh";
        runtimeInputs = [
          pkgs.git
          pkgs.gnused
          pkgs.coreutils
        ];
        text = ''
          t=$(printf 'protocol=https\nhost=github.com\n\n' | git credential fill 2>/dev/null | sed -n 's/^password=//p' || true)
          if [ -n "$t" ]; then
            export GH_TOKEN="$t"
          fi
          exec ${getExe pkgs.gh} "$@"
        '';
      }
    else
      pkgs.gh;

  defaultToolboxPackages = [
    pythonEnv
    pkgs.pandoc
    pkgs.bun
    pkgs.nodejs
    pkgs.git
    pkgs.ripgrep
    pkgs.jq
    pkgs.yq-go
    pkgs.curl
    pkgs.wget
    pkgs.unzip
    pkgs.zip
    pkgs.imagemagick
    pkgs.tree
    pkgs.rsync
    pkgs.openssh
    pkgs.ffmpeg
    pkgs.sox
    pkgs.poppler-utils
    (pkgs.tesseract.override { enableLanguages = [ "eng" "deu" ]; })
    pkgs.gnupg
    pkgs.age
    pkgs.file
    pkgs.which
    pkgs.coreutils
    pkgs.findutils
    pkgs.gawk
    pkgs.gnused
    pkgs.gnutar
    pkgs.gzip
    pkgs.bzip2
    pkgs.xz
    pkgs.zstd
    pkgs.p7zip
    pkgs.htop
    pkgs.ncdu
    pkgs.lsof
    pkgs.netcat-gnu
    ghForToolbox
  ];

  agentExtraPkgs = agent.extraPackages or [ ];
  extras = cfg.extraPackages ++ agentExtraPkgs;
  toolboxPaths = defaultToolboxPackages ++ (if wrapGh then filter (p: p != pkgs.gh) extras else extras);

  hermesToolbox = pkgs.buildEnv {
    name = "hermes-toolbox";
    paths = toolboxPaths;
  };
in
{
  imports = [ ./enable.nix ];

  options.services.hermesPnP.toolbox = {
    enable = mkOption {
      type = types.bool;
      default = true;
      description = ''
        Opinionated everyday CLI buildEnv (the "sauce"): a curated
        ~40-package toolkit + python3 + login PATH.
      '';
    };

    extraPackages = mkOption {
      type = types.listOf types.package;
      default = [ ];
      description = "Append-only packages added to the toolbox set.";
    };

    paths = mkOption {
      type = types.listOf types.package;
      readOnly = true;
      visible = false;
      description = "Resolved toolbox paths.";
    };

    hermesToolbox = mkOption {
      type = types.package;
      readOnly = true;
      description = "The resolved hermesToolbox buildEnv derivation.";
    };

    pythonPackages = mkOption {
      type = types.functionTo (types.listOf types.package);
      default =
        ps: with ps; [
          requests
          pyyaml
          toml
          pip
          setuptools
          wheel
          numpy
          pillow
        ];
      defaultText = literalExpression "ps: with ps; [ requests pyyaml toml pip setuptools wheel numpy pillow ]";
      description = "Python packages baked into the toolbox python3/python.";
    };

    toolboxDir = mkOption {
      type = types.str;
      readOnly = true;
    };
    containerToolboxDir = mkOption {
      type = types.str;
      readOnly = true;
    };
    hostPath = mkOption {
      type = types.str;
      readOnly = true;
    };
    containerPath = mkOption {
      type = types.str;
      readOnly = true;
    };
  };

  config = mkIf (pnp.enable && cfg.enable) {
    services.hermesPnP.toolbox = {
      inherit
        toolboxDir
        containerToolboxDir
        hostPath
        containerPath
        ;
      paths = toolboxPaths;
      inherit hermesToolbox;
    };
  };
}
