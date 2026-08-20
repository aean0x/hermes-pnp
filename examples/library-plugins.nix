# Library path. Composer stays off: no WebUI pairing, no toolbox, no
# browser. First-party plugins still materialize.
#
# Import: inputs.hermes-pnp.nixosModules.plugins
# (or nixosModules.default — plugins work with enable = false)
{
  services.hermes-agent.enable = true;
  services.hermes-agent.settings.model.default = "xai/grok-4";

  services.hermesPnP = {
    enable = false;
    plugins = [
      "model-router"
      "tool-call-coherency"
      "secret-handoff"
    ];
    extraPluginDirs = {
      # my-plugin = ./plugins/my-plugin;
    };
  };
}
