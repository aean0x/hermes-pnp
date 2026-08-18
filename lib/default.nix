# Shared composer helpers. `pkgs` is required for mkOciJail (scripts);
# omit it to get only mkDockerEnv (flake.lib).
{ lib, pkgs ? null }:

(import ./env.nix { inherit lib; })
// lib.optionalAttrs (pkgs != null) (import ./oci-container.nix { inherit pkgs lib; })
