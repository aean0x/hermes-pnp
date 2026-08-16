# Hermes PnP — requirements

## Described

- One Nix block (`services.hermesPnP.models`) names three models:
  `low`, `medium`, `high`. That block seeds every place a model must
  be named (official settings, model-router `config.json`, WebUI,
  slash commands).
- Defaults: low = deepseek / deepseek-v4-flash; medium = deepseek /
  deepseek-v4-pro; high = xai-oauth / grok-4.6.
- No fourth model. No `T1`/`T2`/`T3` in Nix options, README,
  plugin.yaml, WebUI labels, or slash commands.
- Options read like a short consumer flake: comment a line to drop a
  thing. `plugins` is `listOf str`; `extraPlugins` is `attrsOf path`.
- Composer on: default plugins are model-router, tool-call-coherency,
  secret-handoff (`mkDefault`). Composer off: `plugins` default is `[]`.
- `gbrain.enable` appends the two gbrain plugins if missing. Listing
  those plugins does not require `gbrain.enable`.
- When composer is on, seed official `settings.model` +
  `fallback_model` from high, `delegation` from medium, `cron` +
  listed auxiliary slots from low (`reasoning_effort = "none"`).
  Do not seed STT / TTS / vision.
- Users override seeds with official `services.hermes-agent.settings.*`.
- model-router internal keys are `low` < `medium` < `high`. Commands
  `/low` `/medium` `/high` `/auto`. Classifier replies with exactly
  one of those three words. Escalation 4 on low, 3 on medium. Rest
  and final voice on high. Do not change that policy.
- Hidden load-time aliases for leftover `tiers` / `final_tier` /
  `MODEL_ROUTER_T1_*` / `/t1` are allowed so old configs do not crash.
- One conventional commit. Do not push. Do not open a PR.

## Inferred

- Official `settings` is `deepConfigType`: `mkDefault` on a leaf is
  stored as a literal in YAML. Seeds are plain attrsets; last writer
  wins via `recursiveUpdate`. Consumer settings must come after the
  PnP import.
- Official keys used here match live DEFAULT_CONFIG / rk3588:
  `model.{provider,default}`, `fallback_model.{provider,model}`,
  `delegation.{provider,model}`, `cron.{model,model_provider}`,
  `auxiliary.<slot>.{provider,model,reasoning_effort}`.
- Auxiliary slots seeded: title_generation, compression, approval,
  web_extract, skills_hub, mcp, triage_specifier, kanban_decomposer,
  profile_describer, curator, background_review, monitor,
  memory_query_rewrite. Not seeded: vision, tts_audio_tags, moa_*,
  goal_judge, session_search (removed upstream).
- GBrain URL stays on typed `mcpServers.gbrain.url`, not inside
  `settings.mcp_servers`.
- `nix flake check` stays eval-cheap (dummy agent/webui packages).
- Library path: composer off + `plugins = [ "model-router" ]` still
  materializes the plugin and still does not seed official settings.
- First-party plugins still materialize to `$stateDir/plugins/<name>`
  with a relative symlink under `$stateDir/.hermes/plugins/`.
- No HMC module, no declarative gbrain serve, no SOUL.md from Nix,
  no default `extraDependencyGroups = [ "mcp" ]`, no home-manager /
  darwin, no drive-by rewrites of other plugins.
