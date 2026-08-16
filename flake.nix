{
  description = "Hermes PnP (Plug n Pray) — reusable Hermes services and plugins";

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
      catalog = import ./nix/catalog.nix;
    in
    {
      plugins = catalog;

      nixosModules.default = import ./nix/modules/default.nix;
      nixosModules.mcp-proxy = import ./services/mcp-proxy/nix/module.nix;
      nixosModules.plugins = import ./nix/modules/plugins.nix;

      overlays.default = final: _prev: {
        mcp-proxy = final.callPackage ./services/mcp-proxy/nix/package.nix { };
      };

      packages = forAllSystems (
        system:
        let
          pkgs = pkgsFor system;
        in
        rec {
          mcp-proxy = pkgs.callPackage ./services/mcp-proxy/nix/package.nix { };
          default = mcp-proxy;
        }
      );

      checks = forAllSystems (
        system:
        let
          pkgs = pkgsFor system;
        in
        {
          mcp-proxy-tests = pkgs.runCommand "mcp-proxy-tests" { nativeBuildInputs = [ pkgs.python3 ]; } ''
            export PYTHONPATH=${./services/mcp-proxy/src}
            python3 -m unittest discover -s ${./services/mcp-proxy/tests} -v
            touch $out
          '';
          plugin-tests = pkgs.runCommand "hermes-pnp-plugin-tests" {
            nativeBuildInputs = [ pkgs.python3 ];
          } ''
            ( cd ${./plugins/secret-handoff} && PYTHONPATH=. python3 -m unittest discover -s tests -v )
            ( cd ${./plugins/model-router} && PYTHONPATH=. python3 -m unittest discover -s tests -v )
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
              export PYTHONPATH=${toString ./services/mcp-proxy/src}:''${PYTHONPATH:-}
              echo "Hermes PnP"
              echo "  mcp-proxy:  python3 -m mcp_proxy --config …"
              echo "  tests:      nix flake check"
            '';
          };
        }
      );
    };
}
