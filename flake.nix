{
  description = "Declarative MCP reverse proxy: inject secrets, filter tools and arguments";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      inherit (nixpkgs) lib;
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = lib.genAttrs systems;
      pkgsFor = system: nixpkgs.legacyPackages.${system};
    in
    {
      nixosModules.default = import ./nix/module.nix;
      nixosModules.mcp-proxy = self.nixosModules.default;

      overlays.default = final: _prev: {
        mcp-proxy = final.callPackage ./nix/package.nix { };
      };

      packages = forAllSystems (
        system:
        let
          pkgs = pkgsFor system;
        in
        rec {
          mcp-proxy = pkgs.callPackage ./nix/package.nix { };
          default = mcp-proxy;
        }
      );

      checks = forAllSystems (
        system:
        let
          pkgs = pkgsFor system;
        in
        {
          tests = pkgs.runCommand "mcp-proxy-tests" { nativeBuildInputs = [ pkgs.python3 ]; } ''
            export PYTHONPATH=${./src}
            python3 -m unittest discover -s ${./tests} -v
            touch $out
          '';
        }
      );

      devShells = forAllSystems (
        system:
        let
          pkgs = pkgsFor system;
        in
        {
          default = pkgs.mkShell {
            packages = [ pkgs.python3 ];
            shellHook = ''
              export PYTHONPATH=${toString ./src}:''${PYTHONPATH:-}
              echo "mcp-proxy dev: python3 -m mcp_proxy --config …"
              echo "tests: PYTHONPATH=src python3 -m unittest discover -s tests -v"
            '';
          };
        }
      );
    };
}
