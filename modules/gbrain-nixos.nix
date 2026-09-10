# NixOS gbrain-mcp-http + token inject. Not imported by Home Manager.
{
  config,
  lib,
  pkgs,
  options,
  ...
}:

let
  pnp = config.services.hermesPnP;
  cfg = pnp.gbrain;
  agent = config.services.hermes-agent;
  hasToolbox = options.services.hermesPnP ? toolbox && pnp.enable && pnp.toolbox.enable;
  hasWebui = options.services.hermesPnP ? webui && pnp.webui.enable;
  userHome = "${agent.stateDir}/home";
  hermesHome = "${agent.stateDir}/.hermes";
  hostPath =
    if hasToolbox then
      pnp.toolbox.hostPath
    else
      "${userHome}/.bun/bin:${userHome}/.local/bin:/run/current-system/sw/bin:/usr/bin:/bin";
  gbrainBin = "${userHome}/.bun/bin/gbrain";
  gbrainHttpScript = pkgs.writeShellScript "gbrain-mcp-http" ''
    set -euo pipefail
    export HOME="${userHome}"
    export PATH="${hostPath}"
    if [ ! -x "${gbrainBin}" ] && ! command -v gbrain >/dev/null 2>&1; then
      echo "gbrain-mcp-http: gbrain not installed under ${userHome}/.bun/bin (bootstrap first)" >&2
      exit 1
    fi
    cd "$HOME"
    exec gbrain serve --http --port ${toString cfg.port} --bind ${cfg.bind}
  '';
in
{
  config = lib.mkIf cfg.enable {
    systemd.services.gbrain-mcp-http = {
      description = "GBrain MCP HTTP (loopback; sole PGLite writer)";
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      wantedBy = [ "multi-user.target" ];
      environment = lib.optionalAttrs (cfg.model != null) {
        GBRAIN_MODEL = cfg.model;
      };
      unitConfig = {
        StartLimitIntervalSec = 120;
        StartLimitBurst = 5;
      };
      serviceConfig = {
        Type = "simple";
        User = agent.user;
        Group = agent.group;
        EnvironmentFile = map (path: "-${toString path}") agent.environmentFiles;
        Environment = [
          "HOME=${userHome}"
          "PATH=${hostPath}"
        ];
        WorkingDirectory = userHome;
        ExecStart = "${gbrainHttpScript}";
        Restart = "on-failure";
        RestartSec = 10;
        TimeoutStartSec = "120";
        NoNewPrivileges = true;
        ProtectKernelTunables = true;
        ProtectKernelModules = true;
        ProtectKernelLogs = true;
        RestrictSUIDSGID = true;
        LockPersonality = true;
      };
    };

    systemd.services.hermes-agent = {
      after = [ "gbrain-mcp-http.service" ];
      wants = [ "gbrain-mcp-http.service" ];
    };
    systemd.services.hermes-webui = lib.mkIf hasWebui {
      after = [ "gbrain-mcp-http.service" ];
      wants = [ "gbrain-mcp-http.service" ];
    };

    system.activationScripts.hermes-gbrain = lib.stringAfter [ "hermes-agent-setup" ] ''
      install -d -m 0750 -o ${agent.user} -g ${agent.group} ${userHome}
      install -d -m 0750 -o ${agent.user} -g ${agent.group} ${userHome}/.gbrain
      install -d -m 0750 -o ${agent.user} -g ${agent.group} ${userHome}/brain
      if [ ! -e /home/hermes ]; then
        ln -sfn ${userHome} /home/hermes
      elif [ ! -L /home/hermes ] && [ ! -d /home/hermes ]; then
        ln -sfn ${userHome} /home/hermes
      fi
      tokenFile=${userHome}/.gbrain/hermes-mcp.token
      envFile=${hermesHome}/.env
      if [ -s "$tokenFile" ] && [ -f "$envFile" ]; then
        if ! ${pkgs.gnugrep}/bin/grep -q '^GBRAIN_TOKEN=' "$envFile"; then
          ${pkgs.coreutils}/bin/printf 'GBRAIN_TOKEN=%s\n' "$(${pkgs.coreutils}/bin/tr -d '\r\n' < "$tokenFile")" >> "$envFile"
          ${pkgs.coreutils}/bin/chown ${agent.user}:${agent.group} "$envFile" || true
        fi
      fi
    '';
  };
}
