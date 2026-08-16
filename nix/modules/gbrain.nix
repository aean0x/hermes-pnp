# Thin optional GBrain hook. No systemd unit, no PGLite, no sources.
# Enabling first-party gbrain-* plugins does not require this.
{
  config,
  lib,
  ...
}:

let
  inherit (lib)
    mkDefault
    mkIf
    optionalAttrs
    ;

  cfg = config.services.hermesPnP.gbrain;
in
{
  config = mkIf cfg.enable {
    # Official typed option; the agent module merges this into
    # settings.mcp_servers. mkDefault inside settings would be stored
    # as a literal (_type = override) because settings is deepConfigType.
    services.hermes-agent.mcpServers.gbrain.url = mkDefault cfg.url;

    services.hermes-agent.environment = {
      GBRAIN_MCP_URL = mkDefault cfg.url;
    }
    // optionalAttrs (cfg.tokenFile != null) {
      GBRAIN_TOKEN_FILE = mkDefault cfg.tokenFile;
    };
  };
}
