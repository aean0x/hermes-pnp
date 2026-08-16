{
  description = "Hermes PnP — opinionated NixOS composer for Hermes Agent";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    hermes-agent.url = "github:NousResearch/hermes-agent";
    hermes-webui = {
      url = "github:nesquena/hermes-webui";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    { self
    , nixpkgs
    , hermes-agent
    , hermes-webui
    ,
    }:
    let
      inherit (nixpkgs) lib;
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = lib.genAttrs systems;
      pkgsFor = system: nixpkgs.legacyPackages.${system};
      catalog = import ./nix/catalog.nix;

      composer = {
        imports = [
          hermes-agent.nixosModules.default
          hermes-webui.nixosModules.default
          ./nix/modules/default.nix
        ];
        _module.args.hermesPnPFlake = {
          inherit hermes-agent hermes-webui;
        };
      };
    in
    {
      plugins = catalog;

      nixosModules.default = composer;
      nixosModules.agent = hermes-agent.nixosModules.default;
      nixosModules.webui = hermes-webui.nixosModules.default;
      nixosModules.plugins = {
        imports = [
          ./nix/modules/options.nix
          ./nix/modules/plugins.nix
        ];
      };
      nixosModules.mcp-proxy = import ./services/mcp-proxy/nix/module.nix;
      nixosModules.toolbox = {
        imports = [
          ./nix/modules/options.nix
          ./nix/modules/toolbox.nix
        ];
      };
      nixosModules.runtime = {
        imports = [
          ./nix/modules/options.nix
          ./nix/modules/runtime.nix
        ];
      };

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
          evalChecks = import ./nix/checks.nix {
            inherit self nixpkgs system pkgs;
          };
          mcp-proxy = pkgs.runCommand "mcp-proxy-tests" { nativeBuildInputs = [ pkgs.python3 ]; } ''
            export PYTHONPATH=${./services/mcp-proxy/src}
            python3 -m unittest discover -s ${./services/mcp-proxy/tests} -v
            touch $out
          '';
          plugins = pkgs.runCommand "hermes-pnp-plugin-tests"
            {
              nativeBuildInputs = [ pkgs.python3 ];
            } ''
            ( cd ${./plugins/secret-handoff} && PYTHONPATH=. python3 -m unittest discover -s tests -v )
            ( cd ${./plugins/model-router} && PYTHONPATH=. python3 -m unittest discover -s tests -v )
            touch $out
          '';
        in
        {
          inherit mcp-proxy plugins;
          mcp-proxy-tests = mcp-proxy;
          plugin-tests = plugins;
        }
        // evalChecks
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
