# Model-Router Redesign — Implementation Plan

Status: draft (uncommitted, for review before implementation)
Target branch: `feat/model-router-auxiliary-tier` (v0.6.0, tiers `auxiliary`/`medium`/`high`)

## 1. Current state (verified against source)

| Piece | Where | Current behavior |
|---|---|---|
| Tiers | `plugins/model-router/settings.py` | `auxiliary` = deepseek-v4-flash, `medium` = deepseek-v4-pro (both `deepseek`), `high` = grok-4.6 (`xai-oauth`) |
| Turn-start classify | `__init__.py:690 on_pre_llm_call` → `:612 _target_tier` → `:566 _classify` | `_classify` calls `call_llm(task="triage_specifier", …)` = **always flash**, minimal context (last 2 assistant msgs ×300 chars + user msg ×800) |
| Escalation | `__init__.py:782 on_post_tool_call` | **auto-climbs** one tier after N consecutive tool errors (`auxiliary`:4, `medium`:3), cap high |
| Switch | `__init__.py:405 _apply_tier` | resolves via `hermes_cli.model_switch.switch_model`, calls `agent.switch_model(...)` |
| Token limit | `config.yaml:34 model.context_length` | **200000**, Nix-managed, **not** set in hermes-pnp (grep empty → consumer flake or Hermes default) |
| Compaction | Hermes core (`compression.*`) | threshold `0.3`, `target_ratio 0.15`, `threshold_tokens 180000`, `model_thresholds` (deepseek-v4 looser). Auto-fires on threshold/idle/prune. |

Compaction entry point for a plugin: `agent._compress_context(...)` (private, called by the conversation loop at turn boundaries) or `agent.context_engine.compress(...)`. **No plugin-safe public API** — this is spike S-1 below.

## 2. Design requirements (Ian's five + checkpoint + high-removal)

1. Token limit **384K** for all three models; compact at a "reasonable coherency ratio", looser for deepseek, tighter for grok (unchanged pattern).
2. **Compact only on shift-up** (rank increase), from either turn-start classification or mid-turn escalation.
3. **Compact on shift-up only once per session.**
4. **Classifier runs on the previous turn's model**, not the triage slot; classifier does not run at all when pinned; if the session was pinned to grok then auto-restored, default the classifier to `auxiliary`.
5. **Classifier receives normal context** (full system prompt + history) so its call shares the session cache prefix.
6. (From prior agreement) **High removed from turn-start classification** — high is escalation-only in `auto`; `/high` pin still available.
7. (From prior agreement) **Escalation gated behind an in-turn checkpoint** run by the current model, not the classify model.

## 3. Architecture changes

### 3.1 R1 — token limit 384K

- Add to `modules/models.nix`: `settings.model.context_length = lib.mkDefault 384000;` (mkDefault so the consumer can override; hermes-pnp is the composer library and the generated config.yaml is owned by the consumer flake — verify the consumer does not already pin 200000, which would shadow this).
- Leave compaction ratios as-is (`threshold 0.3`, `model_thresholds` deepseek looser). Confirm `target_ratio 0.15` is still the "reasonable coherency ratio" — flag for Ian if the summary quality at 0.15 is too lossy.
- **Verify** whether 384K exceeds grok-4.6's real window. If `get_model_context_length` clamps, the config value is an upper bound only; document the actual per-model resolution.

### 3.2 R2/R3 — compact on shift-up, once per session

- New global `_compacted_sessions: set[str]`.
- In `_set_tier` (after a tier change is committed): if `RANK[new] > RANK[prev]` and `sid not in _compacted_sessions`, mark `_compacted_sessions.add(sid)` and trigger compaction.
- **Compaction mechanism = spike S-1.** Two candidates:
  - *Primary:* invoke `agent._compress_context(...)` (or `context_engine.compress`) from `on_pre_llm_call` when a shift-up is pending — that hook fires at a turn boundary, the only safe point.
  - *Fallback:* set a `_compact_requested[sid]` flag consumed by `on_pre_llm_call`, which returns `{"context": ...}` is not the mechanism — instead call the compressor directly if S-1 proves it safe, else defer to Hermes' own next-boundary compaction by temporarily lowering nothing and accepting a one-turn cold read.
- Mid-turn escalation (from `on_post_tool_call`) happens between tool calls; do **not** compact mid-tool-loop. If compaction can't safely run mid-turn, the escalated turn accepts a one-time cold read and compaction lands at the next turn boundary. Document this as the accepted behaviour.

### 3.3 R4/R5 — classifier on previous model + normal context

- New global `_prev_model: dict[sid, str]` (the model name of the previous turn's settled tier; updated at end of each turn).
- Rewrite `_classify(user_message, history)`:
  - Replace `call_llm(task="triage_specifier", messages=minimal)` with a direct completion on **`_prev_model[sid]`** (fallback `auxiliary` if absent or if `_prev_model` was grok via a prior pin→auto-restore, per R4).
  - Pass the **full context**: system prompt (reuse `_CLASSIFIER` only if a dedicated classifier system prompt is still wanted; otherwise the session system prompt) + full `history`, so the call byte-matches the session prefix and hits cache.
  - Keep `max_tokens=8`, `temperature=0.0`, fail-open to `medium`.
- `on_pre_llm_call`: skip `_classify` entirely when pinned (already the case) and when session has no prior turn (no `_prev_model` → default `auxiliary`).

### 3.4 R6 — remove high from turn-start

- In `_target_tier`, clamp the classifier result to `min(result, "medium")` so `auto` never starts a turn on `high`. `high` remains reachable only via escalation or explicit `/high` pin (`_detect_explicit_tier`).

### 3.5 R7 — escalation checkpoint (replaces auto-climb)

- Remove the auto-climb branch in `on_post_tool_call`.
- New flow:
  1. `on_post_tool_call` counts consecutive errors; at threshold set `_checkpoint[sid] = True` (do not climb).
  2. Next `on_pre_llm_call` sees `_checkpoint[sid]`, returns `{"context": "<nudge>"}` (verified: `pre_llm_call` return `{"context": ...}` is appended to the user message — `turn_context.py:449`), clears the flag. Nudge text (runs on the **current** model): "You've had N consecutive tool failures. Decide: make a working attempt on the next step, or end the turn and ask the user for more context / to escalate manually."
  3. Escalation signal (decision point D-2): on the next **failure** (N+1) `on_post_tool_call` escalates to `high`; a successful tool call resets the counter; a user-directed end-of-turn (no tool call) means the model chose to ask the user — no escalation.

### 3.6 Prune orphaned code

- Delete the old auto-climb threshold logic in `on_post_tool_call` (superseded by 3.5).
- Drop any half-switch / host-stomp repair paths that are dead after 3.3's direct-completion classifier (audit `_heal_uncategorized`, `_repair_host_stomp` usage).
- Remove the `triage_specifier`-slot coupling in `_classify` if the new direct-completion path makes it unused (keep the slot seed in Nix — it still serves other consumers).
- Reconcile the `auxiliary` vs `low` naming drift: `AGENTS.md` still says `low/medium/high` and "no /auxiliary"; `DESIGN.md:151` already says `auxiliary/medium/high`. Update AGENTS.md (and any `/low` command aliases) to match the branch.

## 4. New state (add to module globals)

- `_compacted_sessions: set[str]`
- `_prev_model: dict[str, str]`
- `_checkpoint: dict[str, bool]`
- (existing) `_last_msg`, `_last_tier`, `_pinned`, `_tool_errors`, `_last_user_sid`, `_last_bound`, `_lock`

All keyed by `session_id`, cleared on session end where Hermes provides a teardown hook (else rely on bounded growth + the existing per-sid dicts' patterns).

## 5. Spike / verify items

- **S-1 (compaction invocation):** confirm a plugin can safely call `agent._compress_context` / `context_engine.compress` at the `on_pre_llm_call` boundary without corrupting session state (SQLite split, dedup, system-prompt rebuild). Deliverable: a minimal spike plugin + a green run on a throwaway session. *Blocks R2/R3.*
- **S-2 (context_length seeding):** locate whether the consumer flake pins `model.context_length: 200000` (would shadow the models.nix `mkDefault`). Grep the consumer repo + confirm the generated config.yaml's provenance.
- **S-3 (384K feasibility):** `get_model_context_length("grok-4.6")` and the two deepseek models — confirm 384000 is honored or clamped, so the plan documents the real ceiling.
- **S-4 (plugin tool registration):** verify whether a Hermes plugin can register a tool (for a potential explicit "escalate" action in D-2). If yes, prefer an explicit `escalate_model` tool over the N+1 backstop; if no, ship the backstop.

## 6. Decision points for Ian

- **D-1 "shift-up" scope:** compact on *any* rank increase (aux→med, med→high) or only when the destination is `high`? Rationale argues only→high matters (deepseek↔deepseek cache forfeit is sub-cent), but R2 as written says "shifting up" generally. Recommend: compact only when destination is `high`.
- **D-2 escalation signal:** explicit `escalate_model` tool (needs S-4) vs implicit N+1-failure backstop (no new infra). Recommend the backstop for v1, upgrade to the tool if S-4 is clean.
- **D-3 disable Hermes auto-compaction:** R2 implies Hermes' threshold/idle/prune compaction should stop (else it fires before shift-up). Options: (a) keep it as a safety net at a high threshold, (b) `compression.enabled: false` + router-only compaction. Recommend (a) — keep `threshold_tokens` high (~360K) as overflow protection, router compacts earlier on shift-up.

## 7. Task list (ordered, bite-sized)

1. S-3 + S-2: confirm 384K ceiling + locate consumer `context_length` override. *(unblocks R1)*
2. R1: add `settings.model.context_length = lib.mkDefault 384000` to `modules/models.nix`; adjust `compression.threshold_tokens` per D-3.
3. R6: clamp `_target_tier` to never return `high` in auto.
4. R4/R5: add `_prev_model`; rewrite `_classify` to direct-completion on previous model with full context; wire `_prev_model` update at turn end.
5. S-1: spike compaction invocation; land a safe `_compact(sid, agent)` helper.
6. R2/R3: in `_set_tier`, on shift-up-to-high (per D-1) and `sid not in _compacted_sessions`, mark + call `_compact`.
7. R7: replace auto-climb with checkpoint nudge + escalation signal (D-2).
8. Prune: remove dead auto-climb / half-switch paths (3.6).
9. Tests: update `tests/test_router.py` + `tests/test_settings.py` (classifier-model selection, no-high-at-turn-start, once-per-session compaction gate, checkpoint flag).
10. Docs: update `DESIGN.md` (router section), `AGENTS.md` (auxiliary naming), `README.md` if it references tiers.

## 8. Verification

- Unit: `pytest plugins/model-router/tests` green.
- Behavioural (local): pin a session `/auto`, confirm turn-start never lands on `high`; force 4 consecutive failures on a long context → confirm compaction fires once, escalation to `high`, second escalation does not re-compact; confirm classifier logs show the previous model (not flash).
- Cost: after deploy, read `prompt_cache_hit_tokens` (deepseek) / `cached_tokens` (xai) to confirm hit-rate improved — this is the probe plugin from the earlier plan, kept as a follow-up.
