# Seed services.hermes-agent.settings from hermesPnP.models.
# Always-on slots: models.default + models.auxiliary.
# modelRouter.enable adds models.low + models.high and the plugin.
# services.hermesPnP.model.default ("low" | "default" | "high"; default
# "default") picks which tier seeds settings.model.default when the
# router is on. "medium" is a deprecated alias for "default".
# fallback_model is models.high with the router, else models.default.
#
# settings is deepConfigType: do not wrap leaves in mkDefault (the merge
# stores the wrapper as a literal). Last writer wins; assign consumer
# settings after the PnP import.
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
    foldl'
    genAttrs
    mkIf
    mkOption
    optionalAttrs
    recursiveUpdate
    types
    ;

  pnp = config.services.hermesPnP;
  inherit (pnp) models;

  # Labels and best_for default from the plugin JSON. Model id and
  # provider are Nix options (composer OOBE below) — not catalog IDs.
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
      compression_ratio = mkOption {
        type = types.addCheck types.float (x: x > 0 && x <= 1);
        default = defaults.compression_ratio;
        description = ''
          Fraction of this model's own context window at which Hermes
          auto-compaction fires. Seeded into
          settings.compression.model_thresholds.<model>. Hermes
          resolves the window (built-in table + models.dev + live
          probe) unless context_length is set. 0.95 ≈ overflow-only.
        '';
      };
      context_length = mkOption {
        type = types.nullOr types.ints.positive;
        default = defaults.context_length;
        description = ''
          Optional hard window for this model, in tokens. null lets
          Hermes resolve it. When set, PnP writes
          model_overrides.<provider>.<model>.context_window. Do not
          set settings.model.context_length in the consumer — that
          one value stamps every model until the first switch.
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
      label = mkOption {
        type = types.str;
        default = defaults.label;
        description = "Display name (WebUI pin). Router tiers only.";
      };
      short = mkOption {
        type = types.str;
        default = defaults.short;
        description = "Short display name. Router tiers only.";
      };
    };

  mkNamedModel =
    {
      provider,
      model,
      description,
      reasoning_effort ? null,
      best_for ? null,
      label ? null,
      short ? null,
      compression_ratio ? 0.95,
      context_length ? null,
    }:
    mkOption {
      type = types.submodule {
        options = mkModelFields {
          inherit
            provider
            model
            reasoning_effort
            best_for
            label
            short
            compression_ratio
            context_length
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

  # Session-facing names only. Auxiliary often shares default's model
  # id — do not fold it in or it would just rewrite the same threshold.
  sessionTier =
    let
      raw = pnp.model.default;
      name = if raw == "medium" then "default" else raw;
    in
    if pnp.modelRouter.enable then name else "default";

  namedModels =
    if pnp.modelRouter.enable then
      [
        models.low
        models.default
        models.high
      ]
    else
      [ models.default ];

  workhorse = models.default;
  fallbackSlot = if pnp.modelRouter.enable then models.high else models.default;
  cronSlot = if pnp.modelRouter.enable then models.low else models.default;

  modelThresholds = foldl' (
    acc: m: acc // { "${m.model}" = m.compression_ratio; }
  ) { } namedModels;

  modelOverrides = foldl' (
    acc: m:
    if m.context_length == null then
      acc
    else
      recursiveUpdate acc {
        ${m.provider} = {
          ${m.model} = {
            context_window = m.context_length;
          };
        };
      }
  ) { } namedModels;
in
{
  imports = [
    (lib.mkRenamedOptionModule
      [ "services" "hermesPnP" "models" "medium" ]
      [ "services" "hermesPnP" "models" "default" ]
    )
  ];

  options.services.hermesPnP.modelRouter.enable = mkOption {
    type = types.bool;
    default = true;
    description = ''
      Install the model-router plugin and seed low / default / high.
      false: do not materialize the plugin, do not set
      context.engine = "model-router", and seed session / fallback /
      delegation / cron from models.default only. models.default and
      models.auxiliary stay required either way.
    '';
  };

  options.services.hermesPnP.model = {
    default = mkOption {
      type = types.enum [
        "low"
        "default"
        "high"
        "medium"
      ];
      default = "default";
      description = ''
        Which named slot seeds settings.model.default when
        modelRouter.enable is true. "medium" is accepted as "default".
        Ignored when the router is off (always models.default).
        fallback_model is models.high with the router, else
        models.default.

        Consumers can still override directly: assign
        services.hermes-agent.settings.model.default in a module after
        the PnP import (deepConfigType last-writer-wins). Do not wrap
        that leaf in mkDefault — the merge stores it as a literal.
      '';
      example = "high";
    };
  };

  options.services.hermesPnP.models = {
    low = mkNamedModel {
      provider = "deepseek";
      model = "deepseek-v4-flash";
      inherit (pluginModels.low) best_for label short;
      compression_ratio = 0.95;
      description = "Cheap helper. OOBE seed for unpinned cron and model-router low.";
    };

    default = mkNamedModel {
      provider = "deepseek";
      model = "deepseek-v4-pro";
      inherit (pluginModels.default) best_for label short;
      compression_ratio = 0.26;
      description = "Workhorse. Session seed, delegation, and model-router default.";
    };

    high = mkNamedModel {
      provider = "xai-oauth";
      model = "grok-4.6";
      inherit (pluginModels.high) best_for label short;
      compression_ratio = 0.28;
      description = ''
        Session identity + voice. OOBE seed for settings.fallback_model.
        Pick it as the session default via services.hermesPnP.model.default
        = "high". Override settings.model.default directly only in a
        module after the PnP import (deepConfigType last writer wins;
        mkDefault on a leaf is stored as a literal).
      '';
    };

    auxiliary = mkNamedModel {
      provider = "deepseek";
      model = "deepseek-v4-flash";
      compression_ratio = 0.95;
      reasoning_effort = "none";
      description = ''
        Official auxiliary tasks (title generation, compression, …).
        Nix-only — not a model-router tier, no slash command.
        reasoning_effort defaults to "none" (overridable).
        Provider/model default like low.
      '';
    };
  };

  config = mkIf pnp.enable {
    assertions = [
      {
        assertion = pnp.modelRouter.enable || sessionTier == "default";
        message = ''
          services.hermesPnP.model.default must be "default" when
          modelRouter.enable is false (low/high are router-only).
        '';
      }
    ];

    services.hermes-agent.settings = {
      model = {
        provider = models.${sessionTier}.provider;
        default = models.${sessionTier}.model;
        # No global context_length. Hermes resolves each model's window.
        # Override per name via models.<name>.context_length → model_overrides.
      };
      context = {
        engine = if pnp.modelRouter.enable then "model-router" else "compressor";
      };
      compression = {
        model_thresholds = modelThresholds;
      };
      fallback_model = {
        provider = fallbackSlot.provider;
        model = fallbackSlot.model;
      };
      delegation = {
        provider = workhorse.provider;
        model = workhorse.model;
      }
      // optionalAttrs (workhorse.reasoning_effort != null) {
        reasoning_effort = workhorse.reasoning_effort;
      };
      cron = {
        model = cronSlot.model;
        model_provider = cronSlot.provider;
      }
      // optionalAttrs (cronSlot.reasoning_effort != null) {
        reasoning_effort = cronSlot.reasoning_effort;
      };
      auxiliary = genAttrs auxiliarySlots (_: auxiliarySlot);
    }
    // optionalAttrs (modelOverrides != { }) {
      model_overrides = modelOverrides;
    }
    // optionalAttrs (pnp.modelRouter.enable && models.high.reasoning_effort != null) {
      agent.reasoning_effort = models.high.reasoning_effort;
    };
  };
}
