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

  mediumSlot = {
    inherit (models.medium) provider model;
    reasoning_effort = "none";
  };

  # Mechanical slots — cheap tier is enough.
  auxiliaryLowSlots = [
    "title_generation"
    "compression"
    "approval"
    "web_extract"
    "skills_hub"
    "mcp"
    "triage_specifier"
    "profile_describer"
    "monitor"
    "memory_query_rewrite"
  ];

  # Reasoning / quality slots — workhorse tier. Not STT / TTS / vision /
  # moa / goal_judge.
  auxiliaryMediumSlots = [
    "background_review"
    "curator"
    "kanban_decomposer"
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
      auxiliary =
        genAttrs auxiliaryLowSlots (_: lowSlot)
        // genAttrs auxiliaryMediumSlots (_: mediumSlot);
    };
  };
}
