# Seed services.hermes-agent.settings from hermesPnP.models.
# settings is deepConfigType: do not wrap leaves in mkDefault.
# Last writer wins; assign consumer settings after the PnP import.
#
# Model-router only switches model/provider. reasoning_effort is Hermes
# session state except for official auxiliary slots, which seed from
# models.auxiliary (default effort "none").
{
  config,
  lib,
  ...
}:

let
  inherit (lib)
    genAttrs
    mkIf
    mkOption
    optionalAttrs
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
    reasoning_effort = mkOption {
      type = types.nullOr types.str;
      default = defaults.reasoning_effort;
      description = ''
        Official reasoning_effort for this named model. null leaves
        Hermes session defaults. Model-router never writes this.
        Auxiliary defaults to "none".
      '';
    };
  };

  mkNamedModel =
    {
      provider,
      model,
      description,
      reasoning_effort ? null,
    }:
    mkOption {
      type = types.submodule {
        options = mkModelFields {
          inherit provider model reasoning_effort;
        };
      };
      default = { };
      inherit description;
      example = { inherit provider model; };
    };

  # Only emit reasoning_effort when the named model set it.
  mkSeed =
    m:
    {
      inherit (m) provider model;
    }
    // optionalAttrs (m.reasoning_effort != null) {
      reasoning_effort = m.reasoning_effort;
    };

  auxiliarySlot = mkSeed models.auxiliary;

  # Official auxiliary tasks. Vision, tts, moa, and goal_judge stay unset.
  auxiliarySlots = [
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
      description = "Cheap helper. OOBE seed for unpinned cron and model-router low.";
    };

    medium = mkNamedModel {
      provider = "deepseek";
      model = "deepseek-v4-pro";
      description = "Workhorse. OOBE seed for delegation and model-router medium.";
    };

    high = mkNamedModel {
      provider = "xai-oauth";
      model = "grok-4.6";
      description = ''
        Session identity + voice. OOBE seed for settings.model.default
        and fallback. Override here — not settings.model.default —
        unless you assign settings after the PnP import (deepConfigType
        last writer wins; mkDefault on a leaf is stored as a literal).
      '';
    };

    auxiliary = mkNamedModel {
      provider = "deepseek";
      model = "deepseek-v4-flash";
      reasoning_effort = "none";
      description = ''
        Official auxiliary tasks (title generation, compression, …).
        Nix-only — not a model-router tier. reasoning_effort defaults
        to "none" (overridable). Provider/model default like low.
      '';
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
      }
      // optionalAttrs (models.medium.reasoning_effort != null) {
        reasoning_effort = models.medium.reasoning_effort;
      };
      cron = {
        model = models.low.model;
        model_provider = models.low.provider;
      }
      // optionalAttrs (models.low.reasoning_effort != null) {
        reasoning_effort = models.low.reasoning_effort;
      };
      auxiliary = genAttrs auxiliarySlots (_: auxiliarySlot);
    }
    // optionalAttrs (models.high.reasoning_effort != null) {
      agent.reasoning_effort = models.high.reasoning_effort;
    };
  };
}
