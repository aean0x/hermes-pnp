{ self, nixpkgs, system, pkgs }:

let
  evalChecks = import ./eval.nix { inherit self nixpkgs system pkgs; };
in
{
  mcp-proxy = import ./mcp-proxy.nix { inherit pkgs; };
  plugins = import ./plugins.nix { inherit pkgs; };
}
// evalChecks
