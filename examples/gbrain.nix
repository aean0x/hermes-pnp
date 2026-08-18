# Loopback GBrain HTTP MCP. Does not ship PGLite, sources, or a memory
# registry. The CLI is a consumer bootstrap (`bun install -g`).
# Enabling this also installs gbrain-retrieval-reflex + gbrain-memory-flush.
#
# Import: inputs.hermes-pnp.nixosModules.default
# Listing the gbrain plugins does not require this hook.
{
  services.hermes-agent.enable = true;

  services.hermesPnP = {
    enable = true;
    gbrain.enable = true;
    # gbrain.url = "http://127.0.0.1:3131/mcp";
    # gbrain.bind = "127.0.0.1";
    # gbrain.port = 3131;
    # gbrain.tokenFile = "${config.services.hermes-agent.stateDir}/home/.gbrain/hermes-mcp.token";
  };
}
