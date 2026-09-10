{ self, nixpkgs, system, pkgs, hermes-agent }:

let
  evalChecks = import ./eval.nix { inherit self nixpkgs system pkgs; };
  hmChecks = import ./home-manager.nix {
    inherit
      self
      nixpkgs
      system
      pkgs
      hermes-agent
      ;
  };
in
{
  mcp-proxy = import ./mcp-proxy.nix { inherit pkgs; };
  plugins = import ./plugins.nix { inherit pkgs; };
  python-extras-filter = import ./python-extras.nix { inherit pkgs; };
}
// evalChecks
// hmChecks
