# Optional loopback `gbrain serve`. Off by default.
# Plugins work without this hook and no-op if GBRAIN_MCP_URL is unset.
{
  config,
  lib,
  pkgs,
  options,
  ...
}:

let
  inherit (lib)
    mkDefault
    mkEnableOption
    mkIf
    mkMerge
    mkOption
    types
    ;

  pnp = config.services.hermesPnP;
  cfg = pnp.gbrain;
  agent = config.services.hermes-agent;
  isHomeManager = options ? home && options.home ? homeDirectory;
  hasToolbox = options.services.hermesPnP ? toolbox && pnp.enable && pnp.toolbox.enable;
  hasWebui = options.services.hermesPnP ? webui && pnp.webui.enable;

  userHome =
    if isHomeManager then config.home.homeDirectory else "${agent.stateDir}/home";
  hermesHome = if isHomeManager then agent.hermesHome else "${agent.stateDir}/.hermes";

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
  options.services.hermesPnP.gbrain = {
    enable = mkEnableOption ''
      GBrain HTTP MCP: start gbrain-mcp-http (loopback serve), mkDefault
      mcpServers.gbrain.url plus an Authorization header (Bearer
      ''${GBRAIN_TOKEN}, expanded by Hermes from .env), and export
      GBRAIN_MCP_URL for the ambient plugin. Also installs the two gbrain
      plugins even if they are not listed.
      Off by default. Does not ship PGLite, sources, or a memory registry.
      CLI install is a one-shot: scripts/gbrain-setup.sh.
    '';

    url = mkOption {
      type = types.str;
      default = "http://127.0.0.1:3131/mcp";
      description = "GBrain HTTP MCP URL advertised to the agent.";
    };

    bind = mkOption {
      type = types.str;
      default = "127.0.0.1";
      description = "Address gbrain serve binds. Keep loopback unless you have a reason.";
    };

    port = mkOption {
      type = types.port;
      default = 3131;
      description = "Port gbrain serve listens on. 3131 is the stock gbrain --http default.";
    };

    model = mkOption {
      type = types.nullOr types.str;
      default = null;
      example = "google:gemini-3.5-flash-lite";
      description = ''
        Chat/expansion model for `gbrain serve` (`GBRAIN_MODEL`).
        Null keeps gbrain's key-aware default. Independent of
        `container.enable` / `desktop.enable`. NixOS: native systemd
        User=hermes. Home Manager: user unit, HOME is the login home.
        Embeddings stay `embedding_model` in `~/.gbrain/config.json`
        (gbrain init / scripts/gbrain-setup.sh), not this option.
      '';
    };
  };

  config = mkIf cfg.enable (mkMerge [
    {
      # Typed mcpServers option; official merges it into settings.mcp_servers.
      # The bearer is an env ref: Hermes expands ${GBRAIN_TOKEN} from
      # $HERMES_HOME/.env at runtime (same pattern as mcp-proxy's
      # ${MCP_PROXY_TOKEN}). No literal token, no post-merge rewrite.
      services.hermes-agent.mcpServers.gbrain = {
        url = mkDefault cfg.url;
        headers.Authorization = mkDefault "Bearer \${GBRAIN_TOKEN}";
        connect_timeout = mkDefault 120;
        timeout = mkDefault 120;
      };

      # Ambient plugin reads this for its own HTTP volunteer_context / query.
      services.hermes-agent.environment.GBRAIN_MCP_URL = mkDefault cfg.url;
    }
    (mkIf (!isHomeManager) {
      systemd.services.gbrain-mcp-http = {
        description = "GBrain MCP HTTP (loopback; sole PGLite writer)";
        after = [
          "network-online.target"
        ];
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
      systemd.services.hermes-webui = mkIf hasWebui {
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
    })
    (mkIf isHomeManager {
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
    })
  ]);
}
