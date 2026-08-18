# Pair plugin dest with the official agent identity.
# First-party plugins materialize to $stateDir/plugins/<name>.
# services.hermes-agent.extraPlugins remains the official listOf package path.
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
