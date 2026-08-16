# Hermes PnP composer. Today: MCP proxy + plugin installer.
# Later services (gbrain HTTP, toolbox, …) import beside these.
{
  imports = [
    ../../services/mcp-proxy/nix/module.nix
    ./plugins.nix
  ];
}
