# Hermes PnP composer. Official agent/webui modules are imported from
# flake.nix (nixosModules.default) so this file stays a plain module.
{ ... }:
{
  imports = [
    ./options.nix
    ./package.nix
    ./agent.nix
    ./webui.nix
    ./models.nix
    ./plugins.nix
    ./skills.nix
    ./toolbox.nix
    ../../services/browser/nix/module.nix
    ./gbrain.nix
    ./hmc.nix
    ../../services/mcp-proxy/nix/module.nix
  ];
}
