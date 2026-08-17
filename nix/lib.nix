# Shared helpers for the composer modules.
{ lib }:

{
  # Official container.extraOptions form. Same map as environment{} —
  # container entrypoints do not exec the upstream hermes wrapper.
  mkDockerEnv =
    attrs:
    lib.flatten (
      lib.mapAttrsToList (k: v: [
        "--env"
        "${k}=${v}"
      ]) attrs
    );
}
