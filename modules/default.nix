# Composer module list. Official agent/webui modules are imported in flake.nix.
{ ... }:
{
  imports = [
    ./enable.nix
    ./package.nix
    ./agent.nix
    ./git.nix
    ./models.nix
    ./plugins.nix
    ./skills.nix
    ./toolbox.nix
    ./gbrain.nix
    ./hmc.nix
    ./webui
    ./browser
    ./admin.nix
    ./mcp-proxy.nix
  ];
}
