# Official container.extraOptions form. Same map as environment{} —
# container entrypoints do not exec the upstream hermes wrapper.
{ lib }:

{
  mkDockerEnv =
    attrs:
    lib.flatten (
      lib.mapAttrsToList (k: v: [
        "--env"
        "${k}=${v}"
      ]) attrs
    );
}
