# Native composer. One enable pairs agent + WebUI + toolbox + browser
# and seeds models / default plugins. Site identity stays in the consumer.
#
# Import: inputs.hermes-pnp.nixosModules.default
{
  services.hermes-agent.enable = true;

  services.hermesPnP = {
    enable = true;
    # environmentFiles = [ config.sops.templates.hermesEnv.path ];

    models.low = {
      provider = "deepseek";
      model = "deepseek-v4-flash";
    };
    models.medium = {
      provider = "deepseek";
      model = "deepseek-v4-pro";
    };
    models.high = {
      provider = "xai-oauth";
      model = "grok-4.6";
    };
    # model.default = "medium"; # tier seeding settings.model.default: low | medium | high
    # models.auxiliary = { provider = "deepseek"; model = "deepseek-v4-flash"; };
    # models.high.reasoning_effort = "high"; # else Hermes session default
    # models.low.best_for = [ "Short acknowledgements" ];
    # workspace = "/var/lib/hermes"; # host path; remaps to /data in the jail

    plugins = [
      "model-router"
      "tool-call-coherency"
      "secret-handoff"
    ];

    # webui.enable = true;
    # toolbox.enable = true;
    # browser.enable = true;
  };
}
