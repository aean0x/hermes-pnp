{
  description = "NixOS host using hermes-pnp";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    hermes-pnp.url = "github:aean0x/hermes-pnp";
  };

  outputs =
    { nixpkgs, hermes-pnp, ... }:
    {
      nixosConfigurations.example = nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        modules = [
          hermes-pnp.nixosModules.default
          ./configuration.nix
        ];
      };
    };
}
