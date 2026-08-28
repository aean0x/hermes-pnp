# Hermes PnP — requirements

- One Nix block (`services.hermesPnP.models`) names `low`, `medium`,
  `high` (router + session seeds) and `auxiliary` (official aux slots
  only — not a router tier, no slash command).
- Defaults: low = deepseek / deepseek-v4-flash; medium = deepseek /
  deepseek-v4-pro; high = xai-oauth / grok-4.6; auxiliary = same as
  low. Each has `reasoning_effort` (`nullOr str`); default unset
  except auxiliary = `"none"`.
- No fourth **router** model. No `T1`/`T2`/`T3` in plugin.yaml, WebUI
  labels, or slash commands. Model-router never writes
  `reasoning_effort` / `reasoning_config`.
- `plugins` is `listOf str`; `extraPluginDirs` is `attrsOf path`
  (`extraPlugins` is a renamed alias). Official
  `services.hermes-agent.extraPlugins` (`listOf package`) stays
  distinct and is unioned into `settings.plugins.enabled`.
- Composer on: default plugins are model-router, tool-call-coherency,
  secret-handoff (`mkDefault`). Composer off: `plugins` default is `[]`.
- `gbrain.enable` appends the two gbrain plugins if missing, without
  writing back into the `plugins` option. Listing those plugins does
  not require `gbrain.enable`.
- When composer is on, seed official `settings.model` +
  `fallback_model` from high, `delegation` from medium, `cron` from
  low, and listed auxiliary slots from `models.auxiliary` (including
  `reasoning_effort` when that option is set). Do not seed vision /
  tts / moa / goal_judge.
- Users override seeds with `hermesPnP.models.*`, or official
  `services.hermes-agent.settings.*` assigned **after** the PnP import
  (`deepConfigType` last writer wins). Do not assign
  `settings.plugins.enabled` yourself — add names via `plugins`,
  `extraPluginDirs`, or official `extraPlugins`.
- Official `extraPackages` fold into the toolbox buildEnv. Do not put
  the toolbox env back on `extraPackages`.
- WebUI/browser jails follow official `container.enable` (and
  `container.network` when that option exists).
- model-router keys are `low` / `medium` / `high`. Commands `/low`
  `/medium` `/high` `/auto`. Auto turn-start classifier replies
  `low`, `medium`, or `high` (`high` is money / irreversible / security).
  Escalation 4 on low, 3 on medium, cap high. Plugin v0.8.3.
  Auto always classifies low/medium/high (no classify_high flag).
  Model id and provider are Nix options (`hermesPnP.models.*`), written
  to plugin `config.json`. Catalog JSON has labels / `best_for` /
  escalate_* only — no model IDs. Overlay via Nix, plugin `config.json`,
  or `MODEL_ROUTER_{LOW,MEDIUM,HIGH}_*`.
  Each router tier has `best_for` (`listOf str`), `label`, and `short`.
- Official `settings` is `deepConfigType`: `mkDefault` on a leaf is
  stored as a literal. Seeds are plain attrsets; last writer wins via
  `recursiveUpdate`. Consumer settings come after the PnP import.
- Official keys: `model.{provider,default}`,
  `fallback_model.{provider,model}`, `delegation.{provider,model}`,
  `cron.{model,model_provider}`,
  `auxiliary.<slot>.{provider,model,reasoning_effort?}`.
- Auxiliary slots seeded from `models.auxiliary`: title_generation,
  compression, approval, web_extract, skills_hub, mcp,
  triage_specifier, kanban_decomposer, profile_describer, curator,
  background_review, monitor, memory_query_rewrite.
- GBrain URL stays on typed `mcpServers.gbrain.url`.
- mcp-proxy policy denials (and upstream do-not-retry results) are a
  completed `tools/call` result (`POLICY_DENIED`, `retry=false`), not
  a JSON-RPC error. Identical arguments are held for 60s, then a new
  attempt is forwarded — not a process-lifetime lockout.
- `nix flake check` stays eval-cheap (dummy agent/webui packages).
- Composer off + `plugins = [ "model-router" ]` still materializes the
  plugin and does not seed official settings.
- First-party plugins materialize to `$stateDir/plugins/<name>` with a
  relative symlink under `$stateDir/.hermes/plugins/`.
- HMC and `gbrain.enable` are opt-in. No PGLite/registry from Nix. No
  SOUL.md from Nix. No default extras on the package wrap.
