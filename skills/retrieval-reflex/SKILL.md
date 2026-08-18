---
name: retrieval-reflex
description: "When brain pointers appear or a durable entity is salient, get_page / volunteer_context before answering from MEMORY alone."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [gbrain, memory, hermes-pnp]
---

# Retrieval reflex (gbrain-native)

Policy skill for ambient + op GBrain push-context on Hermes.

Plugin SoT: catalog `gbrain-retrieval-reflex`.
Runtime audit: `~/.gbrain/retrieval-reflex-last.json`.

## What injects pointers

| Channel | Who | How |
|---------|-----|-----|
| Ambient | plugin `gbrain-retrieval-reflex` | On every non-trivial `pre_llm_call`: HTTP MCP **`volunteer_context`** (entity resolve, multi-turn window) **and** hybrid **`query`** (topical). Merge/dedupe → inject **top-5** `## Brain pages (ambient push)`. |
| Op | **you** (MCP) | Extra `volunteer_context` / `query` when ambient miss, deeper recall, or multi-hop |
| Open | **you** (MCP) | `get_page` on ranked slugs before relying on details |

Ambient uses MCP over the consumer's `gbrain serve --http` (Bearer). Never a second serve or bare CLI.

## Ambient block shape (present state)

```text
## Brain pages (ambient push)
Top matches by relevance (strength-ordered). Open with MCP get_page before relying on details.

1. **Title** → `slug` (0.97 · query) — preview synopsis…
2. **…** → `…` (0.65 · volunteer/slug-suffix) — …
Never shell gbrain CLI while serve is up.
```

- Ranked 1..N (default **N=5**). Strength = volunteer `confidence` or query score.
- Source tags: `volunteer` / `volunteer/<arm>` / `query` / `volunteer+query` (audit `source`).
- Previews prefer: volunteer `synopsis` → page/query `summary`/`description` → cleaned `chunk_text` (headers stripped, clipped ≤400).
- Env knobs: `GBRAIN_RETRIEVAL_REFLEX_MAX_POINTERS`, `_SYNOPSIS_MAX`, `_MIN_CONF` (volunteer gate, default 0.6), `_HISTORY_TURNS`, `_MAX_CONTEXT_BYTES`.

## volunteer_context vs query

| | `volunteer_context` | `query` |
|--|---------------------|---------|
| Mode | Entity/name resolve (alias ≥0.9, title ≥0.8, slug-suffix ≥0.6; multi-turn boosts) | Hybrid topical search |
| Best for | People, companies, named ops pages | Concepts, “how does X work”, multi-keyword |
| Empty when | No extractable entity aliases in the window (topic-only turns) | Weak lexical/vector match |
| Plugin role | Precision first in merge | Fills remaining top-N slots by score |

Empty volunteer on topic-only turns is expected; query covers that. Named-entity turns still need volunteer. `volunteer_context` is a GBrain MCP tool the plugin calls.

## When a pointer block appears

1. Treat it as a ranked lead list, not full truth.
2. Call MCP **`get_page`** on slugs that are the subject of the answer (or top 1–2 by strength).
3. Do **not** invent details from MEMORY when a pointer is present.

## When no pointer block (or inject empty)

1. Call MCP **`volunteer_context`** with a short multi-turn window (oldest → newest, `user:` / `assistant:` lines).
2. Else **`query`** with a tight phrase.
3. Session / MEMORY only after that; durable facts → **`put_page`**.

## Anti-patterns

- Shell `gbrain` CLI while hermes-agent / serve is up
- Maintaining a workspace alias JSON / pointer-index
- Answering from MEMORY alone when a pointer or durable entity is the subject
- Expecting volunteer to replace topical search (use both)

## Tools (typical MCP names)

| Intent | Tool |
|--------|------|
| Entity push | `volunteer_context` |
| Topical search | `query` |
| Open page | `get_page` |
| Write | `put_page` |
