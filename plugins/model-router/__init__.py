"""model-router — per-turn cost routing for Hermes across three named tiers.

Exactly three models: low < medium < high. Models, providers, labels, and
escalation are config — see settings.py and config.default.json. Override via
a plugin-adjacent config.json, a MODEL_ROUTER_CONFIG path, or MODEL_ROUTER_*
env vars.

Policy:
  • Each real user turn is classified once; the work loop stays on that tier
    for the whole multi-tool turn.
  • Multi-sentence / long messages floor at medium (never the cheapest model).
  • Consecutive tool errors climb one tier, capped at escalate_max.
  • Manual /low /medium /high pins pause auto-routing until /auto.
  • Every pre_api_request re-heals a half-switch (model name on the wrong
    API host — e.g. a WebUI credential refresh desync).

No Hermes/WebUI core file edits. Live switch uses AIAgent.switch_model via
the same hermes_cli.model_switch resolver as /model (native providers, not
OpenRouter slugs). Agent capture is deferred so register() cannot circular-
import run_agent. Does not write SOUL.md.
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
_ESCALATE_MAX = _s.ESCALATE_MAX
_ESCALATION_ERRORS = _s.ESCALATION_ERRORS
_SKIP_PLATFORMS = _s.SKIP_PLATFORMS
_PROVIDER_HOSTS = _s.PROVIDER_HOSTS
_CLASSIFIER = _s.CLASSIFIER
_MIN = "low"
_MID = "medium"
_TOP = NAMES[-1]  # "high"


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

_NAME_RE = re.compile(
    r"(?:^|(?<=\s)|(?<=\())/?(low|medium|high)(?:\b|(?=\)))",
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
    r"(low|medium|high)\b"
    r"|(?:use|pin|switch\s+to|run\s+(?:on|at)|please\s+use)\s+"
    r"(low|medium|high)\b)",
    re.IGNORECASE,
)
_SENTENCE_SPLIT_RE = re.compile(r"[.!?]+\s+|\n+")

_lock = threading.Lock()
_live_agents: dict[str, Any] = {}
_last_bound: tuple[str, Any] | None = None
_last_user_sid: str = ""  # session of the most recent real user turn (command anchor)
_pinned: dict[str, bool] = {}
_last_tier: dict[str, str] = {}
_last_msg: dict[str, tuple[str, str]] = {}  # (msg, tier) to skip re-classifying a repeat
_tool_errors: dict[str, int] = {}
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
    return max(1, len(parts))


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


def _apply_tier_ladder(agent: Any, meta: dict[str, Any]) -> None:
    """Replace the live fallback chain with this tier's ladder."""
    chain = list(meta.get("ladder") or [])
    try:
        agent._fallback_chain = chain
        agent._fallback_model = chain[0] if chain else None
        agent._fallback_index = 0
        agent._fallback_activated = False
        unavailable = getattr(agent, "_unavailable_fallback_keys", None)
        if isinstance(unavailable, (set, dict)):
            unavailable.clear()
    except Exception:
        logger.debug("model-router: could not apply tier ladder", exc_info=True)


def _apply_tier(agent: Any, name: str) -> bool:
    meta = MODELS.get(name)
    if not meta or agent is None:
        return False
    model = meta["model"]
    provider = meta["provider"]
    if _same_route(agent, model, provider):
        _apply_tier_ladder(agent, meta)
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

    # Jurisdiction is model/provider only. Session reasoning stays
    # whatever Hermes set. Auxiliary effort is Nix-seeded, not here.

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

    _apply_tier_ladder(agent, meta)
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
        named = as_name(raw)
        if named:
            return named
        logger.warning("model-router: classifier non-name %r — default medium", raw[:40])
    except Exception as exc:
        logger.warning("model-router: classifier failed (%s) — default medium", exc)
    return _MID


def _target_tier(session_id: str, msg: str, history: list) -> tuple[str, str]:
    """Compute (tier, reason) for a real user turn.

    Repeats of the last classified message reuse its tier without a second
    triage call. Multi-sentence or long messages floor at medium.
    """
    with _lock:
        cached = _last_msg.get(session_id)
    if cached is not None and cached[0] == msg:
        return cached[1], "cached"

    body = _strip_platform_prefix(msg)
    words = body.split()
    n_sent = _sentence_count(body)
    explicit = _detect_explicit_tier(msg)
    if explicit is not None:
        name, reason = explicit, "explicit"
    elif _ACK_RE.match(body) and len(words) <= 6:
        name, reason = _MIN, "ack"
    else:
        name = _classify(msg, history)
        reason = "classify"
        # Deterministic floor: multi-sentence work is never the cheapest model.
        if _rank(name) < _rank(_MID) and (n_sent > 1 or len(words) > 12):
            name, reason = _MID, "classify+floor"

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
    return name, reason


def _set_tier(session_id: str, name: str, reason: str) -> None:
    """Record the target tier and switch the live agent onto it."""
    with _lock:
        prev = _last_tier.get(session_id)
        _last_tier[session_id] = name
    if prev != name:
        logger.info("model-router: %s (%s) sid=%s", name, reason, session_id or "-")
    agent = _get_agent(session_id)
    if agent is None:
        logger.warning(
            "model-router: %s (%s) no live agent sid=%s",
            name,
            reason,
            session_id or "-",
        )
        return
    if not _apply_tier(agent, name):
        logger.warning(
            "model-router: %s apply failed sid=%s model=%s provider=%s base=%s",
            name,
            session_id or "-",
            getattr(agent, "model", "") or "-",
            getattr(agent, "provider", "") or "-",
            _agent_base_url(agent) or "-",
        )


def _should_skip(platform: str, kwargs: dict) -> bool:
    plat = (platform or "").strip().lower()
    if plat in _SKIP_PLATFORMS:
        return True
    # subagent = delegate_task children: model is pinned by delegation config.
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
            # A real user turn anchors which session the human is talking in;
            # slash commands resolve against this, not the racy _last_bound.
            with _lock:
                _last_user_sid = sid
        agent = _get_agent(sid)
        if agent is not None and sid:
            bind_agent(sid, agent)

        with _lock:
            pinned = _pinned.get(sid, False)
            current = _last_tier.get(sid)

        if pinned:
            # Still heal half-switch on pinned sessions (WebUI credential refresh).
            if agent is not None:
                _apply_tier(agent, current or _TOP)
            return

        msg = (user_message or "").strip()
        if not msg:
            # Empty hook payload (tool-call continuation) still needs coherence.
            if agent is not None and current:
                _apply_tier(agent, current)
            return

        name, reason = _target_tier(sid, msg, conversation_history or [])
        with _lock:
            _tool_errors[sid] = 0
        _set_tier(sid, name, reason)
    except Exception as exc:
        logger.warning("model-router: on_pre_llm_call error: %s", exc, exc_info=True)


def on_pre_api_request(*, session_id: str = "", platform: str = "", **kwargs: Any) -> None:
    """Re-apply route every API call — WebUI credential_refresh half-switches mid-turn."""
    try:
        if _should_skip(platform, kwargs):
            return
        sid = session_id or ""
        with _lock:
            current = _last_tier.get(sid)

        agent = _get_agent(sid)
        if agent is not None and sid:
            bind_agent(sid, agent)
        if agent is None:
            return

        if current:
            _apply_tier(agent, current)
        else:
            _heal_uncategorized(agent)
    except Exception as exc:
        logger.warning("model-router: on_pre_api_request error: %s", exc, exc_info=True)


def _heal_uncategorized(agent: Any) -> None:
    """No tier classified yet — repair a provider/host half-switch by matching the live model."""
    prov = _norm(getattr(agent, "provider", "") or "")
    base = _agent_base_url(agent)
    if not prov or _base_url_matches_provider(base, prov):
        return
    m = _norm(getattr(agent, "model", "") or "")
    heal = _TOP
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


def on_post_tool_call(
    *,
    tool_name: str = "",
    result: str | None = None,
    session_id: str = "",
    **kwargs: Any,
) -> None:
    """Count consecutive tool errors and climb one tier past the threshold."""
    try:
        sid = session_id or ""
        if not sid:
            return
        with _lock:
            if _pinned.get(sid, False):
                return

        is_error = False
        if result is not None:
            # Hook may pass dict/list/ToolResult — never slice non-str.
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
                or (
                    '"exit_code": ' in head
                    and '"exit_code": 0' not in head
                    and '"exit_code": null' not in head
                )
            ):
                is_error = True

        with _lock:
            if is_error:
                _tool_errors[sid] = _tool_errors.get(sid, 0) + 1
            else:
                _tool_errors[sid] = 0
            count = _tool_errors.get(sid, 0)
            current = _last_tier.get(sid, _MIN)

        threshold = _escalation_threshold(current)
        if is_error and count >= threshold and _rank(current) < _rank(_ESCALATE_MAX):
            _set_tier(sid, _higher(current), f"auto-escalate after {count} tool errors")
            with _lock:
                _tool_errors[sid] = 0
    except Exception as exc:
        logger.warning("model-router: on_post_tool_call error: %s", exc, exc_info=True)


def _resolve_cmd_sid() -> str:
    """Resolve which session a slash command should target.

    Slash commands are dispatched with only raw_args — no session id — so the
    plugin must infer the session. Prefer the session of the most recent real
    user turn (set by on_pre_llm_call), then the last-bound agent, then the
    CLI manager's agent.
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
    logger.info("model-router: /%s pin sid=%s", name, sid or "-")
    _set_tier(sid, name, "pin")
    if agent is not None:
        return (
            f"Pinned to {meta['label']} ({meta['provider']} / {meta['model']}). "
            "Auto-routing paused. /auto to resume."
        )
    return (
        f"Pinned to {meta['label']} ({meta['provider']} / {meta['model']}). "
        "Will apply on the next turn if no live agent was bound."
    )


def _cmd_auto(raw_args: str) -> str:
    del raw_args
    sid = _resolve_cmd_sid()
    with _lock:
        was = _pinned.pop(sid, False)
        # Drop cached tier + message so the next turn is classified fresh,
        # not healed onto the previously pinned model.
        _last_msg.pop(sid, None)
        _last_tier.pop(sid, None)
        _tool_errors.pop(sid, None)
    logger.info("model-router: /auto sid=%s was_pinned=%s", sid or "-", was)
    if was:
        return "Auto routing resumed. Next turn is classified automatically."
    return "Auto routing already active."


def _deferred_install_capture() -> None:
    """Install the AIAgent capture once run_agent finishes importing."""
    for i in range(10):
        try:
            _install_agent_capture()
            logger.info("model-router: AIAgent capture installed (attempt %d)", i + 1)
            return
        except AttributeError:  # run_agent still initializing
            time.sleep(1.0)
        except Exception as exc:
            logger.warning("model-router: AIAgent capture install failed: %s", exc)
            return
    logger.warning(
        "model-router: AIAgent capture NOT installed after retries (run_agent never ready)"
    )


def register(ctx: Any) -> None:
    global _manager
    _manager = getattr(ctx, "_manager", None)
    _attach_file_handler()
    threading.Thread(target=_deferred_install_capture, daemon=True).start()
    ctx.register_hook("pre_llm_call", on_pre_llm_call)
    ctx.register_hook("pre_api_request", on_pre_api_request)
    ctx.register_hook("post_tool_call", on_post_tool_call)
    for name in NAMES:
        meta = MODELS[name]
        label = meta.get("label") or name.capitalize()
        ctx.register_command(
            name,
            lambda args, n=name: _cmd_pin(args, n),
            f"Pin session to {label} ({meta.get('provider')}/{meta.get('model')})",
        )
    ctx.register_command("auto", _cmd_auto, "Resume model-router auto routing")
    labels = " / ".join(f"{n} {MODELS[n].get('label')}" for n in NAMES)
    logger.info(
        "model-router: %s | escalate≤%s | /low /medium /high /auto | no SOUL writes",
        labels,
        _ESCALATE_MAX,
    )
