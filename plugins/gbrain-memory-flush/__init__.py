"""gbrain-memory-flush — keep MEMORY thin; durable facts live in GBrain.

Stock Hermes background_review only whitelists memory+skill tools and sets
`_skip_mcp_refresh = True` (prefix-cache parity). It cannot call MCP put_page.
Dumping whole MEMORY.md via exclusive `gbrain` CLI races `gbrain serve` and
corrupted PGLite — that path is retired.

This plugin:
1. pre_llm_call — when MEMORY.md is near the injection budget, inject a short
   instruction to put durable notes via MCP put_page and prune MEMORY to
   pointers only (main agent already has MCP tools).
2. Does not stop hermes or touch exclusive CLI.

Day-to-day SoT remains MCP. Host timers may still run brain md import + dream
with exclusive CLI; they must not dump MEMORY.md into hermes/inbox/*.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_BUDGET = int(os.environ.get("GBRAIN_MEMORY_BUDGET_CHARS", "2200"))
_WARN_AT = float(os.environ.get("GBRAIN_MEMORY_WARN_RATIO", "0.72"))
_MIN_INTERVAL_S = float(os.environ.get("GBRAIN_MEMORY_NUDGE_INTERVAL_S", "120"))

_last_nudge_mono: float = 0.0


def register(ctx: Any) -> None:
    ctx.register_hook("pre_llm_call", on_pre_llm_call)
    logger.info(
        "gbrain-memory-flush: registered pre_llm_call (budget=%s warn_ratio=%s)",
        _BUDGET,
        _WARN_AT,
    )


def on_pre_llm_call(*, user_message: Any = None, **kwargs: Any) -> Optional[Dict[str, str]]:
    """Nudge main agent when MEMORY is near full — MCP put_page + prune."""
    global _last_nudge_mono
    try:
        import time

        now = time.monotonic()
        if now - _last_nudge_mono < _MIN_INTERVAL_S:
            return None

        mem_path = _memory_path()
        if not mem_path or not mem_path.is_file():
            return None
        text = mem_path.read_text(encoding="utf-8", errors="replace")
        n = len(text)
        if n < int(_BUDGET * _WARN_AT):
            return None

        _last_nudge_mono = now
        free = max(0, _BUDGET - n)
        context = (
            f"## MEMORY budget ({n}/{_BUDGET} chars, ~{free} free)\n"
            "Durable facts must live in GBrain, not MEMORY.md. Before other work "
            "when this is the subject of the turn (or MEMORY is blocking adds):\n"
            "1. Use the GBrain MCP put_page tool on a stable slug "
            "with full markdown + frontmatter — merge, do not dump raw MEMORY.\n"
            "2. memory tool: remove or shorten those notes; leave one-line "
            "pointers only (slug → one-line reminder).\n"
            "3. Never run a second gbrain CLI process while serve is up.\n"
            "Skip this if the user message is logistics-only (ok/thanks/ping)."
        )
        return {"context": context}
    except Exception as exc:
        logger.debug("gbrain-memory-flush: fail-open: %s", exc, exc_info=True)
        return None


def _memory_path() -> Optional[Path]:
    candidates = []
    explicit = os.environ.get("HERMES_MEMORY_PATH", "").strip()
    if explicit:
        candidates.append(Path(explicit))
    home = os.environ.get("HERMES_HOME")
    if home:
        candidates.append(Path(home) / "memories" / "MEMORY.md")
    # Official container + NixOS module default stateDir fallbacks.
    candidates.extend(
        [
            Path("/data/.hermes/memories/MEMORY.md"),
            Path("/var/lib/hermes/.hermes/memories/MEMORY.md"),
        ]
    )
    for p in candidates:
        try:
            if p.is_file():
                return p
        except OSError:
            continue
    return None
