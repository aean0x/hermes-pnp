{
  self,
  nixpkgs,
  system,
  pkgs,
  hermes-agent,
  pluginSources,
}:

let
  evalChecks = import ./eval.nix {
    inherit
      self
      nixpkgs
      system
      pkgs
      pluginSources
      ;
  };
  hmChecks = import ./home-manager.nix {
    inherit
      self
      nixpkgs
      system
      pkgs
      hermes-agent
      pluginSources
      ;
  };
in
{
  mcp-proxy = import ./mcp-proxy.nix { inherit pkgs; };
  plugins = import ./plugins.nix { inherit pkgs pluginSources; };
  python-extras-filter = import ./python-extras.nix { inherit pkgs; };
}
// evalChecks
// hmChecks
