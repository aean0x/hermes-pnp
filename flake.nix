{
  description = "Hermes PnP — opinionated NixOS composer and Home Manager module for Hermes Agent";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    hermes-agent.url = "github:NousResearch/hermes-agent";
    hermes-webui = {
      url = "github:nesquena/hermes-webui";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # The first-party plugins that carry a catalog entry upstream reach a host
    # through `hermes plugins install <name>`, so they are not pinned here:
    # no input, no materialize step, one pin (the catalog entry). model-picker
    # is not listed upstream and still arrives as a pinned source tree.
    hermes-model-picker = {
      url = "github:aean0x/hermes-model-picker";
      flake = false;
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      hermes-agent,
      hermes-webui,
      hermes-model-picker,
    }:
    let
      inherit (nixpkgs) lib;
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = lib.genAttrs systems;
      pkgsFor = system: nixpkgs.legacyPackages.${system};

      # Plugin name → source tree for plugins the upstream catalog does not
      # carry. Keys are plugin names (what services.hermesPnP.plugins takes).
      externalPlugins = {
        model-picker = hermes-model-picker;
      };

      overlay = final: _prev: {
        mcp-proxy = final.callPackage ./pkgs/mcp-proxy { };
        agent-infra-browser-ui = final.callPackage ./pkgs/agent-infra-browser-ui.nix { };
      };

      composer = {
        imports = [
          hermes-agent.nixosModules.default
          hermes-webui.nixosModules.default
          ./modules
        ];
        services.hermesPnP.internal.officialAgentPackageFor =
          system: hermes-agent.packages.${system}.default;
        services.hermesPnP.internal.officialAgentSrc = hermes-agent.outPath;
        services.hermesPnP.internal.officialDesktopPackageFor =
          system: hermes-agent.packages.${system}.desktop;
        # Same Python instance as hermesVenv (hermes-agent.nix python312).
        # Host/composer nixpkgs is a different interpreter and is stripped.
        services.hermesPnP.internal.officialPythonPackagesFor =
          system: hermes-agent.inputs.nixpkgs.legacyPackages.${system}.python312Packages;
        services.hermesPnP.internal.pluginSources = externalPlugins;
      };

      homeComposer = {
        imports = [
          hermes-agent.homeManagerModules.default
          ./modules/hm
        ];
        services.hermesPnP.internal.officialAgentPackageFor =
          system: hermes-agent.packages.${system}.default;
        services.hermesPnP.internal.officialAgentSrc = hermes-agent.outPath;
        services.hermesPnP.internal.officialPythonPackagesFor =
          system: hermes-agent.inputs.nixpkgs.legacyPackages.${system}.python312Packages;
        services.hermesPnP.internal.pluginSources = externalPlugins;
      };
    in
    {
      lib = {
        inherit (import ./lib/env.nix { inherit lib; }) mkDockerEnv remapStatePath agentContainerNetwork;
        forPkgs = pkgs: import ./lib { inherit pkgs lib; };
      };

      plugins = import ./plugins/catalog.nix // externalPlugins;
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
      nixosModules.desktop = ./modules/desktop.nix;

      homeManagerModules.default = homeComposer;
      homeManagerModules.hermesPnP = homeComposer;

      overlays.default = overlay;

      packages = forAllSystems (
        system:
        let
          pkgs = pkgsFor system;
        in
        {
          mcp-proxy = pkgs.callPackage ./pkgs/mcp-proxy { };
          agent-infra-browser-ui = pkgs.callPackage ./pkgs/agent-infra-browser-ui.nix { };
        }
      );

      checks = forAllSystems (
        system:
        import ./checks {
          inherit self nixpkgs system hermes-agent;
          pkgs = pkgsFor system;
          pluginSources = externalPlugins;
        }
      );

      formatter = forAllSystems (system: (pkgsFor system).nixfmt-rfc-style);

      devShells = forAllSystems (
        system:
        let
          pkgs = pkgsFor system;
        in
        {
          default = pkgs.mkShell {
            packages = [
              pkgs.python3
              pkgs.ruff
              pkgs.oxlint
              pkgs.statix
            ];
            shellHook = ''
              export PYTHONPATH=${toString ./pkgs/mcp-proxy/src}:''${PYTHONPATH:-}
              echo "Hermes PnP"
              echo "  mcp-proxy:  python3 -m mcp_proxy --config …"
              echo "  tests:      nix flake check"
              echo "  lint:       ruff check && oxlint && statix check ."
            '';
          };
        }
      );
    };
}
