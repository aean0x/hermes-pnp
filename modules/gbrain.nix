# Optional loopback `gbrain serve`. Off by default.
# Plugins work without this hook and no-op if GBRAIN_MCP_URL is unset.
{
  config,
  lib,
  ...
}:

let
  inherit (lib)
    mkDefault
    mkEnableOption
    mkIf
    mkOption
    types
    ;

  pnp = config.services.hermesPnP;
  cfg = pnp.gbrain;
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

  config = mkIf cfg.enable {
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
  };
}
