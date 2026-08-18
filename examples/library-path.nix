# Library path: composer off, plugins still install.
{
  services.hermes-agent.enable = true;
  services.hermes-agent.settings.model.default = "xai/grok-4";
  services.hermesPnP.enable = false;
  services.hermesPnP.plugins = [ "model-router" ];
}
