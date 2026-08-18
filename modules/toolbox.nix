# Opinionated everyday CLI toolkit for Hermes container mode.
#
# Pattern: buildEnv -> /var/lib/hermes/toolbox/bin, which the container
# sees as /data/toolbox/bin (stateDir bind). Container PATH is pushed via
# container.extraOptions --env (NOT persisted into .env, so host `hermes
# chat` never inherits /data paths).
#
# This is the composer's "sauce": a curated ~40-package set + python3 +
# login PATH. Browser-specific aliases live in the browser module.
# Extend with toolbox.extraPackages. Disable with toolbox.enable = false.
{ config
, lib
, pkgs
, ...
}:

let
  inherit (lib)
    concatStringsSep
    literalExpression
    mkIf
    mkOption
    types
    ;

  inherit (import ../lib { inherit pkgs lib; }) mkDockerEnv containerData containerHome;

  pnp = config.services.hermesPnP;
  agent = config.services.hermes-agent;
  cfg = pnp.toolbox;

  # Official container conventions (see hermes-agent nixos module).
  stateDir = agent.stateDir;
  home = "${stateDir}/home";
  hermesHome = "${stateDir}/.hermes";

  toolboxDir = "${stateDir}/toolbox/bin";
  containerToolboxDir = "${containerData}/toolbox/bin";
  skillsDir = "${stateDir}/skills";
  pluginsDir = "${stateDir}/plugins";

  sysPathTail = [
    "/run/current-system/sw/bin"
    "/usr/local/sbin"
    "/usr/local/bin"
    "/usr/sbin"
    "/usr/bin"
    "/sbin"
    "/bin"
  ];

  containerPath = concatStringsSep ":" ([
    "${containerHome}/.npm-global/bin"
    "${containerHome}/.bun/bin"
    containerToolboxDir
  ] ++ sysPathTail);

  hostPath = concatStringsSep ":" ([
    toolboxDir
    "${home}/.bun/bin"
    "${home}/.npm-global/bin"
    "${home}/.local/bin"
    "/etc/profiles/per-user/${agent.user}/bin"
  ] ++ sysPathTail);

  containerProcessEnv = {
    PATH = containerPath;
    HERMES_PYTHON = "${containerToolboxDir}/python3";
  };

  # withPackages already ships `python` and `python3`; keep both names
  # explicit so a nixpkgs change cannot drop one.
  pythonEnv = pkgs.python3.withPackages cfg.pythonPackages;

  # Explicit python/python3 symlinks so the buildEnv path always has both.
  pythonBins = pkgs.runCommand "hermes-python" { } ''
    mkdir -p "$out/bin"
    ln -s ${pythonEnv}/bin/python3 "$out/bin/python3"
    ln -s ${pythonEnv}/bin/python3 "$out/bin/python"
  '';

  hermesToolbox = pkgs.buildEnv {
    name = "hermes-toolbox";
    paths = [
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
      pkgs.gh
    ] ++ cfg.extraPackages;
  };

  # The official module merges environmentFiles into $HERMES_HOME/.env;
  # strip container-only PATH entries so the host `hermes chat` CLI does
  # not inherit /data/toolbox paths.
  dotenvSanitize = pkgs.writeShellScript "hermes-toolbox-dotenv-sanitize" ''
    env_file=${hermesHome}/.env
    if [ -f "$env_file" ]; then
      sed -i \
        '/^MESSAGING_CWD=/d;/^TERMINAL_CWD=/d;/^PATH=/d;/^HERMES_PY=/d;/^HERMES_PYTHON=/d;/^AGENT_BROWSER_EXECUTABLE_PATH=/d' \
        "$env_file" 2>/dev/null || true
      chown ${agent.user}:${agent.group} "$env_file" 2>/dev/null || true
      chmod 640 "$env_file" 2>/dev/null || true
    fi
  '';

  containerProfile = pkgs.writeText "hermes-home-profile" ''
    export NPM_CONFIG_PREFIX="$HOME/.npm-global"
    export PATH="${containerPath}"
  '';

  hostProfile = pkgs.writeText "hermes-host-profile" ''
    if [ -d ${toolboxDir} ]; then
      export PATH="${hostPath}:$PATH"
    fi
  '';

  containerBashrc = pkgs.writeText "hermes-home-bashrc" ''
    [ -f "$HOME/.profile" ] && . "$HOME/.profile"
  '';
in
{
  imports = [ ./enable.nix ];

  options.services.hermesPnP.toolbox = {
    enable = mkOption {
      type = types.bool;
      default = true;
      description = ''
        Opinionated everyday CLI buildEnv (the "sauce"): a curated
        ~40-package toolkit + python3 + login PATH. Browser-specific
        aliases live in the browser module. Set false for a bare agent.
      '';
    };

    extraPackages = mkOption {
      type = types.listOf types.package;
      default = [ ];
      description = "Append-only packages added to the toolbox set.";
    };

    pythonPackages = mkOption {
      type = types.functionTo (types.listOf types.package);
      default = ps: with ps; [
        requests
        pyyaml
        toml
      ];
      defaultText = literalExpression "ps: with ps; [ requests pyyaml toml ]";
      description = "Python packages baked into the toolbox python3/python.";
    };

    # Read-only paths computed by this module; host modules may reference
    # these instead of re-deriving PATH.
    toolboxDir = mkOption { type = types.str; readOnly = true; };
    containerToolboxDir = mkOption { type = types.str; readOnly = true; };
    hostPath = mkOption { type = types.str; readOnly = true; };
    containerPath = mkOption { type = types.str; readOnly = true; };
  };

  config = mkIf (pnp.enable && cfg.enable) {
    services.hermesPnP.toolbox = {
      inherit
        toolboxDir
        containerToolboxDir
        hostPath
        containerPath
        ;
    };

    # Host login shells for `sudo -u hermes` / doctor.
    environment.etc."profile.d/hermes-agent-cli.sh" = {
      text = ''
        if [ -d ${toolboxDir} ]; then
          export PATH="${hostPath}:$PATH"
        fi
      '';
      mode = "0644";
    };

    # Login-shell snapshots (WebUI terminal uses bash -l) only reliably see
    # /run/current-system/sw/bin — not /var/lib/hermes/toolbox/bin.
    # pythonBins guarantees both `python` and `python3` on that path.
    environment.systemPackages = [ pythonBins ];

    services.hermes-agent = {
      # Do NOT put PATH / HERMES_PY / AGENT_BROWSER in `environment` — the
      # module merges that into $HERMES_HOME/.env, which host `hermes chat`
      # loads and which breaks host terminal (container /data/toolbox paths).
      environment = { };

      container.extraOptions = mkIf agent.container.enable (
        mkDockerEnv containerProcessEnv
      );
    };

    system.activationScripts.hermes-toolbox = lib.stringAfter [ "hermes-agent-setup" ] ''
      install -d -m 0755 -o ${agent.user} -g ${agent.group} ${stateDir}/toolbox
      ln -sfn ${hermesToolbox}/bin ${toolboxDir}

      install -d -m 0750 -o ${agent.user} -g ${agent.group} ${home}
      install -d -m 0750 -o ${agent.user} -g ${agent.group} ${home}/.npm-global
      install -d -m 0755 -o ${agent.user} -g ${agent.group} ${home}/.local/bin

      # Interactive / docker-exec shells inside the container.
      install -m 0644 -o ${agent.user} -g ${agent.group} ${containerProfile} ${home}/.profile
      install -m 0644 -o ${agent.user} -g ${agent.group} ${containerBashrc} ${home}/.bashrc
      # Host hermes user HOME (${stateDir}) — WebUI bash -l snapshot.
      install -m 0644 -o ${agent.user} -g ${agent.group} ${hostProfile} ${stateDir}/.profile

      install -d -m 2770 -o ${agent.user} -g ${agent.group} ${skillsDir}
      install -d -m 2770 -o ${agent.user} -g ${agent.group} ${pluginsDir}
    '';

    # Run after setup merges environmentFiles into .env so we can strip PATH again.
    system.activationScripts.hermes-toolbox-dotenv = lib.stringAfter [
      "hermes-agent-setup"
      "hermes-toolbox"
    ] ''
      ${dotenvSanitize}
    '';
  };
}
