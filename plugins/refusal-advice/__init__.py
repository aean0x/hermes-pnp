"""refusal-advice — name the gate-clean shape on an unattended approvals refusal.

Every unattended approvals refusal is rendered from one template
(``_Unattended.block_message`` in hermes-agent ``tools/approval.py``) whose advice is
always the same sentence: "Find an alternative approach that avoids this command." A cron
job that preloads no skill gets no further signal, so its next turn repeats the same banned
shape; adding the rule to a loaded skill does not bind the call shape (measured: 21.0 cron
refusals/day before and after two rounds of skill-side carriers).

``transform_tool_result`` is the one surface guaranteed to be in the model's context at the
moment of the mistake, whichever skills the job loaded: the refusal itself. This plugin
appends the alternative the gate does not name, keyed to the detector's own description —
including the hardline floor, whose message is rendered by a different template
(``_hardline_block_result`` in hermes-agent ``tools/approval_floors.py``), carries no advice
about the shape that blocked it, and has no approval path at all.

Message-only. It never changes what the gate blocks, never rewrites a command and never
runs a tool. Pure string work on one result: no IO, no subprocess, no locks. That is a
requirement, not a style choice — ``plugins.hook_callback_timeout: 0`` runs this hook on
the caller thread, and any work that can block belongs in a plugin of its own.
"""
from __future__ import annotations

import json
import logging

log = logging.getLogger("hermes.plugins.refusal_advice")

#: The detector's own refusal prefix; the description follows inside parentheses.
_MARKER = "BLOCKED: Command flagged as dangerous ("

#: Dominant shape: script execution via `-c`/`-e` or a heredoc.
_SCRIPT_FILE_ADVICE = (
    " The gate-clean alternative: write the logic to a script FILE and run"
    " `python3 <path>` (skill cron-output-guide, section Headless tooling). Never `-c`/`-e`"
    " and never a heredoc."
)

#: Pattern matched somewhere raw in the command — commonly inside a quoted payload that
#: only *names* the dangerous text (the staging call that lost its item to this).
_QUOTED_MATCH_ADVICE = (
    " The detector matches raw text anywhere in the command, quoted arguments included. If"
    " this match is inside text you are only passing along, reword the payload so the literal"
    " verb-and-unit pair does not appear (name the unit and say it needs a restart), then"
    " re-send (skill cron-output-guide, section Headless tooling)."
)

#: Anything else the detector can flag: still cheaper to name the shape that is allowed.
_OTHER_ADVICE = (
    " The gate-clean alternative: write the logic to a script FILE and run"
    " `python3 <path>`, or use a plain tool (`read_file`, `search_files`, `jq`, `grep`) for"
    " inspection (skill cron-output-guide, section Headless tooling)."
)

#: Substrings of the detector description → the advice that unblocks that family.
_FAMILY_ADVICE = (
    ("service", _QUOTED_MATCH_ADVICE),
    ("script execution", _SCRIPT_FILE_ADVICE),
    ("shell command", _SCRIPT_FILE_ADVICE),
)

#: The hardline floor's own prefix (``_hardline_block_result``); the description follows it
#: up to the first period. It never contains ``_MARKER``, so the two templates never collide.
_HARDLINE_MARKER = "BLOCKED (hardline): "

#: Hardline families whose dominant false positive is TEXT the command only passes along:
#: every raw newline is a command start, so a heredoc body line that merely begins with the
#: verb (a commit message that wrapped onto "reboot, before the 05:30 reclaim …") blocks the
#: whole call — and the hardline floor has no approval path, so a retry cannot clear it.
_TEXT_PAYLOAD_HARDLINE = ("system shutdown/reboot",)

_TEXT_PAYLOAD_ADVICE = (
    " This match is usually a heredoc BODY line, not a command: the detector treats every raw"
    " newline as a command start and `_mask_quoted_newlines` masks only newlines INSIDE quotes,"
    " so a `<<'MSG'` body is not masked while an `-m \"…\"` argument is. If the text is only"
    " being passed along, write it to a file and use the file form (`git commit -F <path>`)"
    " instead of a heredoc (skill cron-output-guide, section Headless tooling)."
)

#: Any other hardline family: name the property that makes a retry pointless.
_GENERIC_HARDLINE_ADVICE = (
    " The hardline list has no approval path — no flag, mode or `--yolo` clears it — so"
    " re-sending the same command cannot succeed: change the shape of the call"
    " (skill cron-output-guide, section Headless tooling)."
)

#: Families whose block result already appends its own recovery hint; leave those alone.
_SELF_ADVISING_HARDLINE = ("command parser limit", "malformed executable")


def _description(result: str) -> str:
    """The detector description inside the refusal's parentheses, lower-cased."""
    tail = result.split(_MARKER, 1)[1]
    return tail.split(")", 1)[0].strip().lower()


def advice_for(description: str) -> str:
    """The advice to append for one detector description."""
    for needle, advice in _FAMILY_ADVICE:
        if needle in description:
            return advice
    return _OTHER_ADVICE


def _hardline_description(result: str) -> str:
    """The hardline family inside ``BLOCKED (hardline): <description>. …``, lower-cased."""
    tail = result.split(_HARDLINE_MARKER, 1)[1]
    return tail.split(".", 1)[0].strip().lower()


def hardline_advice_for(description: str) -> str | None:
    """The advice to append for one hardline description (``None`` = leave it alone)."""
    if description.startswith(_SELF_ADVISING_HARDLINE):
        return None
    if description.startswith(_TEXT_PAYLOAD_HARDLINE):
        return _TEXT_PAYLOAD_ADVICE
    return _GENERIC_HARDLINE_ADVICE


def advice_for_result(result: str) -> str | None:
    """The advice for a block-result string, from either gate template.

    ``None`` means "append nothing": the string is not a block result, or its family already
    carries advice of its own.
    """
    if _MARKER in result:
        return advice_for(_description(result))
    if _HARDLINE_MARKER in result:
        return hardline_advice_for(_hardline_description(result))
    return None


def _attach_advice(result: str, advice: str) -> str | None:
    """Append *advice* where the model will read it; ``None`` when it is already there.

    The gates hand the model a JSON envelope (``{"output": "", "exit_code": -1, "error": …}``),
    not bare prose, so appending after the closing brace produces a string no parser accepts.
    Inside an envelope the advice joins the ``error``/``message`` field and the object is
    re-serialized; anything else is appended as plain text.
    """
    try:
        payload = json.loads(result)
    except ValueError:
        return None if advice in result else result + advice
    if not isinstance(payload, dict):
        return None if advice in result else result + advice
    for key in ("error", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            if advice in value:
                return None
            payload[key] = value + advice
            return json.dumps(payload, ensure_ascii=False)
    return None


def transform(tool_name: str = "", result=None, **_kwargs):
    """Append the missing alternative to a dangerous-command refusal.

    Returns the rewritten result, or ``None`` to leave it untouched (``transform_tool_result``
    keeps the original on ``None``, and the first string return wins). Idempotent: a result
    that already carries the advice is not rewritten, so a second pass is a no-op.
    """
    try:
        if not isinstance(result, str):
            return None
        advice = advice_for_result(result)
        if not advice:
            return None
        out = _attach_advice(result, advice)
        if out is None:
            return None
        log.debug(
            "refusal-advice: appended gate-clean alternative to a %s refusal",
            tool_name or "tool",
        )
        return out
    except Exception:
        log.exception("refusal-advice: transform failed; leaving the refusal untouched")
        return None


def register(ctx) -> None:
    """Hermes plugin entrypoint."""
    ctx.register_hook("transform_tool_result", transform)
