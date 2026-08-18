# Optional GBrain HTTP MCP. Off by default (mkEnableOption).
# Starts loopback `gbrain serve` when enabled. Does not ship PGLite,
# sources, a memory registry, or config.yaml mutation — those stay
# with the consumer / bootstrap.
#
# Enabling first-party gbrain-* plugins does not require this hook;
# the plugins no-op if GBRAIN_MCP_URL is unset.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  inherit (lib)
    mkDefault
    mkEnableOption
    mkIf
    mkOption
    optionalAttrs
    types
    ;

  inherit (import ../lib { inherit pkgs lib; }) mkDockerEnv remapStatePath;

  pnp = config.services.hermesPnP;
  cfg = pnp.gbrain;
  agent = config.services.hermes-agent;

  containerTokenFile = remapStatePath {
    inherit (agent) stateDir;
    path = cfg.tokenFile;
  };
  home = "${agent.stateDir}/home";
  hostPath =
    if pnp.enable && pnp.toolbox.enable then
      pnp.toolbox.hostPath
    else
      "${home}/.bun/bin:${home}/.local/bin:/run/current-system/sw/bin:/usr/bin:/bin";
  gbrainBin = "${home}/.bun/bin/gbrain";

  gbrainHttpScript = pkgs.writeShellScript "gbrain-mcp-http" ''
    set -euo pipefail
    export HOME="${home}"
    export PATH="${hostPath}"
    if [ ! -x "${gbrainBin}" ] && ! command -v gbrain >/dev/null 2>&1; then
      echo "gbrain-mcp-http: gbrain not installed under ${home}/.bun/bin (bootstrap first)" >&2
      exit 1
    fi
    cd "$HOME"
    exec gbrain serve --http --port ${toString cfg.port} --bind ${cfg.bind}
  '';
in
{
  options.services.hermesPnP.gbrain = {
    enable = mkEnableOption ''
      GBrain HTTP MCP: start gbrain-mcp-http (loopback serve), mkDefault
      mcpServers.gbrain.url, and export GBRAIN_MCP_URL / GBRAIN_TOKEN_FILE.
      Also installs the two gbrain plugins even if they are not listed.
      Off by default. Does not ship PGLite, sources, or a memory registry.
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
      description = "Port gbrain serve listens on.";
    };

    tokenFile = mkOption {
      type = types.nullOr types.str;
      default = "${agent.stateDir}/home/.gbrain/hermes-mcp.token";
      defaultText = lib.literalExpression ''"''${config.services.hermes-agent.stateDir}/home/.gbrain/hermes-mcp.token"'';
      description = "Path to a token file. Injected as GBRAIN_TOKEN_FILE; never read into Nix.";
    };
  };

  config = mkIf cfg.enable {
    # Official typed option; the agent module merges this into
    # settings.mcp_servers. mkDefault inside settings would be stored
    # as a literal (_type = override) because settings is deepConfigType.
    services.hermes-agent.mcpServers.gbrain = {
      url = mkDefault cfg.url;
      connect_timeout = mkDefault 120;
      timeout = mkDefault 120;
    };

    services.hermes-agent.environment = {
      GBRAIN_MCP_URL = mkDefault cfg.url;
    }
    // optionalAttrs (cfg.tokenFile != null && !agent.container.enable) {
      GBRAIN_TOKEN_FILE = mkDefault cfg.tokenFile;
    };

    services.hermes-agent.container.extraOptions = mkIf (
      agent.container.enable && cfg.tokenFile != null
    ) (mkDockerEnv { GBRAIN_TOKEN_FILE = containerTokenFile; });

    systemd.services.gbrain-mcp-http = {
      description = "GBrain MCP HTTP (loopback; sole PGLite writer)";
      after = [
        "network-online.target"
        "hermes-agent-setup.service"
      ];
      wants = [ "network-online.target" ];
      wantedBy = [ "multi-user.target" ];
      # systemd only honours StartLimit* under [Unit], not [Service]
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
          "HOME=${home}"
          "PATH=${hostPath}"
        ];
        WorkingDirectory = home;
        ExecStart = "${gbrainHttpScript}";
        Restart = "on-failure";
        RestartSec = 10;
        TimeoutStartSec = "120";
      };
    };

    systemd.services.hermes-agent = {
      after = [ "gbrain-mcp-http.service" ];
      wants = [ "gbrain-mcp-http.service" ];
    };
    systemd.services.hermes-webui = mkIf pnp.webui.enable {
      after = [ "gbrain-mcp-http.service" ];
      wants = [ "gbrain-mcp-http.service" ];
    };

    # HOME layout the serve process expects. No registry, no MEMORY.md,
    # no config.yaml rewrite.
    system.activationScripts.hermes-gbrain = lib.stringAfter [ "hermes-agent-setup" ] ''
      install -d -m 0750 -o ${agent.user} -g ${agent.group} ${home}
      install -d -m 0750 -o ${agent.user} -g ${agent.group} ${home}/.gbrain
      install -d -m 0750 -o ${agent.user} -g ${agent.group} ${home}/brain
      if [ ! -e /home/hermes ]; then
        ln -sfn ${home} /home/hermes
      elif [ ! -L /home/hermes ] && [ ! -d /home/hermes ]; then
        ln -sfn ${home} /home/hermes
      fi
    '';
  };
}
