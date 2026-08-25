# Model-Router Redesign — cache-aware escalation

Status: implemented on `feat/model-router-auxiliary-tier` (v0.7.0).

## Why

Teknium's point holds verbatim: switching models invalidates the new model's
entire prompt cache, so the new model repays full input price on the whole
conversation. For Grok that is $2.00/M vs $0.50/M cached — the single biggest
cost spike when the router climbs to `high`. This redesign makes the climb to
`high` an explicit, compacted hand-off instead of a full-context cold read.

## Design

### 1. Turn-start classification is binary (`auxiliary` / `medium`)

- `high` is removed from turn-start classification. It is reachable only via
  the `escalate_model` tool mid-turn, or an explicit `/high` pin.
- `_generated_classifier` now offers only `auxiliary`/`medium`.
- `_classify` runs on the **previous turn's tier** (never grok) with real
  history (not the old 300-char/800-char truncation). Routing is via
  `_resolve_tier_runtime`, which reuses the same `resolve_switch` path as
  `_apply_tier`; falls back to the `triage_specifier` auxiliary slot if the
  runtime cannot be resolved.
- `_target_tier` clamps any `high` outcome to `medium` at turn start
  (`+clamp(high escalation-only)`).

### 2. Escalation is a checkpoint + tool, not an auto-climb

- `on_post_tool_call` no longer climbs. After N consecutive tool errors (4 on
  auxiliary, 3 on medium) it stages an **escalation checkpoint**.
- The next tool-continuation `pre_llm_call` injects a one-shot nudge
  (`_CHECKPOINT_NUDGE`) telling the working model it may call `escalate_model`.
- The working model decides — it can keep going or escalate. The classifier is
  never the one escalating.

### 3. `escalate_model` tool + handoff context engine

- `escalate_model` is a plugin tool (`provides_tools`, `register_tool`) with a
  structured schema: `summary`, `task_state`, `tried_so_far`, `failure_point`,
  `next_hypothesis`. The schema forces the handing-over model to record where
  it failed, so Grok does not repeat dead approaches.
- On call, the handler stashes the handoff on the live agent's
  `context_compressor` and switches to the next tier.
- `engine.py` registers a `ContextCompressor` subclass (`ModelRouterContextEngine`,
  `name = "model-router"`) whose `select_context` — fired per provider request,
  mid-tool-loop included — swaps the outgoing message list to
  **system prompt + handoff summary + recent verbatim tail (~20–30k tokens)**.
  Persisted history is never mutated; the swap is request-scoped, so prompt
  cache and the conversation DB stay coherent.
- Handoff tail budget is `handoff_tail_chars` (default 64000 ≈ 16k tokens);
  the handoff summary is whatever the model wrote (2–8k), landing the total in
  the 20–30k target.

### 4. Per-model compaction ceilings

Hermes auto-compaction keeps churning, but at per-model ceilings:

- `auxiliary` (deepseek-v4-flash) → `~0.95` → overflow-only ("idc" cache).
- `medium` (deepseek-v4-pro) → `~0.68` → ~260k.
- `high` (grok-4.6) → `~0.37` → ~140k.

These are fractions of `model.context_length`, seeded in `modules/models.nix`
alongside `context.engine = "model-router"` and `context_length = 384000`.

## Config contract

`modules/models.nix` seeds:

```nix
model.context_length = 384000;
context.engine = "model-router";
compression.model_thresholds = {
  "<auxiliary.model>" = 0.95;
  "<medium.model>"    = 0.68;
  "<high.model>"      = 0.37;
};
```

**Consumer action required:** the 260k/140k ceilings assume a 384k context
window. If the consumer flake assigns `model.context_length` after the PnP
import (deepConfigType last-writer-wins), it shadows the PnP default — remove
or match it. Without `context.engine = "model-router"`, escalation still
switches models but the aggressive handoff compaction is inactive (falls back
to normal thresholds only).

## Files

- `plugins/model-router/engine.py` — `ModelRouterContextEngine` (`select_context`).
- `plugins/model-router/__init__.py` — binary classifier, checkpoint,
  `escalate_model` tool, `_resolve_tier_runtime`, engine registration.
- `plugins/model-router/settings.py` — binary classifier prompt, handoff config.
- `plugins/model-router/config.default.json` — `handoff_tail_chars`,
  `classifier_context_chars`.
- `plugins/model-router/plugin.yaml` — `provides_tools: [escalate_model]`, v0.7.0.
- `modules/models.nix` — `context.engine`, `context_length`, `model_thresholds`.
