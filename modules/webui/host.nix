# Harden the official hermes-webui unit.
{
  config,
  lib,
  ...
}:

let
  pnp = config.services.hermesPnP;
  pairing = pnp.enable && pnp.webui.enable;
  wctr = pnp.webui.container;
  inherit (import ../../lib { inherit lib; }) hardenHost;
in
{
  config = lib.mkIf (pairing && !wctr.enable) {
    systemd.services.hermes-webui = {
      after = [ "hermes-agent.service" ];
      wants = [ "hermes-agent.service" ];
      serviceConfig = hardenHost;
    };
  };
}
