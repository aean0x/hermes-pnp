# NixOS-specific toolbox wiring and activation scripts.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  inherit (lib) mkIf;

  pnp = config.services.hermesPnP;
  agent = config.services.hermes-agent;
  cfg = pnp.toolbox;

  inherit (import ../lib { inherit pkgs lib; }) mkDockerEnv containerHome;

  stateDir = agent.stateDir;
  home = "${stateDir}/home";
  hermesHome = "${stateDir}/.hermes";
  toolboxDir = cfg.toolboxDir;
  hostPath = cfg.hostPath;
  containerPath = cfg.containerPath;
  hostVenv = "${home}/.venv";
  skillsDir = "${stateDir}/skills";
  pluginsDir = "${stateDir}/plugins";

  pythonEnv = pkgs.python3.withPackages cfg.pythonPackages;
  pythonBins = pkgs.runCommand "hermes-python-nixos" { } ''
    mkdir -p "$out/bin"
    ln -s ${pythonEnv}/bin/python3 "$out/bin/python3"
    ln -s ${pythonEnv}/bin/python3 "$out/bin/python"
  '';

  containerProcessEnv = {
    PATH = containerPath;
    HERMES_PYTHON = "${containerHome}/.venv/bin/python3";
  };

  dotenvSanitize = pkgs.writeShellScript "hermes-toolbox-dotenv-sanitize" ''
    env_file=${hermesHome}/.env
    if [ -f "$env_file" ]; then
      sed -i '/^PATH=/d;/^HERMES_PYTHON=/d' "$env_file" 2>/dev/null || true
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
  config = mkIf (pnp.enable && cfg.enable) {
    # Host login PATH.
    environment.etc."profile.d/hermes-agent-cli.sh" = {
      text = ''
        if [ -d ${toolboxDir} ]; then
          export PATH="${hostPath}:$PATH"
        fi
      '';
      mode = "0644";
    };

    environment.systemPackages = [ pythonBins ];

    users.users.${agent.user}.packages = mkIf agent.enable [ cfg.hermesToolbox ];
    systemd.services.hermes-agent = mkIf agent.enable {
      path = [ cfg.hermesToolbox ];
    };

    services.hermes-agent = {
      container.extraOptions = mkIf agent.container.enable (mkDockerEnv containerProcessEnv);
    };

    system.activationScripts.hermes-toolbox = lib.stringAfter [ "hermes-agent-setup" ] ''
      install -d -m 0755 -o ${agent.user} -g ${agent.group} ${stateDir}/toolbox
      ln -sfn ${cfg.hermesToolbox}/bin ${toolboxDir}

      install -d -m 0750 -o ${agent.user} -g ${agent.group} ${home}
      install -d -m 0750 -o ${agent.user} -g ${agent.group} ${home}/.npm-global
      install -d -m 0755 -o ${agent.user} -g ${agent.group} ${home}/.local/bin

      venv=${hostVenv}
      py=${pythonEnv}/bin/python3
      current=$(${pkgs.coreutils}/bin/readlink -f "$venv/bin/python3" 2>/dev/null || true)
      wanted=$(${pkgs.coreutils}/bin/readlink -f "$py")
      has_sys_site=$(${pkgs.gnugrep}/bin/grep -c 'include-system-site-packages = true' "$venv/pyvenv.cfg" 2>/dev/null || true)
      if [ "$current" != "$wanted" ] || [ "$has_sys_site" = "0" ]; then
        rm -rf "$venv"
        "$py" -m venv --system-site-packages "$venv"
        chown -R ${agent.user}:${agent.group} "$venv"
      fi

      install -m 0644 -o ${agent.user} -g ${agent.group} ${containerProfile} ${home}/.profile
      install -m 0644 -o ${agent.user} -g ${agent.group} ${containerBashrc} ${home}/.bashrc
      install -m 0644 -o ${agent.user} -g ${agent.group} ${hostProfile} ${stateDir}/.profile

      install -d -m 2770 -o ${agent.user} -g ${agent.group} ${skillsDir}
      install -d -m 2770 -o ${agent.user} -g ${agent.group} ${pluginsDir}
    '';

    system.activationScripts.hermes-toolbox-dotenv =
      lib.stringAfter
        [
          "hermes-agent-setup"
          "hermes-toolbox"
        ]
        ''
          ${dotenvSanitize}
        '';
  };
}
