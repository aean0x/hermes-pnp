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
    ./plugins-nixos.nix
    ./skills.nix
    ./skills-nixos.nix
    ./toolbox.nix
    ./gbrain.nix
    ./gbrain-nixos.nix
    ./hmc.nix
    ./hmc-nixos.nix
    ./webui
    ./browser
    ./admin.nix
    ./desktop.nix
    ./mcp-proxy.nix
  ];
}
