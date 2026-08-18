# Seed official hermes-agent.settings from hermesPnP.models.
# Composer on only. settings is deepConfigType — do not wrap leaves
# in mkDefault (those become literals in the YAML). Last writer wins
# via recursiveUpdate; put consumer settings after the PnP import.
{ config
, lib
, ...
}:

let
  inherit (lib)
    genAttrs
    mkIf
    mkOption
    types
    ;

  pnp = config.services.hermesPnP;
  inherit (pnp) models;

  mkModelFields = defaults: {
    provider = mkOption {
      type = types.str;
      default = defaults.provider;
      description = "Provider id (official settings / plugin).";
    };
    model = mkOption {
      type = types.str;
      default = defaults.model;
      description = "Model id (official settings / plugin).";
    };
  };

  mkNamedModel =
    { provider
    , model
    , description
    ,
    }:
    mkOption {
      type = types.submodule { options = mkModelFields { inherit provider model; }; };
      default = { };
      inherit description;
      example = { inherit provider model; };
    };

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
  options.services.hermesPnP.models = {
    low = mkNamedModel {
      provider = "deepseek";
      model = "deepseek-v4-flash";
      description = "Cheap helper. Seeds mechanical auxiliary slots + unpinned cron.";
    };

    medium = mkNamedModel {
      provider = "deepseek";
      model = "deepseek-v4-pro";
      description = "Workhorse. Seeds delegation + reasoning auxiliary slots (background_review, curator, kanban_decomposer).";
    };

    high = mkNamedModel {
      provider = "xai-oauth";
      model = "grok-4.6";
      description = "Session identity + voice. Seeds model.default, fallback, rest.";
    };
  };

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
