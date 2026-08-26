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

  # Classifier matrices live in the plugin JSON. Nix option defaults
  # follow that file so composer config.json and standalone defaults
  # cannot drift.
  pluginModels =
    (builtins.fromJSON (
      builtins.readFile ../plugins/model-router/config.default.json
    )).models;

  mkModelFields =
    defaults:
    {
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
    }
    // optionalAttrs (defaults.best_for != null) {
      best_for = mkOption {
        type = types.listOf types.str;
        default = defaults.best_for;
        description = ''
          Classifier descriptors for this router tier. Sole source of
          the model-router triage prompt. Plugin lists are the defaults;
          override here to steer routing without editing Python.
        '';
      };
    };

  mkNamedModel =
    {
      provider,
      model,
      description,
      reasoning_effort ? null,
      best_for ? null,
    }:
    mkOption {
      type = types.submodule {
        options = mkModelFields {
          inherit
            provider
            model
            reasoning_effort
            best_for
            ;
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
      description = "Cheap helper. OOBE seed for unpinned cron. Auxiliary inherits provider/model.";
    };

    medium = mkNamedModel {
      provider = "deepseek";
      model = "deepseek-v4-pro";
      best_for = pluginModels.medium.best_for;
      description = "Workhorse. OOBE seed for delegation and model-router medium.";
    };

    high = mkNamedModel {
      provider = "xai-oauth";
      model = "grok-4.6";
      best_for = pluginModels.high.best_for;
      description = ''
        Session identity + voice. OOBE seed for settings.model.default
        and fallback. Override here — not settings.model.default —
        unless you assign settings after the PnP import (deepConfigType
        last writer wins; mkDefault on a leaf is stored as a literal).
      '';
    };

    auxiliary = mkNamedModel {
      provider = models.low.provider;
      model = models.low.model;
      best_for = pluginModels.auxiliary.best_for;
      reasoning_effort = "none";
      description = ''
        Cheap no-reasoning tier. Official auxiliary tasks (title
        generation, compression, …) and model-router's cheapest tier.
        Provider/model follow models.low; reasoning_effort defaults to
        "none" (overridable).
      '';
    };
  };

  config = mkIf pnp.enable {
    services.hermes-agent.settings = {
      model = {
        provider = models.high.provider;
        default = models.high.model;
        # No context_length here — it is model-specific and resolved from
        # hermes-agent metadata (grok-4.6 = 500k; deepseek-v4-flash/-pro = 1M).
        # The consumer flake owns the explicit model.context_length (500000);
        # do not shadow it with a wrong fixed value.
      };
      # Activate the model-router handoff context engine. Without this the
      # escalate_model tool still switches models, but the aggressive 20-30k
      # handoff compaction is inactive (falls back to normal thresholds).
      context = {
        engine = "model-router";
      };
      # Per-model compaction ceilings, as fractions of EACH model's own window.
      # resolve_model_threshold: longest key wins. hermes-agent also floors any
      # model under 512k at >=0.75, and the consumer's global
      # compression.threshold_tokens takes the LOWER of fraction*window and the
      # cap — so the effective ceiling is min(fraction*window, cap).
      #   auxiliary (deepseek-v4-flash, 1M) 0.95 → overflow-only ("idc" cache)
      #   medium    (deepseek-v4-pro,   1M) 0.26 → ~260k
      #   high      (grok-4.6,        500k) 0.28 → ~140k intent (floored to 0.75)
      compression = {
        model_thresholds = {
          "${models.auxiliary.model}" = 0.95;
          "${models.medium.model}" = 0.26;
          "${models.high.model}" = 0.28;
        };
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
