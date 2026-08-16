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

  # runtime.extraBindMounts is a list of host paths. Pass through if
  # the consumer already wrote host:container:mode.
  bindMountToVolume =
    p: if lib.hasInfix ":" p then p else "${p}:${p}:rw";
}
