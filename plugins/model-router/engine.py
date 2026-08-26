"""model-router — handoff context engine.

A transparent subclass of Hermes's built-in ``ContextCompressor`` that adds a
single capability: request-scoped context replacement for mid-turn escalation.

When the ``escalate_model`` tool fires, it stashes a structured handoff on the
live agent's ``context_compressor`` (this engine, deep-copied per agent). On the
next provider request — including the very next call in the same tool loop —
``select_context`` swaps the outgoing message list for a compact handoff
(system prompt + handoff summary + recent verbatim tail) so the higher model
does not cold-read the full conversation. Persisted history is never mutated;
the swap is request-only, so prompt cache and the conversation DB stay coherent.

Every other method is inherited unchanged from ``ContextCompressor``, so normal
compaction (``should_compress`` / ``compress`` / ``update_model`` /
``model_thresholds``) is byte-for-byte the built-in behavior.
"""

from __future__ import annotations

from typing import Any

from agent.context_compressor import ContextCompressor

ENGINE_NAME = "model-router"

# Fallback tail budget if the settings module cannot be loaded (roughly 16k
# tokens at ~4 chars/token). The handoff summary (2-8k tokens) plus this tail
# lands the total within the 20-30k escalation budget.
_DEFAULT_TAIL_CHARS = 64000


def _load_tail_chars() -> int:
    try:
        from . import settings as _s

        return int(getattr(_s, "HANDOFF_TAIL_CHARS", _DEFAULT_TAIL_CHARS))
    except Exception:
        return _DEFAULT_TAIL_CHARS


def _format_handoff(handoff: dict[str, Any]) -> str:
    """Render the structured handoff the escalating model supplied."""
    dest = (handoff.get("to_tier") or "high").strip()
    model = (handoff.get("to_model") or "").strip()
    src = (handoff.get("from_tier") or "").strip()
    dest_label = f"{dest}" + (f" ({model})" if model else "")
    src_label = f" from {src}" if src else ""
    lines = [
        f"CONTEXT HANDOFF (model escalation{src_label} → {dest_label}): "
        "the previous model summarised the conversation and its in-flight "
        "task so you can continue without re-reading the full history. "
        "Continue from here.",
        "",
    ]
    for key, label in (
        ("summary", "Conversation summary"),
        ("task_state", "Current task and intent"),
        ("tried_so_far", "Already tried"),
        ("failure_point", "Where it failed"),
        ("next_hypothesis", "Next hypothesis"),
    ):
        value = (handoff.get(key) or "").strip()
        if value:
            lines.append(f"## {label}")
            lines.append(value)
            lines.append("")
    return "\n".join(lines).strip()


class ModelRouterContextEngine(ContextCompressor):
    """Built-in compressor plus a request-scoped escalation handoff."""

    @property
    def name(self) -> str:
        return ENGINE_NAME

    def __init__(self, *args: Any, model: str = "", **kwargs: Any) -> None:
        super().__init__(*args, model=model, **kwargs)
        self.handoff: dict[str, Any] | None = None
        self._handoff_tail_chars: int = _load_tail_chars()

    def select_context(
        self,
        request_messages: list[dict[str, Any]],
        *,
        conversation_messages: list[dict[str, Any]] | None = None,
        incoming_message: dict[str, Any] | None = None,
        budget_tokens: int = 0,
    ) -> list[dict[str, Any]] | None:
        """Replace the request with a compact handoff when an escalation is pending.

        Returns ``None`` (no-op → byte-identical request, cache preserved) when
        no handoff is stashed.
        """
        handoff = self.handoff
        if not handoff:
            return None
        self.handoff = None
        try:
            out: list[dict[str, Any]] = []

            # Preserve the system prompt verbatim — it carries the stable
            # prefix and the (already-switched) Model:/Provider: footer.
            system = request_messages[0] if request_messages else None
            if isinstance(system, dict) and system.get("role") == "system":
                out.append(system)

            out.append({"role": "user", "content": _format_handoff(handoff)})

            # Recent verbatim tail: prefer the live request (includes this
            # turn's tool loop / failure text) over persisted history.
            source = request_messages or conversation_messages or []
            tail: list[dict[str, Any]] = []
            budget = self._handoff_tail_chars
            for msg in reversed(source):
                if not isinstance(msg, dict):
                    continue
                role = msg.get("role")
                if role == "system":
                    continue
                content = msg.get("content", "")
                if isinstance(content, list):
                    content = " ".join(
                        (c.get("text", "") if isinstance(c, dict) else str(c))
                        for c in content
                    )
                if not isinstance(content, str):
                    content = str(content)
                text = content.strip()
                if not text:
                    continue
                tail.insert(0, {"role": role, "content": text})
                budget -= len(text)
                if budget <= 0:
                    break

            out.extend(tail)
            return out
        except Exception:
            # Fail-open: if handoff construction breaks, fall back to the
            # unmodified request rather than breaking the turn.
            return None
