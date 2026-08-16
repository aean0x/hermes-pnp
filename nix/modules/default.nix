# Hermes PnP composer. Official agent/webui modules are imported from
# flake.nix (nixosModules.default) so this file stays a plain module.
{
  imports = [
    ./options.nix
    ./package.nix
    ./agent.nix
    ./webui.nix
    ./plugins.nix
    ./toolbox.nix
    ./runtime.nix
    ./gbrain.nix
    ../../services/mcp-proxy/nix/module.nix
  ];
}
