# Harden the official hermes-webui unit. Do not replace it.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  pnp = config.services.hermesPnP;
  pairing = pnp.enable && pnp.webui.enable;
  wctr = pnp.webui.container;
  oci = import ../_lib.nix { inherit pkgs lib; };
in
{
  config = lib.mkIf (pairing && !wctr.enable) {
    systemd.services.hermes-webui = {
      after = [ "hermes-agent.service" ];
      wants = [ "hermes-agent.service" ];
      serviceConfig = oci.hardenHost;
    };
  };
}
