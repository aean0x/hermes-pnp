# Home Manager gbrain-mcp-http user unit + token inject.
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
  userHome = config.home.homeDirectory;
  hermesHome = agent.hermesHome;
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
  tokenInject = ''
    install -d -m 0750 "${userHome}"
    install -d -m 0750 "${userHome}/.gbrain"
    install -d -m 0750 "${userHome}/brain"
    tokenFile=${userHome}/.gbrain/hermes-mcp.token
    envFile=${hermesHome}/.env
    if [ -s "$tokenFile" ] && [ -f "$envFile" ]; then
      if ! ${pkgs.gnugrep}/bin/grep -q '^GBRAIN_TOKEN=' "$envFile"; then
        ${pkgs.coreutils}/bin/printf 'GBRAIN_TOKEN=%s\n' "$(${pkgs.coreutils}/bin/tr -d '\r\n' < "$tokenFile")" >> "$envFile"
      fi
    fi
  '';
in
{
  config = lib.mkIf cfg.enable {
    systemd.user.services.gbrain-mcp-http = {
      Unit = {
        Description = "GBrain MCP HTTP (loopback; sole PGLite writer)";
        After = [ "default.target" ];
      };
      Service = {
        Type = "simple";
        EnvironmentFile = map (path: "-${toString path}") agent.environmentFiles;
        Environment =
          [
            "HOME=${userHome}"
            "PATH=${hostPath}"
          ]
          ++ lib.optional (cfg.model != null) "GBRAIN_MODEL=${cfg.model}";
        WorkingDirectory = userHome;
        ExecStart = "${gbrainHttpScript}";
        Restart = "on-failure";
        RestartSec = "10";
        TimeoutStartSec = "120";
      };
      Install.WantedBy = [ "default.target" ];
    };

    systemd.user.services.hermes-agent = {
      Unit = {
        After = [ "gbrain-mcp-http.service" ];
        Wants = [ "gbrain-mcp-http.service" ];
      };
    };

    home.activation.hermesGbrain = lib.hm.dag.entryAfter [ "writeBoundary" ] tokenInject;
  };
}
