# Shared composer helpers. `pkgs` is required for mkOciJail (scripts);
# omit it to get mkDockerEnv / remapStatePath / hardenHost (flake.lib).
{ lib, pkgs ? null }:

(import ./env.nix { inherit lib; })
// (import ./harden-host.nix)
// lib.optionalAttrs (pkgs != null) (import ./oci-container.nix { inherit pkgs lib; })
