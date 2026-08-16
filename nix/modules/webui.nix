# Official hermes-webui + pairing defaults. mkDefault only.
# Gated on hermesPnP.enable && hermesPnP.webui.enable.
{
  config,
  lib,
  ...
}:

let
  inherit (lib) mkDefault mkIf;

  pnp = config.services.hermesPnP;
  agent = config.services.hermes-agent;
  pairing = pnp.enable && pnp.webui.enable;
in
{
  config = mkIf pairing {
    services.hermes-webui = {
      enable = mkDefault true;
      user = mkDefault agent.user;
      group = mkDefault agent.group;
      agent.package = mkDefault agent.package;
      hermesHome = mkDefault "${agent.stateDir}/.hermes";
      host = mkDefault "127.0.0.1";
      port = mkDefault 8787;
      environmentFiles = mkDefault agent.environmentFiles;
    };

    systemd.services.hermes-webui = {
      after = [ "hermes-agent.service" ];
      wants = [ "hermes-agent.service" ];
    };
  };
}
