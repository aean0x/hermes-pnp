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

      overlay = final: _prev: {
        mcp-proxy = final.callPackage ./pkgs/mcp-proxy { };
        agent-browser = final.callPackage ./pkgs/agent-browser.nix { };
      };

      composer = {
        imports = [
          hermes-agent.nixosModules.default
          hermes-webui.nixosModules.default
          ./modules
        ];
        services.hermesPnP.internal.officialAgentPackageFor =
          system: hermes-agent.packages.${system}.default;
      };
    in
    {
      lib = {
        inherit (import ./lib/env.nix { inherit lib; }) mkDockerEnv;
        forPkgs = pkgs: import ./lib { inherit pkgs lib; };
      };

      plugins = import ./plugins/catalog.nix;
      skills = import ./skills/catalog.nix;

      nixosModules.default = composer;
      nixosModules.hermesPnP = composer;
      nixosModules.agent = hermes-agent.nixosModules.default;
      nixosModules.webui = hermes-webui.nixosModules.default;
      nixosModules.plugins = ./modules/plugins.nix;
      nixosModules.mcp-proxy = ./modules/mcp-proxy.nix;
      nixosModules.skills = ./modules/skills.nix;
      nixosModules.toolbox = ./modules/toolbox.nix;
      nixosModules.browser = ./modules/browser;

      overlays.default = overlay;

      packages = forAllSystems (
        system:
        let
          pkgs = pkgsFor system;
        in
        {
          mcp-proxy = pkgs.callPackage ./pkgs/mcp-proxy { };
          agent-browser = pkgs.callPackage ./pkgs/agent-browser.nix { };
        }
      );

      checks = forAllSystems (
        system:
        import ./checks {
          inherit self nixpkgs system;
          pkgs = pkgsFor system;
        }
      );

      templates.default = {
        path = ./templates/default;
        description = "NixOS host using hermes-pnp composer";
      };

      formatter = forAllSystems (system: (pkgsFor system).nixfmt-rfc-style);

      devShells = forAllSystems (
        system:
        let
          pkgs = pkgsFor system;
        in
        {
          default = pkgs.mkShell {
            packages = [ pkgs.python3 ];
            shellHook = ''
              export PYTHONPATH=${toString ./pkgs/mcp-proxy/src}:''${PYTHONPATH:-}
              echo "Hermes PnP"
              echo "  mcp-proxy:  python3 -m mcp_proxy --config …"
              echo "  tests:      nix flake check"
            '';
          };
        }
      );
    };
}
