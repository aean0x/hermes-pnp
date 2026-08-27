# Cache-bust harness

Measures prompt-cache prefix stability for the Hermes Context Manager
(HMC) plugin. Replays HMC's real strategy code (`materialize_view`) over
a synthetic agent-loop conversation and reports the ideal prefix-cache
hit ratio plus every prefix-bust event. No API calls, deterministic.

## Why

HMC's `dedup` and `error_purge` strategies rewrite **old** tool messages
in place every turn. On DeepSeek, cache-hit input is ~30x cheaper than
cache-miss, so each rewrite busts the prompt-cache prefix and re-bills
the whole suffix at miss rate — often costing more than the removed
bytes save. This harness quantifies that: it computes how many input
tokens an ideal prefix cache would serve as hits under a given strategy
config, and attributes each bust to the message that changed.

## Run

Imports the installed HMC plugin's `engine`/`state`/`config` modules
directly, so results track the real code (not a reimplementation):

```sh
HMC_DIR=/data/plugins/hermes-context-manager python3 cache_bust_harness.py
```

`HMC_DIR` defaults to `/data/plugins/hermes-context-manager`.

## Result (shipped vs proposed)

| config | cache-hit ratio | bust events |
| --- | --- | --- |
| `dedup` + `error_purge` ON (shipped) | 51.7% | 3 |
| `dedup` + `error_purge` OFF (see `modules/hmc.nix`) | 79.2% | 0 |

The three busts under the shipped config:

1. a repeated read collapses to a placeholder;
2. the placeholder text itself rewrites when the repeat count changes
   (`… called N× …` is not idempotent);
3. an aging error output is purged after `turns` user turns.

## Extend

- `build_scenario()` — add a tool-call pattern that matches a real
  session's loop and re-run.
- `make_config()` — mirrors `modules/hmc.nix`; flip flags to A/B a
  strategy before shipping it.
