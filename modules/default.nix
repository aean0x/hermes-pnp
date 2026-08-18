# Hermes PnP composer. Official agent/webui modules are imported from
# flake.nix (nixosModules.default) so this file stays a plain module.
{ ... }:
{
  imports = [
    ./enable.nix
    ./package.nix
    ./agent.nix
    ./models.nix
    ./plugins.nix
    ./skills.nix
    ./toolbox.nix
    ./gbrain.nix
    ./hmc.nix
    ./webui
    ./browser
    ./mcp-proxy.nix
  ];
}
