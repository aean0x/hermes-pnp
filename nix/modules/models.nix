# Seed official hermes-agent.settings from hermesPnP.models.
# Composer on only. settings is deepConfigType — do not wrap leaves
# in mkDefault (those become literals in the YAML). Last writer wins
# via recursiveUpdate; put consumer settings after the PnP import.
{ config
, lib
, ...
}:

let
  inherit (lib) genAttrs mkIf;

  pnp = config.services.hermesPnP;
  inherit (pnp) models;

  lowSlot = {
    inherit (models.low) provider model;
    reasoning_effort = "none";
  };

  # Official DEFAULT_CONFIG names. Not STT / TTS / vision / moa / goal_judge.
  auxiliarySlots = [
    "title_generation"
    "compression"
    "approval"
    "web_extract"
    "skills_hub"
    "mcp"
    "triage_specifier"
    "kanban_decomposer"
    "profile_describer"
    "curator"
    "background_review"
    "monitor"
    "memory_query_rewrite"
  ];
in
{
  config = mkIf pnp.enable {
    services.hermes-agent.settings = {
      model = {
        provider = models.high.provider;
        default = models.high.model;
      };
      fallback_model = {
        provider = models.high.provider;
        model = models.high.model;
      };
      delegation = {
        provider = models.medium.provider;
        model = models.medium.model;
      };
      cron = {
        model = models.low.model;
        model_provider = models.low.provider;
      };
      auxiliary = genAttrs auxiliarySlots (_: lowSlot);
    };
  };
}
