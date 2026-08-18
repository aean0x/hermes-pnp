# Official hermes-agent pairing: plugin dest follows the agent identity.
# First-party plugins are not installed via services.hermes-agent.extraPlugins
# (that copies into $HERMES_HOME and fights the container /data remap).
{ config
, lib
, ...
}:

let
  inherit (lib) mkDefault mkIf;

  pnp = config.services.hermesPnP;
  agent = config.services.hermes-agent;
in
{
  config = lib.mkMerge [
    (mkIf pnp.enable {
      services.hermes-agent.environmentFiles = pnp.environmentFiles;
      services.hermesPnP.pluginInstall.stateDir = mkDefault agent.stateDir;
      services.hermesPnP.pluginInstall.user = mkDefault agent.user;
      services.hermesPnP.pluginInstall.group = mkDefault agent.group;
    })
    (mkIf pnp.container.enable {
      services.hermes-agent.container.enable = mkDefault true;
      services.hermes-agent.container.backend = mkDefault pnp.container.backend;
      services.hermes-agent.container.image = mkDefault pnp.container.image;
    })
  ];
}
