"""model-router — per-turn cost routing for Hermes across three named tiers.

Exactly three models: low < medium < high. Models, providers, labels, and
escalation are config — see settings.py and config.default.json. Override via
a plugin-adjacent config.json, a MODEL_ROUTER_CONFIG path, or MODEL_ROUTER_*
env vars.

Policy:
  • Each real user turn is classified once; the work loop stays on that tier
    for the whole multi-tool turn.
  • Classifier is 3-way (low/medium/high). High is rare (money /
    irreversible / security). Prefer low on doubt.
  • Consecutive tool errors climb one tier, capped at escalate_max.
  • Manual /low /medium /high pins pause auto-routing until /auto.
  • Slash pins must start the message; a mid-paragraph /high is not a pin.
  • Classifier matrices (`best_for`) are config — Nix / config.json / env.
  • Client rebuilds that pair the live provider with the previous API host
    (WebUI credential_refresh) are refused at `_replace_primary_openai_client`.
    pre_api_request still re-heals if a stomp lands between rebuilds.

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
_HANDOFF_TAIL_CHARS = _s.HANDOFF_TAIL_CHARS
_CLASSIFIER_CONTEXT_CHARS = _s.CLASSIFIER_CONTEXT_CHARS
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
# Slash pin only at the start of the message (after the WebUI workspace prefix).
# Mid-paragraph "/high" in a bug report must not route to grok.
_SLASH_PIN_RE = re.compile(
    r"^/(low|medium|high)\b",
    re.IGNORECASE,
)
# Phrase pins ("pin high", "please use medium") — only honoured on short messages.
_PIN_PHRASE_RE = re.compile(
    r"(?:^|\s)(?:use|pin|switch\s+to|run\s+(?:on|at)|please\s+use)\s+/?"
    r"(low|medium|high)\b",
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
_checkpoint: dict[str, bool] = {}  # session -> escalation checkpoint nudge pending
_good_routes: dict[int, dict[str, str]] = {}
_manager = None
_patched = False

# Injected into the next tool-continuation turn when an escalation checkpoint
# is pending. The working model (not the classifier) decides whether to call
# escalate_model — this is just a nudge.
_CHECKPOINT_NUDGE = (
    "⚠️ Repeated tool failures on this task. If you are stuck, you may call the "
    "`escalate_model` tool to hand off to a stronger model. Provide a summary of "
    "the conversation, the current task and intent, what you already tried, where "
    "it failed (include the exact error), and your best next hypothesis. Otherwise, "
    "continue working normally."
)


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


def _set_agent_base_url(agent: Any, base: str) -> None:
    agent.base_url = base
    kw = getattr(agent, "_client_kwargs", None)
    if isinstance(kw, dict):
        kw = dict(kw)
        kw["base_url"] = base
        agent._client_kwargs = kw


def _agent_api_key(agent: Any) -> str:
    kw = getattr(agent, "_client_kwargs", None)
    if isinstance(kw, dict):
        key = kw.get("api_key")
        if key:
            return str(key)
    return str(getattr(agent, "api_key", "") or "")


def _set_agent_api_key(agent: Any, key: str) -> None:
    agent.api_key = key
    kw = getattr(agent, "_client_kwargs", None)
    if isinstance(kw, dict):
        kw = dict(kw)
        kw["api_key"] = key
        agent._client_kwargs = kw


def _remember_route(agent: Any) -> None:
    """Snapshot a coherent model/provider/host/key for host-stomp repair."""
    if agent is None:
        return
    model = getattr(agent, "model", "") or ""
    provider = getattr(agent, "provider", "") or ""
    base = _agent_base_url(agent)
    if not (model and provider and base):
        return
    if not _base_url_matches_provider(base, provider):
        return
    with _lock:
        _good_routes[id(agent)] = {
            "model": _norm(model),
            "provider": _norm(provider),
            "base_url": base,
            "api_key": _agent_api_key(agent),
        }


def _repair_host_stomp(agent: Any) -> bool:
    """Undo a credential-refresh write that put the live provider on the old host.

    WebUI `_refresh_cached_agent_runtime` copies base_url *and* api_key from
    the request's original kwargs while leaving model/provider. Restore the
    last coherent host+key for this (model, provider) instead of rebuilding
    the client on DeepSeek's URL with the session's xAI key (HTTP 401).
    """
    if agent is None:
        return False
    model = _norm(getattr(agent, "model", "") or "")
    provider = _norm(getattr(agent, "provider", "") or "")
    base = _agent_base_url(agent)
    if not (model and provider):
        return False

    with _lock:
        snap = _good_routes.get(id(agent))
    if not (
        snap
        and snap.get("model") == model
        and snap.get("provider") == provider
        and snap.get("base_url")
        and _norm(snap["base_url"]) != _norm(base)
    ):
        return False
    want = snap["base_url"]
    if not want or _norm(want) == _norm(base):
        return False
    logger.warning(
        "model-router: blocked host stomp model=%s provider=%s was=%s restore=%s",
        getattr(agent, "model", ""),
        getattr(agent, "provider", ""),
        base,
        want,
    )
    _set_agent_base_url(agent, want)
    _set_agent_api_key(agent, snap.get("api_key") or "")
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


def _wrap_cls_method(cls: Any, name: str, factory: Any) -> bool:
    orig = getattr(cls, name, None)
    if orig is None:
        return False
    if getattr(orig, "_model_router_wrapped", False):
        return True
    wrapped = factory(orig)
    wrapped._model_router_wrapped = True  # type: ignore[attr-defined]
    setattr(cls, name, wrapped)
    return True


def _install_agent_capture() -> None:
    global _patched
    try:
        import run_agent
    except Exception as exc:
        logger.warning("model-router: cannot import run_agent for capture: %s", exc)
        return

    cls = run_agent.AIAgent

    def wrap_init(orig):
        def wrapped_init(self, *args, **kwargs):
            orig(self, *args, **kwargs)
            bind_agent(getattr(self, "session_id", None) or "", self)
            _remember_route(self)

        return wrapped_init

    def wrap_run(orig):
        def wrapped_run(self, *args, **kwargs):
            bind_agent(getattr(self, "session_id", None) or "", self)
            return orig(self, *args, **kwargs)

        return wrapped_run

    def wrap_switch(orig):
        def wrapped_switch(self, *args, **kwargs):
            result = orig(self, *args, **kwargs)
            _remember_route(self)
            return result

        return wrapped_switch

    def wrap_replace(orig):
        def wrapped_replace(self, *args, **kwargs):
            repaired = _repair_host_stomp(self)
            prov = getattr(self, "provider", "") or ""
            if not repaired and not _base_url_matches_provider(_agent_base_url(self), prov):
                logger.warning(
                    "model-router: skip client rebuild on unresolved host mismatch "
                    "model=%s provider=%s base_url=%s",
                    getattr(self, "model", ""),
                    prov,
                    _agent_base_url(self),
                )
                # True: WebUI treats False as "rebuild from original kwargs"
                # (the session's xAI host). Keep the live client instead.
                return True
            result = orig(self, *args, **kwargs)
            _remember_route(self)
            return result

        return wrapped_replace

    _wrap_cls_method(cls, "__init__", wrap_init)
    _wrap_cls_method(cls, "run_conversation", wrap_run)
    _wrap_cls_method(cls, "switch_model", wrap_switch)
    if not _wrap_cls_method(cls, "_replace_primary_openai_client", wrap_replace):
        logger.info(
            "model-router: AIAgent has no _replace_primary_openai_client; "
            "host-stomp guard is pre_api_request only"
        )
    _patched = True
    logger.info("model-router: AIAgent capture installed")


def _apply_tier(agent: Any, name: str) -> bool:
    meta = MODELS.get(name)
    if not meta or agent is None:
        return False
    model = meta["model"]
    provider = meta["provider"]
    if _same_route(agent, model, provider):
        _remember_route(agent)
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

    logger.info(
        "model-router: applied %s → %s / %s (base=%s)",
        meta["label"],
        resolved_prov,
        result.new_model,
        live_base or "-",
    )
    _remember_route(agent)
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
    """Honor slash-at-start and short pin phrases — not mid-paragraph /high."""
    text = _strip_platform_prefix(msg)
    m = _SLASH_PIN_RE.match(text)
    if m:
        return as_name(m.group(1))

    words = text.split()
    if len(words) <= 8:
        reqs: set[str] = set()
        for hit in _PIN_PHRASE_RE.finditer(text):
            name = as_name(hit.group(1))
            if name:
                reqs.add(name)
        if reqs:
            return max(reqs, key=_rank)

    mentions: set[str] = set()
    for hit in _NAME_RE.finditer(text):
        name = _token_to_name(*hit.groups())
        if name:
            mentions.add(name)
    if len(mentions) != 1:
        return None
    # Short messages like "medium please" / "high" only.
    if len(words) <= 6:
        return next(iter(mentions))
    return None


def _resolve_tier_runtime(name: str, agent: Any) -> dict[str, str] | None:
    """Resolve (model, provider, base_url, api_key, api_mode) for a tier without switching.

    Reuses the same ``resolve_switch`` path as ``_apply_tier`` so the classifier
    and the escalate tool can address a tier's model/provider directly. Returns
    None on any failure.
    """
    meta = MODELS.get(name)
    if not meta:
        return None
    model = meta["model"]
    provider = meta["provider"]
    try:
        from hermes_cli.config import load_config
        from hermes_cli.model_switch import switch_model as resolve_switch
    except Exception as exc:
        logger.warning("model-router: model_switch import failed: %s", exc)
        return None
    try:
        cfg = load_config() or {}
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
        if not getattr(result, "success", False):
            return None
        base = (getattr(result, "base_url", None) or "").strip()
        prov = getattr(result, "target_provider", None) or provider
        if not base or not _base_url_matches_provider(base, prov):
            return None
        return {
            "model": getattr(result, "new_model", model),
            "provider": prov,
            "base_url": base,
            "api_key": result.api_key or "",
            "api_mode": result.api_mode or "",
        }
    except Exception as exc:
        logger.warning("model-router: resolve %s runtime failed: %s", name, exc)
        return None


def _classify(user_message: str, history: list, session_id: str = "") -> str:
    """Return low|medium|high. Fail-open to low — escalation corrects a miss.

    Runs on the previous turn's tier (never ``high``/grok) with real history so
    the classifier sees the same conversation the previous model already read.
    """
    try:
        from agent.auxiliary_client import call_llm

        with _lock:
            prev = _last_tier.get(session_id, _MID)
        if prev not in (_MIN, _MID):
            prev = _MID

        body = _strip_platform_prefix(user_message)
        messages: list[dict[str, Any]] = [{"role": "system", "content": _CLASSIFIER}]
        ctx_parts: list[str] = []
        budget = _CLASSIFIER_CONTEXT_CHARS
        for msg in reversed(history or []):
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            if role not in ("assistant", "user"):
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
            ctx_parts.insert(0, f"{role}: {text}")
            budget -= len(text)
            if budget <= 0:
                break
        if ctx_parts:
            messages.append(
                {"role": "user", "content": "[Conversation context]\n" + "\n---\n".join(ctx_parts)}
            )
            messages.append({"role": "assistant", "content": "Understood."})
        messages.append({"role": "user", "content": body[:800] or user_message[:800]})

        call_kwargs: dict[str, Any] = {"messages": messages, "max_tokens": 8, "temperature": 0.0}
        rt = _resolve_tier_runtime(prev, _get_agent(session_id))
        if rt and rt.get("base_url"):
            call_kwargs.update(
                model=rt["model"],
                provider=rt["provider"],
                base_url=rt["base_url"],
                api_key=rt["api_key"] or None,
            )
            response = call_llm(**call_kwargs)
        else:
            response = call_llm(task="triage_specifier", **call_kwargs)

        raw = (response.choices[0].message.content or "").strip()
        raw_l = raw.lower()
        allowed = (_MIN, _MID, _TOP)
        found = [n for n in allowed if re.search(rf"\b{n}\b", raw_l)]
        if len(found) == 1:
            return found[0]
        named = as_name(raw)
        if named in allowed:
            return named
        logger.warning("model-router: classifier non-name %r — default low", raw[:40])
    except Exception as exc:
        logger.warning("model-router: classifier failed (%s) — default low", exc)
    return _MIN


def _target_tier(session_id: str, msg: str, history: list) -> tuple[str, str]:
    """Compute (tier, reason) for a real user turn.

    Repeats of the last classified message reuse its tier without a second
    triage call. No sentence/word floor — weighting is the classifier prior.
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
        name = _classify(msg, history, session_id)
        reason = "classify"

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
) -> dict | None:
    try:
        if _should_skip(platform, kwargs):
            return None
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
            checkpoint = _checkpoint.get(sid, False)

        if pinned:
            # Still heal half-switch on pinned sessions (WebUI credential refresh).
            if agent is not None:
                _apply_tier(agent, current or _TOP)
            if checkpoint:
                with _lock:
                    _checkpoint[sid] = False
            return None

        msg = (user_message or "").strip()
        if not msg:
            # Empty hook payload (tool-call continuation): keep the route
            # coherent, and if an escalation checkpoint is pending, nudge the
            # working model toward escalate_model (one-shot).
            if agent is not None and current:
                _apply_tier(agent, current)
            if checkpoint:
                with _lock:
                    _checkpoint[sid] = False
                return {"context": _CHECKPOINT_NUDGE}
            return None

        name, reason = _target_tier(sid, msg, conversation_history or [])
        with _lock:
            _tool_errors[sid] = 0
            _checkpoint[sid] = False
            # Slash/phrase pins arriving as a user message (WebUI buttons send
            # "/high" through the composer) must stick, not one-shot.
            if reason == "explicit":
                _pinned[sid] = True
        _set_tier(sid, name, reason)
        return None
    except Exception as exc:
        logger.warning("model-router: on_pre_llm_call error: %s", exc, exc_info=True)
        return None


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
    heal = _MID
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
    """Count consecutive tool errors; at threshold, stage an escalation checkpoint."""
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
            # Stage an escalation checkpoint instead of climbing directly: the
            # working model decides whether to escalate via escalate_model.
            with _lock:
                _checkpoint[sid] = True
                _tool_errors[sid] = 0
            logger.info(
                "model-router: escalation checkpoint staged sid=%s after %d tool errors (tier=%s)",
                sid,
                count,
                current,
            )
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


ESCALATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": (
        "Escalate to the next-higher model when the current model is stuck. "
        "Provide a structured handoff so the stronger model can continue without "
        "re-reading the full conversation."
    ),
    "properties": {
        "summary": {
            "type": "string",
            "description": "Concise summary of what has been established/decided so far.",
        },
        "task_state": {
            "type": "string",
            "description": "What this turn is trying to accomplish, in detail.",
        },
        "tried_so_far": {
            "type": "string",
            "description": "What approaches were already attempted.",
        },
        "failure_point": {
            "type": "string",
            "description": "Where it is stuck, including the exact error text if any.",
        },
        "next_hypothesis": {
            "type": "string",
            "description": "Best next approach to try on the stronger model (optional).",
        },
    },
    "required": ["summary", "task_state", "failure_point"],
}


def _handle_escalate_model(**kwargs: Any) -> str:
    """Switch to the next tier and stash a structured handoff for the context engine."""
    try:
        sid = str(kwargs.get("session_id") or kwargs.get("task_id") or "").strip()
        if not sid:
            sid = _resolve_cmd_sid()
        agent = _get_agent(sid) if sid else _get_agent("")
        with _lock:
            pinned = _pinned.get(sid, False)
            current = _last_tier.get(sid, _MIN)
        if pinned:
            return "Pinned session — escalation disabled. /auto to resume auto-routing."
        if _rank(current) >= _rank(_ESCALATE_MAX):
            return f"Already at the highest tier ({_ESCALATE_MAX}); nothing to escalate to."

        target = _higher(current)
        dest = MODELS[target]
        handoff = {
            "from_tier": current,
            "to_tier": target,
            "to_model": dest.get("model") or target,
            "summary": str(kwargs.get("summary") or "").strip(),
            "task_state": str(kwargs.get("task_state") or "").strip(),
            "tried_so_far": str(kwargs.get("tried_so_far") or "").strip(),
            "failure_point": str(kwargs.get("failure_point") or "").strip(),
            "next_hypothesis": str(kwargs.get("next_hypothesis") or "").strip(),
        }

        # Stash the handoff on the context engine so select_context swaps the
        # next request to a compact handoff (request-scoped, no history mutation).
        engine = getattr(agent, "context_compressor", None) if agent is not None else None
        if engine is not None and hasattr(engine, "handoff"):
            try:
                engine.handoff = handoff
            except Exception as exc:
                logger.warning("model-router: handoff stash failed: %s", exc)
        else:
            logger.warning(
                "model-router: no handoff engine on agent — escalation falls back to "
                "per-model compaction thresholds only"
            )

        _set_tier(sid, target, "escalate_model")
        with _lock:
            _tool_errors[sid] = 0
            _checkpoint[sid] = False
        meta = MODELS[target]
        return (
            f"Escalated to {meta['label']} ({meta.get('provider')}/{meta.get('model')}). "
            "A handoff summary was prepared so the stronger model can continue without "
            "re-reading the full conversation."
        )
    except Exception as exc:
        logger.warning("model-router: escalate_model failed: %s", exc, exc_info=True)
        return f"escalate_model failed: {exc}"


def _register_escalate_tool(ctx: Any) -> None:
    kwargs = {
        "name": "escalate_model",
        "handler": _handle_escalate_model,
        "schema": ESCALATE_SCHEMA,
        "toolset": "plugin",
        "description": ESCALATE_SCHEMA["description"],
        "emoji": "🪜",
    }
    try:
        import inspect as _inspect

        sig = _inspect.signature(ctx.register_tool)
        params = sig.parameters
        if any(p.kind == _inspect.Parameter.VAR_KEYWORD for p in params.values()):
            ctx.register_tool(**kwargs)
            return
        accepted = {
            name
            for name, p in params.items()
            if p.kind
            in (_inspect.Parameter.POSITIONAL_OR_KEYWORD, _inspect.Parameter.KEYWORD_ONLY)
        }
        ctx.register_tool(**{k: v for k, v in kwargs.items() if k in accepted})
    except TypeError:
        ctx.register_tool(
            name="escalate_model",
            toolset="plugin",
            schema=ESCALATE_SCHEMA,
            handler=_handle_escalate_model,
            description=ESCALATE_SCHEMA["description"],
            emoji="🪜",
        )


def _register_engine(ctx: Any) -> None:
    if not hasattr(ctx, "register_context_engine"):
        logger.warning("model-router: ctx has no register_context_engine; handoff disabled")
        return
    try:
        try:
            from . import engine as _engine
        except ImportError:
            import importlib.util
            from pathlib import Path

            path = Path(__file__).with_name("engine.py")
            spec = importlib.util.spec_from_file_location("model_router_engine", path)
            if spec is None or spec.loader is None:
                raise ImportError(f"cannot load {path}")
            _engine = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(_engine)
        inst = _engine.ModelRouterContextEngine(model="")
        ctx.register_context_engine(inst)
        logger.info("model-router: handoff context engine registered (name=%s)", _engine.ENGINE_NAME)
    except Exception as exc:
        logger.warning("model-router: handoff engine registration failed: %s", exc)


def register(ctx: Any) -> None:
    global _manager
    _manager = getattr(ctx, "_manager", None)
    _attach_file_handler()
    threading.Thread(target=_deferred_install_capture, daemon=True).start()
    ctx.register_hook("pre_llm_call", on_pre_llm_call)
    ctx.register_hook("pre_api_request", on_pre_api_request)
    ctx.register_hook("post_tool_call", on_post_tool_call)
    _register_escalate_tool(ctx)
    _register_engine(ctx)
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
