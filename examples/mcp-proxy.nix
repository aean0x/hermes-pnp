# Loopback MCP reverse proxy. Composer does not auto-enable this.
# Clients call 127.0.0.1; this process injects secrets and applies
# filters. Point official mcpServers at the proxy paths.
#
# Import: inputs.hermes-pnp.nixosModules.mcp-proxy
# (or nixosModules.default)
{
  services.hermes-agent.enable = true;

  services.hermesPnP.mcpProxy = {
    enable = true;
    listenAddress = "127.0.0.1";
    listenPort = 3140;

    # OAuth stays in the MCP client; proxy only filters.
    backends.github = {
      upstream = "https://api.githubcopilot.com/mcp/";
      auth.mode = "passthrough";
      tools.deny = [ ];
    };

    # Host-held key. Prefer sops — do not put tokens in Nix.
    # backends.composio = {
    #   upstream = "https://backend.composio.dev/mcp";
    #   secrets.Authorization = {
    #     file = config.sops.secrets.composio_api_key.path;
    #     prefix = "Bearer ";
    #   };
    #   tools.allow = [ "COMPOSIO_MULTI_EXECUTE_TOOL" ];
    #   tools.deny = [ "GMAIL_SEND_EMAIL" ];
    # };

    backends.docs = {
      upstream = "https://example.invalid/mcp";
      auth.mode = "passthrough";
      tools.deny = [ "write_file" ];
    };
  };

  services.hermes-agent.mcpServers = {
    github.url = "http://127.0.0.1:3140/github";
    docs.url = "http://127.0.0.1:3140/docs";
  };
}
