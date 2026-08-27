#!/usr/bin/env python3
"""Cache-bust harness for Hermes Context Manager (HMC).

Replays HMC's REAL strategy code (``materialize_view``) over a synthetic
agent-loop conversation and measures prompt-cache prefix stability. A
prefix cache serves the longest common prefix (LCP) between consecutive
provider requests as a "hit"; a strategy that rewrites an OLD message
shrinks the LCP and re-bills the whole suffix at miss rate.

This is the empirical half of the "does HMC help or hurt" question: it
quantifies, in tokens, how many would be cache-hits under an ideal
prefix cache for a given strategy config -- no API cost, deterministic.

Run:
    python3 cache_bust_harness.py [--hmc-dir /data/plugins/hermes-context-manager]

Imports the installed HMC plugin's engine/state/config modules directly,
so the numbers track the real code, not a reimplementation.
"""
from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path

HMC_DIR = Path(os.environ.get("HMC_DIR", "/data/plugins/hermes-context-manager"))
sys.path.insert(0, str(HMC_DIR))

# config.py does ``import yaml`` at module top. We only build HmcConfig
# directly (never load_config), so stub yaml if it isn't installed.
try:
    import yaml  # noqa: F401
except ImportError:
    sys.modules["yaml"] = types.ModuleType("yaml")

from hermes_context_manager.config import HmcConfig  # noqa: E402
from hermes_context_manager.engine import (  # noqa: E402
    estimate_message_tokens,
    materialize_view,
)
from hermes_context_manager.state import (  # noqa: E402
    SessionState,
    ToolRecord,
    create_input_fingerprint,
)

# Fields that actually ship to the provider (mirrors engine._API_VISIBLE_FIELDS).
API_FIELDS = ("role", "content", "tool_calls", "tool_call_id", "name")


def wire_key(message: dict) -> str:
    """Canonical serialization of a message's on-the-wire fields."""
    rel = {k: message[k] for k in API_FIELDS if k in message}
    return json.dumps(rel, sort_keys=True, default=str)


def make_config(dedup: bool, purge: bool) -> HmcConfig:
    """Mirror the shipped modules/hmc.nix values, flipping the two flags
    that mutate the historical prefix."""
    cfg = HmcConfig()
    cfg.strategies.deduplication.enabled = dedup
    cfg.strategies.purge_errors.enabled = purge
    cfg.strategies.purge_errors.turns = 4
    cfg.truncation.enabled = True
    cfg.truncation.max_lines = 30
    cfg.truncation.head_lines = 10
    cfg.truncation.tail_lines = 6
    cfg.truncation.min_content_length = 500
    cfg.short_circuits.enabled = True
    cfg.code_filter.enabled = True
    cfg.background_compression.enabled = False
    return cfg


BIG_FILE = "# app config\n" + ("setting: value\n" * 300)   # ~4k chars
OTHER_FILE = "# notes\n" + ("- a line of notes\n" * 400)


def build_scenario() -> list:
    """Synthetic conversation built from the exact cache-busting patterns:
    repeated identical reads (dedup) and an error that ages out (purge)."""

    def read(cid: str, path: str) -> dict:
        return {
            "id": cid,
            "name": "read_file",
            "args": {"path": path},
            "content": BIG_FILE if path == "/app/config.yaml" else OTHER_FILE,
            "error": False,
        }

    def err(cid: str) -> dict:
        return {
            "id": cid,
            "name": "read_file",
            "args": {"path": "/app/missing.yaml"},
            "content": "RuntimeError: no such file /app/missing.yaml",
            "error": True,
        }

    return [
        ("Inspect the app config.", [read("c1", "/app/config.yaml")]),
        ("Re-read it to be sure.", [read("c2", "/app/config.yaml")]),
        ("Now load the missing file.", [err("c3")]),
        ("Also check the notes file.", [read("c4", "/app/notes.md")]),
        ("Read config again to check for drift.", [read("c5", "/app/config.yaml")]),
        ("Turn six, no tools.", []),
        ("Turn seven, no tools.", []),
    ]


def replay(cfg: HmcConfig) -> list:
    """Run the scenario under a config, returning per-turn wire snapshots."""
    messages = [{"role": "system", "content": "You are a helpful coding agent."}]
    state = SessionState()
    snapshots = []

    for turn, (user_text, tool_calls) in enumerate(build_scenario(), start=1):
        messages.append({"role": "user", "content": user_text})
        if tool_calls:
            messages.append({
                "role": "assistant",
                "content": "Working on it.",
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["args"]),
                        },
                    }
                    for tc in tool_calls
                ],
            })
            for tc in tool_calls:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "name": tc["name"],
                    "content": tc["content"],
                })
                state.tool_calls[tc["id"]] = ToolRecord(
                    tool_call_id=tc["id"],
                    tool_name=tc["name"],
                    input_args=tc["args"],
                    input_fingerprint=create_input_fingerprint(tc["name"], tc["args"]),
                    is_error=tc["error"],
                    turn_index=turn,
                    timestamp=0.0,
                    token_estimate=len(tc["content"]) // 4,
                )
        else:
            messages.append({"role": "assistant", "content": "Done."})

        # Materialize on a shallow copy so `messages` stays pristine across turns.
        work = [dict(m) for m in messages]
        materialize_view(work, state, cfg)
        snapshots.append(
            (turn, [wire_key(m) for m in work], [estimate_message_tokens(m) for m in work])
        )

    return snapshots


def compute(snapshots: list):
    """Prefix-cache accounting: hit tokens = LCP with the previous request."""
    total = 0
    hit = 0
    busts = []
    prev_keys = None
    for turn, keys, toks in snapshots:
        total += sum(toks)
        if prev_keys is not None:
            k = 0
            n = min(len(prev_keys), len(keys))
            while k < n and prev_keys[k] == keys[k]:
                k += 1
            hit += sum(toks[:k])
            if k < len(prev_keys):
                busts.append((turn, k, len(prev_keys), prev_keys[k], keys[k]))
        prev_keys = keys
    ratio = hit / total if total else 0.0
    return total, hit, ratio, busts


def label(snippet: str) -> str:
    try:
        obj = json.loads(snippet)
        role = obj.get("role", "?")
        content = obj.get("content", "")
        if isinstance(content, str):
            content = content[:80]
        return f"{role}: {content}"
    except Exception:
        return snippet[:80]


def report(name: str, cfg: HmcConfig) -> None:
    total, hit, ratio, busts = compute(replay(cfg))
    print(f"\n=== {name} ===")
    print(f"  total input tokens (sum of all requests): {total:,}")
    print(f"  cache-hit tokens:                          {hit:,}")
    print(f"  cache-hit ratio:                           {ratio:.1%}")
    print(f"  prefix-bust events:                        {len(busts)}")
    for turn, k, plen, old, new in busts:
        print(f"    turn {turn}: prefix broke at msg {k}/{plen}")
        print(f"        was: {label(old)}")
        print(f"        now: {label(new)}")


def main() -> None:
    report("current config  (dedup + error_purge ON)", make_config(dedup=True, purge=True))
    report("proposed config (dedup + error_purge OFF)", make_config(dedup=False, purge=False))


if __name__ == "__main__":
    main()
