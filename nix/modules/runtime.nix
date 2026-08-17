# extraBindMounts + runtime.mode.
# upstream: official container.extraVolumes. s6 is a stub, not a half-port.
{
  config,
  lib,
  ...
}:

let
  inherit (lib) mkIf;

  inherit (import ../lib.nix { inherit lib; }) bindMountToVolume;

  pnp = config.services.hermesPnP;
in
{
  config = mkIf pnp.enable {
    # throw is not safe here: mkIf content is inspected during merge.
    assertions = [
      {
        assertion = pnp.runtime.mode != "s6";
        message = "s6 runtime not implemented";
      }
    ];

    services.hermes-agent.container.extraVolumes = map bindMountToVolume pnp.runtime.extraBindMounts;
  };
}
