"""model-router — configurable per-turn cost routing for Hermes.

Exactly three named models: low < medium < high. Models, providers,
labels, final-voice, and rest-on-high are config — see settings.py and
config.default.json. Override via config.json or MODEL_ROUTER_* env.

Policy:
  • Work loop stays on the classified model for the full multi-tool turn.
  • Multi-sentence user messages floor at medium.
  • Tool-error escalation may climb toward high and de-escalate back.
  • End of turn: optional one-shot final-voice polish when still off high.
  • Manual /low /medium /high pins win over auto final-voice.
  • Every pre_api_request re-heals half-switch (model name on the wrong API host).
  • post_llm_call optionally rests on high between turns.

No Hermes/WebUI core file edits. Live switch uses AIAgent.switch_model via
the same hermes_cli.model_switch resolver as /model (native providers, not
OpenRouter slugs). Agent capture is deferred so register() cannot circular-
import run_agent.

Does not write SOUL.md.
"""
from __future__ import annotations

import importlib.util
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("plugins.model-router")


def _import_settings() -> Any:
    try:
        from . import settings as settings_mod

        return settings_mod
    except ImportError:
        path = Path(__file__).resolve().parent / "settings.py"
        spec = importlib.util.spec_from_file_location("_model_router_settings", path)
        if spec is None or spec.loader is None:
            raise
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod


_s = _import_settings()
MODELS = _s.MODELS
NAMES = _s.NAMES
RANK = _s.RANK
as_name = _s.as_name
_FINAL = _s.FINAL
_FINAL_VOICE = _s.FINAL_VOICE
_REST_ON_HIGH = _s.REST_ON_HIGH
_ESCALATE_MAX = _s.ESCALATE_MAX
_ESCALATION_ERRORS = _s.ESCALATION_ERRORS
_SKIP_PLATFORMS = _s.SKIP_PLATFORMS
_PROVIDER_HOSTS = _s.PROVIDER_HOSTS
_CLASSIFIER = _s.CLASSIFIER
_FINAL_VOICE_SYSTEM = _s.FINAL_VOICE_SYSTEM
_MIN = "low"
_MID = "medium"


def _attach_file_handler() -> None:
    """Route routing decisions to their own file so they are greppable in one place.

    Without this, INFO-level routing logs only reach agent.log (per-session) and
    WARNING reaches errors.log; they never appear in gateway.log, which makes
    "did the router actually switch" hard to answer from the container logs.
    """
    if any(isinstance(h, logging.FileHandler) for h in logger.handlers):
        return
    hermes_home = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
    log_dir = os.path.join(hermes_home, "logs")
    os.makedirs(log_dir, exist_ok=True)
    handler = logging.FileHandler(os.path.join(log_dir, "model-router.log"))
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Escalation may climb toward high; post_llm_call de-escalates back
# to the classified work base.


def _rank(name: str) -> int:
    return RANK.get(name, RANK[_MID])


def _higher(name: str) -> str:
    i = _rank(name)
    nxt = NAMES[min(i + 1, len(NAMES) - 1)]
    if _rank(nxt) > _rank(_ESCALATE_MAX):
        return _ESCALATE_MAX
    return nxt


def _escalation_threshold(name: str) -> int:
    return _ESCALATION_ERRORS.get(name, 3)


# A real tool error has a non-empty "error" value or "failed": true.
# Successful results carry "error": null / "" or "failed": false and must not
# count toward escalation, otherwise two clean tool calls false-escalate.
_ERROR_PAT = re.compile(r'"(?:error|failed)"\s*:\s*(?!\s*null\b)(?!\s*false\b)(?!\s*"")')

# subagent = delegate_task children: model is pinned by the delegation
# config; their output is intermediate work, not a user-facing reply.

_NAME_RE = re.compile(
    r"(?:^|(?<=\s)|(?<=\())(?:/?(low|medium|high)|/?t([123]))(?:\b|(?=\)))",
    re.IGNORECASE,
)
_TIER_WORD_RE = re.compile(
    r"(?:^|(?<=\s)|(?<=\())tier\s*([123])",
    re.IGNORECASE,
)
_ACK_RE = re.compile(
    r"^(ok|okay|thanks|thank you|thx|got it|understood|sure|yes|no|yep|nope|"
    r"alright|cool|great|nice|perfect|done|noted|ack|hello|hi|hey)"
    r"[!?.]*$",
    re.IGNORECASE,
)
# WebUI prefixes every user turn; strip before ack/length/sentence heuristics.
_WEBUI_WORKSPACE_RE = re.compile(
    r"^\[Workspace::v1:\s*[^\]]+\]\s*",
    re.IGNORECASE,
)
# Explicit pin-style requests only — bare "low" inside a long critique is NOT a pin.
_EXPLICIT_REQ_RE = re.compile(
    r"(?:^|\s)(?:/"
    r"(?:(low|medium|high)|t([123]))\b"
    r"|(?:use|pin|switch\s+to|run\s+(?:on|at)|please\s+use)\s+"
    r"(?:(low|medium|high)|t([123]))\b)",
    re.IGNORECASE,
)
_SENTENCE_SPLIT_RE = re.compile(r"[.!?]+\s+|\n+")

_lock = threading.Lock()
_live_agents: dict[str, Any] = {}
_last_bound: tuple[str, Any] | None = None
_last_user_sid: str = ""  # session of the most recent real user turn (command anchor)
_pinned: dict[str, bool] = {}
_last_tier: dict[str, str] = {}
_base_tier: dict[str, str] = {}
_last_msg: dict[str, tuple[str, str]] = {}
_tool_errors: dict[str, int] = {}
_escalated: dict[str, bool] = {}
_pending: dict[str, str] = {}  # classified but not yet applied
_tools_this_turn: dict[str, int] = {}
_user_msg: dict[str, str] = {}  # original user message for final-voice polish
_ack_turn: dict[str, bool] = {}
_manager = None
_patched = False


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def _agent_base_url(agent: Any) -> str:
    """Prefer live client kwargs base — WebUI credential refresh can desync attrs."""
    if agent is None:
        return ""
    kw = getattr(agent, "_client_kwargs", None) or {}
    if isinstance(kw, dict):
        b = (kw.get("base_url") or "").strip()
        if b:
            return b
    return (getattr(agent, "base_url", "") or "").strip()


def _strip_platform_prefix(msg: str) -> str:
    return _WEBUI_WORKSPACE_RE.sub("", (msg or "").strip()).strip()


def _sentence_count(msg: str) -> int:
    text = _strip_platform_prefix(msg)
    if not text:
        return 0
    parts = [p.strip() for p in _SENTENCE_SPLIT_RE.split(text) if p.strip()]
    # No terminator still counts as one sentence if there is content.
    return max(1, len(parts)) if text else 0


def _base_url_matches_provider(base_url: str, provider: str) -> bool:
    """Detect half-switched agents (model name set, still on previous API host)."""
    base = _norm(base_url)
    prov = _norm(provider)
    if not base or not prov:
        return True  # unknown — let switch_model decide
    spec = _PROVIDER_HOSTS.get(prov)
    if not spec:
        return True
    forbid = spec.get("forbid") or []
    prefer = spec.get("prefer") or []
    if any(token in base for token in forbid):
        return False
    if prefer and any(token in base for token in prefer):
        return True
    return True


def _same_route(agent: Any, model: str, provider: str) -> bool:
    if _norm(getattr(agent, "model", "")) != _norm(model):
        return False
    if _norm(getattr(agent, "provider", "")) != _norm(provider):
        return False
    base = _agent_base_url(agent)
    if not _base_url_matches_provider(base, provider):
        logger.warning(
            "model-router: half-switch detected model=%s provider=%s base_url=%s — re-applying",
            getattr(agent, "model", ""),
            getattr(agent, "provider", ""),
            base,
        )
        return False
    return True


def bind_agent(session_id: str, agent: Any) -> None:
    if agent is None:
        return
    global _last_bound
    sid = session_id or getattr(agent, "session_id", None) or ""
    with _lock:
        if sid:
            _live_agents[sid] = agent
        _last_bound = (sid, agent)


def _get_agent(session_id: str = "") -> Any | None:
    sid = session_id or ""
    with _lock:
        if sid and sid in _live_agents:
            return _live_agents[sid]
        bound = _last_bound
        live_items = list(_live_agents.items())

    def _ok(agent: Any) -> bool:
        if agent is None:
            return False
        if not sid:
            return True
        agent_sid = getattr(agent, "session_id", "") or ""
        return (not agent_sid) or agent_sid == sid

    if _manager is not None:
        try:
            cli = getattr(_manager, "_cli_ref", None)
            agent = getattr(cli, "agent", None) if cli else None
            if _ok(agent):
                bind_agent(sid, agent)
                return agent
        except Exception:
            pass

    if bound is not None and _ok(bound[1]):
        bind_agent(sid, bound[1])
        return bound[1]

    for mapped_sid, agent in live_items:
        if _ok(agent):
            bind_agent(sid or mapped_sid, agent)
            return agent
    return None


def _install_agent_capture() -> None:
    global _patched
    if _patched:
        return
    try:
        import run_agent
    except Exception as exc:
        logger.warning("model-router: cannot import run_agent for capture: %s", exc)
        return

    orig_init = run_agent.AIAgent.__init__
    if getattr(orig_init, "_model_router_wrapped", False):
        _patched = True
        return

    def wrapped_init(self, *args, **kwargs):
        orig_init(self, *args, **kwargs)
        bind_agent(getattr(self, "session_id", None) or "", self)

    wrapped_init._model_router_wrapped = True  # type: ignore[attr-defined]
    run_agent.AIAgent.__init__ = wrapped_init  # type: ignore[method-assign]

    orig_run = run_agent.AIAgent.run_conversation

    def wrapped_run(self, *args, **kwargs):
        bind_agent(getattr(self, "session_id", None) or "", self)
        return orig_run(self, *args, **kwargs)

    run_agent.AIAgent.run_conversation = wrapped_run  # type: ignore[method-assign]
    _patched = True
    logger.info("model-router: AIAgent capture installed")


def _apply_tier(agent: Any, name: str) -> bool:
    meta = MODELS.get(name)
    if not meta or agent is None:
        return False
    model = meta["model"]
    provider = meta["provider"]
    if _same_route(agent, model, provider):
        return True
    try:
        from hermes_cli.config import load_config
        from hermes_cli.model_switch import switch_model as resolve_switch
    except Exception as exc:
        logger.warning("model-router: model_switch import failed: %s", exc)
        return False

    try:
        cfg = load_config() or {}
        # When half-switched (model name on the wrong API host), pass a
        # neutral current_base_url so resolve does not inherit the old host.
        cur_base = _agent_base_url(agent)
        cur_prov = getattr(agent, "provider", "") or ""
        if not _base_url_matches_provider(cur_base, provider):
            cur_base = ""
            cur_prov = provider
        result = resolve_switch(
            raw_input=model,
            current_provider=cur_prov,
            current_model=getattr(agent, "model", "") or "",
            current_base_url=cur_base,
            current_api_key=getattr(agent, "api_key", "") or "",
            is_global=False,
            explicit_provider=provider,
            user_providers=cfg.get("providers"),
            custom_providers=cfg.get("custom_providers"),
        )
    except Exception as exc:
        logger.warning("model-router: resolve %s failed: %s", name, exc)
        return False

    if not getattr(result, "success", False):
        logger.warning(
            "model-router: resolve %s failed: %s",
            name,
            getattr(result, "error_message", "unknown"),
        )
        return False

    resolved_base = (getattr(result, "base_url", None) or "").strip()
    resolved_prov = getattr(result, "target_provider", None) or provider
    if not resolved_base:
        logger.warning(
            "model-router: resolve %s returned empty base_url for %s/%s",
            name,
            resolved_prov,
            getattr(result, "new_model", model),
        )
        return False
    if not _base_url_matches_provider(resolved_base, resolved_prov):
        logger.warning(
            "model-router: resolve %s host mismatch provider=%s base_url=%s",
            name,
            resolved_prov,
            resolved_base,
        )
        return False

    try:
        agent.switch_model(
            result.new_model,
            resolved_prov,
            result.api_key or "",
            resolved_base,
            result.api_mode or "",
        )
    except Exception as exc:
        logger.warning("model-router: switch_model %s failed: %s", name, exc)
        return False

    # switch_model does not touch reasoning_config. High-model effort
    # must not leak onto cheaper slots (wasted tokens or provider 400s).
    if name != _FINAL:
        agent.reasoning_config = None

    # Prefer agent attributes; fall back to what we just applied.
    live_model = getattr(agent, "model", "") or result.new_model
    live_prov = getattr(agent, "provider", "") or resolved_prov
    live_base = getattr(agent, "base_url", "") or resolved_base
    # Some hermes builds keep a nested client; best-effort read.
    try:
        client = getattr(agent, "client", None) or getattr(agent, "_client", None)
        client_base = getattr(client, "base_url", None) if client is not None else None
        if client_base is not None:
            live_base = str(client_base) or live_base
    except Exception:
        pass

    if _norm(live_model) != _norm(result.new_model) or _norm(live_prov) != _norm(
        resolved_prov
    ):
        logger.warning(
            "model-router: post-switch attrs mismatch want=%s/%s got=%s/%s",
            resolved_prov,
            result.new_model,
            live_prov,
            live_model,
        )
        return False
    if not _base_url_matches_provider(live_base, resolved_prov):
        logger.warning(
            "model-router: post-switch base_url still wrong for %s: provider=%s base_url=%s",
            name,
            resolved_prov,
            live_base,
        )
        return False

    logger.info(
        "model-router: applied %s → %s / %s (base=%s)",
        meta["label"],
        resolved_prov,
        result.new_model,
        live_base or "-",
    )
    return True


def _token_to_name(*parts: str | None) -> str | None:
    for part in parts:
        if not part:
            continue
        name = as_name(part)
        if name:
            return name
    return None


def _detect_explicit_tier(msg: str) -> str | None:
    """Only honor pin-style or short single-model requests — not meta discussion."""
    text = _strip_platform_prefix(msg)
    reqs: set[str] = set()
    for m in _EXPLICIT_REQ_RE.finditer(text):
        name = _token_to_name(*m.groups())
        if name:
            reqs.add(name)
    if reqs:
        return max(reqs, key=_rank)

    mentions: set[str] = set()
    for m in _NAME_RE.finditer(text):
        name = _token_to_name(*m.groups())
        if name:
            mentions.add(name)
    for m in _TIER_WORD_RE.finditer(text):
        name = as_name(m.group(1))
        if name:
            mentions.add(name)
    if len(mentions) != 1:
        return None
    # Short messages like "medium please" / "high" only.
    words = text.split()
    if len(words) <= 6:
        return next(iter(mentions))
    return None


def _classify(user_message: str, history: list) -> str:
    """Return low|medium|high. Fail-open to medium — never silent low on errors."""
    try:
        from agent.auxiliary_client import call_llm

        context_turns = []
        n = 0
        for msg in reversed(history or []):
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                content = msg.get("content", "")
                if isinstance(content, str) and content.strip():
                    context_turns.insert(0, content[:300])
                    n += 1
                    if n >= 2:
                        break
        messages = [{"role": "system", "content": _CLASSIFIER}]
        if context_turns:
            messages.append(
                {
                    "role": "user",
                    "content": "[Recent assistant context]\n" + "\n---\n".join(context_turns),
                }
            )
            messages.append({"role": "assistant", "content": "Understood."})
        payload = _strip_platform_prefix(user_message)[:800] or user_message[:800]
        messages.append({"role": "user", "content": payload})
        response = call_llm(
            task="triage_specifier",
            messages=messages,
            max_tokens=8,
            temperature=0.0,
        )
        raw = (response.choices[0].message.content or "").strip()
        raw_l = raw.lower()
        found = [n for n in NAMES if re.search(rf"\b{n}\b", raw_l)]
        if len(found) == 1:
            return found[0]
        hidden = as_name(raw)
        if hidden:
            return hidden
        digit = re.search(r"\b[tT]?([123])\b", raw)
        if digit:
            mapped = as_name(digit.group(1))
            if mapped:
                return mapped
        logger.warning("model-router: classifier non-name %r — default medium", raw[:40])
    except Exception as exc:
        logger.warning("model-router: classifier failed (%s) — default medium", exc)
    return _MID


def _target_tier(session_id: str, msg: str, history: list) -> str:
    with _lock:
        cached = _last_msg.get(session_id)
        is_new = cached is None or cached[0] != msg
    if not is_new:
        with _lock:
            return _last_tier.get(session_id, _MID)

    with _lock:
        _tool_errors[session_id] = 0
        _escalated[session_id] = False
        _tools_this_turn[session_id] = 0
        _user_msg[session_id] = msg
        _ack_turn[session_id] = False

    body = _strip_platform_prefix(msg)
    words = body.split()
    n_sent = _sentence_count(body)
    is_ack = bool(_ACK_RE.match(body) and len(words) <= 6)
    explicit = _detect_explicit_tier(msg)
    reason = "classify"
    if explicit is not None:
        name = explicit
        reason = "explicit"
    elif is_ack:
        name = _MIN
        reason = "ack"
    else:
        name = _classify(msg, history)
        reason = "classify"
        # Deterministic floor: multi-sentence work is never the cheapest model.
        if _rank(name) < _rank(_MID) and n_sent > 1:
            name = _MID
            reason = "classify+multi_sentence_floor"
        elif _rank(name) < _rank(_MID) and len(words) > 12:
            name = _MID
            reason = "classify+length_floor"

    logger.info(
        "model-router: route %s (%s) words=%d sentences=%d preview=%r",
        name,
        reason,
        len(words),
        n_sent,
        body[:120],
    )

    with _lock:
        _last_msg[session_id] = (msg, name)
        _base_tier[session_id] = name
        _last_tier[session_id] = name
        _pending[session_id] = name
        _ack_turn[session_id] = is_ack and explicit is None
    return name


def _is_final_model(model: str | None) -> bool:
    """True if *model* looks like the configured final/voice model."""
    m = _norm(model or "")
    if not m:
        return False
    final = MODELS.get(_FINAL) or {}
    want = _norm(str(final.get("model") or ""))
    if want and (m == want or m.endswith("/" + want) or want in m):
        return True
    return False


def _force_tier(session_id: str, name: str, reason: str) -> None:
    """Apply model and bookkeep; mark escalated when climbing above base."""
    with _lock:
        base = _base_tier.get(session_id, name)
        prev = _last_tier.get(session_id, base)
        if _rank(name) > _rank(base):
            _escalated[session_id] = True
        _last_tier[session_id] = name
        _pending[session_id] = name
    if name != prev:
        logger.info(
            "model-router: force %s (was %s → %s) — %s",
            MODELS[name]["label"],
            prev,
            name,
            reason,
        )
    agent = _get_agent(session_id)
    if agent is not None and _apply_tier(agent, name):
        with _lock:
            _pending.pop(session_id, None)


def _extract_call_llm_text(response: Any) -> str:
    try:
        choices = getattr(response, "choices", None) or []
        if choices:
            msg = getattr(choices[0], "message", None)
            content = getattr(msg, "content", None) if msg is not None else None
            if isinstance(content, str) and content.strip():
                return content.strip()
            if isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(str(block.get("text") or ""))
                    elif isinstance(block, str):
                        parts.append(block)
                joined = "".join(parts).strip()
                if joined:
                    return joined
    except Exception:
        pass
    if isinstance(response, dict):
        try:
            return (
                response["choices"][0]["message"]["content"] or ""
            ).strip()
        except Exception:
            pass
    return ""


def _final_voice_polish(session_id: str, draft: str, model_hint: str = "") -> str | None:
    """One-shot rewrite so the user-facing reply uses the configured voice tier.

    Runs at end of turn (transform_llm_output) when the work loop stayed on a
    cheaper tier. Skips acks, manual pins below the final tier, and turns
    already produced on the voice model.
    """
    if not _FINAL_VOICE:
        return None
    draft = (draft or "").strip()
    if not draft:
        return None
    with _lock:
        user_msg = _user_msg.get(session_id, "")
        is_ack = _ack_turn.get(session_id, False)
        pinned = _pinned.get(session_id, False)
        last = _last_tier.get(session_id, _MIN)
        base = _base_tier.get(session_id, last)
    if pinned and _rank(last) < _rank(_FINAL):
        logger.info("model-router: final voice skip (pinned %s)", last)
        return None
    if is_ack:
        return None

    agent = _get_agent(session_id)
    live_model = getattr(agent, "model", "") if agent is not None else ""
    # Prefer hook model (what produced the draft), fall back to live agent.
    draft_model = model_hint or live_model
    if _is_final_model(draft_model) or _is_final_model(live_model):
        logger.info("model-router: final voice skip (already voice model=%s)", draft_model or live_model)
        return None
    # Classifier/escalation already on the final model.
    if _rank(last) >= _rank(_FINAL) and _rank(base) >= _rank(_FINAL):
        logger.info("model-router: final voice skip (base high)")
        return None

    try:
        from agent.auxiliary_client import call_llm

        meta = MODELS[_FINAL]
        response = call_llm(
            provider=meta["provider"],
            model=meta["model"],
            messages=[
                {"role": "system", "content": _FINAL_VOICE_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"User request:\n{(user_msg or '')[:2000]}\n\n"
                        f"Draft reply:\n{draft[:12000]}"
                    ),
                },
            ],
            temperature=0.2,
            max_tokens=min(8192, max(512, len(draft) // 2 + 800)),
        )
        polished = _extract_call_llm_text(response)
        if not polished:
            logger.warning("model-router: final voice polish returned empty")
            return None
        if polished == draft:
            logger.info(
                "model-router: final voice polish no-op (%d chars)",
                len(draft),
            )
            # Still mark final tier so bookkeeping matches the voice path.
            with _lock:
                _last_tier[session_id] = _FINAL
            return None
        logger.info(
            "model-router: final voice polish (%d→%d chars) work=%s",
            len(draft),
            len(polished),
            base if base else last,
        )
        with _lock:
            _last_tier[session_id] = _FINAL
        if agent is not None:
            _apply_tier(agent, _FINAL)
        return polished
    except Exception as exc:
        logger.warning("model-router: final voice polish failed: %s", exc)
    return None


def _should_skip(platform: str, kwargs: dict) -> bool:
    plat = (platform or "").strip().lower()
    if plat in _SKIP_PLATFORMS:
        return True
    parent = kwargs.get("parent_session_id") or ""
    if parent:
        return True
    return False


def on_pre_llm_call(
    *,
    user_message: str = "",
    conversation_history: list | None = None,
    model: str = "",
    session_id: str = "",
    platform: str = "",
    **kwargs: Any,
) -> None:
    try:
        if _should_skip(platform, kwargs):
            return
        sid = session_id or ""
        if (user_message or "").strip():
            # A real user turn (not a tool-call continuation) anchors which
            # session the human is talking in. Slash commands (/low /medium
            # /high /auto) are dispatched without session context, so they
            # resolve against this instead of the racy _last_bound global.
            with _lock:
                _last_user_sid = sid
        agent = _get_agent(sid)
        if agent is not None and sid:
            bind_agent(sid, agent)

        with _lock:
            pinned = _pinned.get(sid, False)
        if pinned:
            # Still heal half-switch on pinned sessions (WebUI credential refresh).
            if agent is not None:
                with _lock:
                    name = _last_tier.get(sid) or _base_tier.get(sid) or _FINAL
                _apply_tier(agent, name)
            logger.debug("model-router: pinned session %s — skip classify (model=%s)", sid or "-", _last_tier.get(sid, "-"))
            return

        msg = (user_message or "").strip()
        if not msg:
            # Empty hook payload still needs host/model coherence repair.
            if agent is not None:
                with _lock:
                    name = _pending.get(sid) or _last_tier.get(sid) or _FINAL
                _apply_tier(agent, name)
            return

        name = _target_tier(sid, msg, conversation_history or [])
        agent = _get_agent(sid)
        if agent is None:
            logger.warning(
                "model-router: %s classified, no live agent sid=%s — first call may stay on default",
                name,
                sid or "-",
            )
            return
        if _apply_tier(agent, name):
            with _lock:
                _pending.pop(sid, None)
        else:
            logger.warning(
                "model-router: %s apply failed sid=%s model=%s provider=%s base=%s",
                name,
                sid or "-",
                getattr(agent, "model", "") or "-",
                getattr(agent, "provider", "") or "-",
                _agent_base_url(agent) or "-",
            )
    except Exception as exc:
        logger.warning("model-router: on_pre_llm_call error: %s", exc, exc_info=True)


def on_pre_api_request(*, session_id: str = "", platform: str = "", **kwargs: Any) -> None:
    """Re-apply route every API call — WebUI credential_refresh half-switches mid-turn.

    Does NOT force the voice tier after tools — work loop stays on the
    classified tier; final voice is transform_llm_output polish at end of turn.
    """
    try:
        if _should_skip(platform, kwargs):
            return
        sid = session_id or ""
        with _lock:
            pinned = _pinned.get(sid, False)
            pending = _pending.get(sid)
            current = _last_tier.get(sid)

        agent = _get_agent(sid)
        if agent is not None and sid:
            bind_agent(sid, agent)

        if pinned:
            if agent is not None and current:
                _apply_tier(agent, current)
            return

        target = pending or current
        if not target:
            # No classification yet — still heal provider/host mismatches.
            if agent is not None:
                prov = _norm(getattr(agent, "provider", "") or "")
                base = _agent_base_url(agent)
                if prov and not _base_url_matches_provider(base, prov):
                    m = _norm(getattr(agent, "model", "") or "")
                    heal = _FINAL
                    for n, meta in MODELS.items():
                        want = _norm(str(meta.get("model") or ""))
                        if want and (m == want or want in m):
                            heal = n
                            break
                    logger.warning(
                        "model-router: uncategorized half-switch heal→%s model=%s base=%s",
                        heal,
                        m,
                        base,
                    )
                    _apply_tier(agent, heal)
            return
        if agent is None:
            return
        if _apply_tier(agent, target):
            with _lock:
                _pending.pop(sid, None)
    except Exception as exc:
        logger.warning("model-router: on_pre_api_request error: %s", exc, exc_info=True)


def on_post_tool_call(
    *,
    tool_name: str = "",
    result: str | None = None,
    session_id: str = "",
    **kwargs: Any,
) -> None:
    """Count tools + error-escalate. Does not force the voice tier on success."""
    try:
        sid = session_id or ""
        if not sid:
            return
        with _lock:
            if _pinned.get(sid, False):
                return
            _tools_this_turn[sid] = _tools_this_turn.get(sid, 0) + 1

        is_error = False
        if result is not None:
            # Hook may pass dict/list/ToolResult — never slice non-str (TypeError: unhashable slice).
            if isinstance(result, str):
                head_src = result
            else:
                try:
                    import json as _json
                    head_src = _json.dumps(result, default=str)
                except Exception:
                    head_src = str(result)
            head = head_src[:500].lower()
            if (
                _ERROR_PAT.search(head)
                or head_src.startswith("Error")
                or ('"exit_code": ' in head and '"exit_code": 0' not in head and '"exit_code": null' not in head)
            ):
                is_error = True

        with _lock:
            if is_error:
                _tool_errors[sid] = _tool_errors.get(sid, 0) + 1
            else:
                _tool_errors[sid] = 0
            count = _tool_errors.get(sid, 0)
            current = _last_tier.get(sid, _MIN)

        # Tool-error escalation may climb toward high. Threshold is
        # per current model so cheaper slots can burn more retries first.
        threshold = _escalation_threshold(current)
        if is_error and count >= threshold and _rank(current) < _rank(_ESCALATE_MAX):
            new_name = _higher(current)
            _force_tier(
                sid,
                new_name,
                f"auto-escalate after {count} tool errors (need {threshold} on {current})",
            )
            with _lock:
                _tool_errors[sid] = 0
            logger.info(
                "model-router: auto-escalate %s→%s after %d tool errors (need %d)",
                current,
                new_name,
                count,
                threshold,
            )
    except Exception as exc:
        logger.warning("model-router: on_post_tool_call error: %s", exc, exc_info=True)


def on_transform_llm_output(
    *,
    response_text: str = "",
    session_id: str = "",
    model: str = "",
    platform: str = "",
    **kwargs: Any,
) -> str | None:
    """If the turn still ends off the voice tier, polish once onto that voice."""
    try:
        if _should_skip(platform, kwargs):
            return None
        sid = session_id or ""
        if not sid or not (response_text or "").strip():
            return None
        return _final_voice_polish(sid, response_text, model_hint=model)
    except Exception as exc:
        logger.warning("model-router: on_transform_llm_output error: %s", exc, exc_info=True)
        return None


def on_post_llm_call(*, session_id: str = "", model: str = "", **kwargs: Any) -> None:
    try:
        sid = session_id or ""
        agent = _get_agent(sid)
        if agent is None:
            return
        with _lock:
            if _pinned.get(sid, False):
                # Leave pin alone; clear per-turn counters.
                _tools_this_turn[sid] = 0
                return
            was = _escalated.get(sid, False)
            base = _base_tier.get(sid, _MIN)
            current = _last_tier.get(sid, _MIN)
            _tools_this_turn[sid] = 0

        # De-escalate bookkeeping back to work base, then optionally rest
        # on high so the default lineage stays there.
        if was and _rank(current) > _rank(base):
            with _lock:
                _escalated[sid] = False
                _last_tier[sid] = base
                _pending[sid] = base
            logger.info(
                "model-router: de-escalate %s→%s (base)%s",
                current,
                base,
                ", then rest on high" if _REST_ON_HIGH else "",
            )

        # Next pre_llm_call re-classifies and downgrades for work.
        if _REST_ON_HIGH:
            with _lock:
                _last_tier[sid] = _FINAL
                _pending[sid] = _FINAL
            if _apply_tier(agent, _FINAL):
                with _lock:
                    _pending.pop(sid, None)
    except Exception as exc:
        logger.warning("model-router: on_post_llm_call error: %s", exc, exc_info=True)


def _resolve_cmd_sid() -> str:
    """Resolve which session a slash command should target.

    Slash commands are dispatched with only ``raw_args`` — no session id —
    so the plugin must infer the session. Prefer the session of the most
    recent real user turn (set by on_pre_llm_call), then fall back to the
    last-bound agent, then the CLI manager's agent.
    """
    with _lock:
        if _last_user_sid:
            return _last_user_sid
        if _last_bound is not None and _last_bound[0]:
            return _last_bound[0]
    agent = _get_agent("")
    if agent is not None:
        return getattr(agent, "session_id", "") or ""
    return ""


def _cmd_pin(raw_args: str, name: str) -> str:
    del raw_args
    sid = _resolve_cmd_sid()
    agent = _get_agent(sid) if sid else _get_agent("")
    if agent is not None and sid:
        bind_agent(sid, agent)
    meta = MODELS[name]
    with _lock:
        _pinned[sid] = True
        _last_tier[sid] = name
        _base_tier[sid] = name
        _pending[sid] = name
        _ack_turn[sid] = False
    logger.info("model-router: /%s pin sid=%s", name, sid or "-")
    if agent is not None and _apply_tier(agent, name):
        with _lock:
            _pending.pop(sid, None)
        return f"Pinned to {meta['label']} ({meta['provider']} / {meta['model']}). Auto-routing paused. /auto to resume."
    return (
        f"Pinned to {meta['label']} ({meta['provider']} / {meta['model']}). "
        "Will apply on the next turn if no live agent was bound."
    )


def _cmd_auto(raw_args: str) -> str:
    del raw_args
    sid = _resolve_cmd_sid()
    with _lock:
        was = _pinned.pop(sid, False)
        _last_msg.pop(sid, None)
        _ack_turn.pop(sid, None)
        _tools_this_turn.pop(sid, None)
        _escalated.pop(sid, None)
        _base_tier.pop(sid, None)
        _pending.pop(sid, None)
        _user_msg.pop(sid, None)
        # Drop the cached tier so the next turn is classified fresh, not
        # healed onto the previously pinned model.
        _last_tier.pop(sid, None)
    logger.info("model-router: /auto sid=%s was_pinned=%s", sid or "-", was)
    if was:
        return "Auto routing resumed. Next turn is classified automatically."
    return "Auto routing already active."


def _deferred_install_capture() -> None:
    """Install the AIAgent capture once run_agent finishes importing."""
    for _ in range(10):
        try:
            _install_agent_capture()
            logger.info("model-router: AIAgent capture installed (attempt %d)", _ + 1)
            return
        except AttributeError:  # run_agent still initializing
            time.sleep(1.0)
        except Exception as exc:
            logger.warning("model-router: AIAgent capture install failed: %s", exc)
            return
    logger.warning("model-router: AIAgent capture NOT installed after retries (run_agent never ready)")


def register(ctx: Any) -> None:
    global _manager
    _manager = getattr(ctx, "_manager", None)
    _attach_file_handler()
    threading.Thread(target=_deferred_install_capture, daemon=True).start()
    ctx.register_hook("pre_llm_call", on_pre_llm_call)
    ctx.register_hook("pre_api_request", on_pre_api_request)
    ctx.register_hook("post_tool_call", on_post_tool_call)
    ctx.register_hook("transform_llm_output", on_transform_llm_output)
    ctx.register_hook("post_llm_call", on_post_llm_call)
    for name in NAMES:
        meta = MODELS[name]
        label = meta.get("label") or name.capitalize()
        ctx.register_command(
            name,
            lambda args, n=name: _cmd_pin(args, n),
            f"Pin session to {label} ({meta.get('provider')}/{meta.get('model')})",
        )
    # Hidden aliases for leftover /t1 /t2 /t3 muscle-memory. Not documented.
    for alias, name in (("t1", "low"), ("t2", "medium"), ("t3", "high")):
        ctx.register_command(alias, lambda args, n=name: _cmd_pin(args, n), "")
    ctx.register_command("auto", _cmd_auto, "Resume model-router auto routing")
    labels = " / ".join(f"{n} {MODELS[n].get('label')}" for n in NAMES)
    logger.info(
        "model-router: %s | work-loop=classify | final_voice=%s rest=%s | "
        "escalate≤%s | /low /medium /high /auto | no SOUL writes",
        labels,
        _FINAL_VOICE,
        _REST_ON_HIGH,
        _ESCALATE_MAX,
    )
