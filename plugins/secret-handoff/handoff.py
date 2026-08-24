"""Ephemeral login paste: clarify, inject via CDP, return status only.

Same-process gateway, WebUI, and CLI. Pending metadata lives in module-level
RAM only. The tool asks via stock clarify, extracts the reply, injects it
over a direct CDP websocket, and returns {status, service, detail} — never
the secret.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import threading
import time
from typing import Any, Callable, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger("hermes.plugins.secret_handoff")

DEFAULT_CDP_URL = "http://127.0.0.1:9222"
TOOL_TIMEOUT_S = 300.0
_CDP_TIMEOUT_S = 8.0

_CANCEL_REPLIES = frozenset({"cancel", "n", "no"})

# session_key → metadata (never the secret)
_pending: dict[str, dict[str, Any]] = {}
_lock = threading.RLock()


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested without Hermes)
# ---------------------------------------------------------------------------


def classify_reply(text: Optional[str]) -> str:
    """Classify inbound clarify text: inject | cancel | ignore."""
    stripped = "" if text is None else str(text).strip()
    if stripped.startswith("/"):
        return "ignore"
    if not stripped or stripped.casefold() in _CANCEL_REPLIES:
        return "cancel"
    return "inject"


# ---------------------------------------------------------------------------
# RAM state
# ---------------------------------------------------------------------------


def set_pending(session_key: str, meta: dict[str, Any]) -> None:
    with _lock:
        _pending[session_key] = dict(meta)


def peek_pending(session_key: str) -> Optional[dict[str, Any]]:
    with _lock:
        meta = _pending.get(session_key)
        return dict(meta) if meta else None


def clear_pending(session_key: str) -> None:
    with _lock:
        _pending.pop(session_key, None)


def clear_all(session_key: str) -> None:
    clear_pending(session_key)


def reset_state() -> None:
    """Test helper: drop all RAM slots."""
    with _lock:
        _pending.clear()


# ---------------------------------------------------------------------------
# Session key — same format the gateway uses for pending clarify
# ---------------------------------------------------------------------------


def resolve_session_key_for_tool(**kwargs: Any) -> str:
    """Same key the gateway bound for this agent turn (clarify / approval)."""
    try:
        from tools.approval import get_current_session_key

        key = get_current_session_key("")
        if key and key != "default":
            return str(key)
    except Exception:
        pass
    try:
        from gateway.session_context import get_session_env

        key = get_session_env("HERMES_SESSION_KEY", "")
        if key:
            return str(key)
    except Exception:
        pass
    env = os.environ.get("HERMES_SESSION_KEY", "").strip()
    if env:
        return env
    for name in ("session_id", "task_id"):
        val = kwargs.get(name)
        if val:
            return str(val)
    return "default"


def _find_clarify_callback(session_key: str) -> Optional[Callable]:
    """Best-effort: same-process agent.clarify_callback (gateway worker).

    Prefers an exact session-key match. When the WebUI worker cannot resolve a
    real session key (``resolve_session_key_for_tool`` returns ``"default"``),
    falls back to the first callable ``clarify_callback`` on the heap — there is
    a single active agent per worker process, so the fallback is unambiguous.
    """
    fallback: Optional[Callable] = None
    try:
        import gc

        for obj in gc.get_objects():
            cb = getattr(obj, "clarify_callback", None)
            if not callable(cb):
                continue
            if fallback is None:
                fallback = cb
            gsk = getattr(obj, "_gateway_session_key", None) or getattr(
                obj, "gateway_session_key", None
            )
            if session_key and gsk and str(gsk) == session_key:
                return cb
            sid = getattr(obj, "session_id", None)
            if session_key and sid and str(sid) == session_key:
                return cb
    except Exception:
        return None
    return fallback


# ---------------------------------------------------------------------------
# Direct CDP (never via browser_type / browser_cdp / dispatch_tool)
# ---------------------------------------------------------------------------


def _as_http_base(url: str) -> str:
    raw = (url or "").strip()
    if raw.startswith("ws://"):
        raw = "http://" + raw[5:]
    elif raw.startswith("wss://"):
        raw = "https://" + raw[6:]
    for suffix in ("/json/version", "/json/list", "/json"):
        if raw.endswith(suffix):
            raw = raw[: -len(suffix)]
            break
    if "/devtools/" in raw:
        raw = raw.split("/devtools/", 1)[0]
    return raw.rstrip("/")


def resolve_cdp_http_base(explicit: Optional[str] = None) -> str:
    """explicit arg > BROWSER_CDP_URL > browser.cdp_url > loopback :9222."""
    if explicit and str(explicit).strip():
        return _as_http_base(str(explicit))
    env = os.environ.get("BROWSER_CDP_URL", "").strip()
    if env:
        return _as_http_base(env)
    try:
        from hermes_cli.config import read_raw_config

        cfg = read_raw_config() or {}
        browser = cfg.get("browser") if isinstance(cfg, dict) else None
        if isinstance(browser, dict):
            configured = str(browser.get("cdp_url") or "").strip()
            if configured:
                return _as_http_base(configured)
    except Exception:
        pass
    return DEFAULT_CDP_URL


def _http_json(url: str, timeout: float = 3.0) -> Any:
    req = Request(url, headers={"Accept": "application/json"})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def pick_target(
    targets: list,
    target_id: Optional[str] = None,
    frame_id: Optional[str] = None,
) -> Optional[dict]:
    pages = [t for t in targets if isinstance(t, dict)]
    if frame_id:
        for item in pages:
            if item.get("id") == frame_id:
                return item
    if target_id:
        for item in pages:
            if item.get("id") == target_id:
                return item
    for item in pages:
        if item.get("type") == "page" and item.get("attached"):
            return item
    for item in pages:
        if item.get("type") == "page":
            return item
    return pages[0] if pages else None


def _run_async(coro):
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


async def _cdp_session_call(
    ws_url: str,
    calls: list[tuple[str, dict]],
    *,
    attach_target_id: Optional[str] = None,
    timeout: float = _CDP_TIMEOUT_S,
) -> list[Any]:
    """Open one CDP websocket, optionally attach, run methods, return results."""
    import websockets

    results: list[Any] = []
    async with websockets.connect(
        ws_url,
        max_size=None,
        open_timeout=timeout,
        close_timeout=3,
        ping_interval=None,
    ) as ws:
        next_id = 1
        session_id: Optional[str] = None

        async def _rpc(method: str, params: dict) -> Any:
            nonlocal next_id
            call_id = next_id
            next_id += 1
            req: dict[str, Any] = {"id": call_id, "method": method, "params": params or {}}
            if session_id:
                req["sessionId"] = session_id
            await ws.send(json.dumps(req))
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(method)
                raw = await _wait_recv(ws, remaining)
                msg = json.loads(raw)
                if msg.get("id") != call_id:
                    continue
                if "error" in msg:
                    raise RuntimeError(str(msg.get("error")))
                return msg.get("result", {})

        if attach_target_id:
            attached = await _rpc(
                "Target.attachToTarget",
                {"targetId": attach_target_id, "flatten": True},
            )
            session_id = (attached or {}).get("sessionId")
            if not session_id:
                raise RuntimeError("attachToTarget returned no sessionId")

        for method, params in calls:
            results.append(await _rpc(method, params))
    return results


async def _wait_recv(ws: Any, remaining: float) -> str:
    import asyncio

    raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
    return raw if isinstance(raw, str) else raw.decode("utf-8", errors="replace")


def _eval_length_expr() -> str:
    return (
        "(function(){var el=document.activeElement;"
        "if(!el||typeof el.value!=='string')return 0;"
        "return el.value.length;})()"
    )


def _eval_focus_expr() -> str:
    return (
        "(function(){var el=document.activeElement;"
        "if(el&&typeof el.focus==='function'){el.focus();return true;}"
        "return false;})()"
    )


def _length_from_eval(result: Any) -> int:
    if not isinstance(result, dict):
        return 0
    inner = result.get("result")
    if not isinstance(inner, dict):
        return 0
    value = inner.get("value")
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def inject_secret(
    secret: str,
    *,
    cdp_url: Optional[str] = None,
    target_id: Optional[str] = None,
    frame_id: Optional[str] = None,
) -> tuple[bool, str]:
    """Type *secret* into the focused field over a direct CDP websocket."""
    if not secret:
        return False, "empty secret"
    base = resolve_cdp_http_base(cdp_url)
    try:
        targets = _http_json(base + "/json")
    except (URLError, OSError, TimeoutError, json.JSONDecodeError, ValueError):
        logger.warning("secret-handoff: CDP target list failed")
        return False, "cdp unavailable"
    if not isinstance(targets, list):
        return False, "cdp unavailable"

    target = pick_target(targets, target_id=target_id, frame_id=frame_id)
    if target is None:
        return False, "no target"
    ws_url = str(target.get("webSocketDebuggerUrl") or "").strip()
    attach_id: Optional[str] = None
    if not ws_url:
        # Browser-level endpoint + attach to the chosen target / OOPIF.
        try:
            version = _http_json(base + "/json/version")
            ws_url = str((version or {}).get("webSocketDebuggerUrl") or "").strip()
        except (URLError, OSError, TimeoutError, json.JSONDecodeError, ValueError):
            ws_url = ""
        attach_id = str(target.get("id") or frame_id or target_id or "") or None
    elif frame_id and target.get("id") != frame_id:
        attach_id = frame_id

    if not ws_url:
        return False, "cdp unavailable"

    focus_call = ("Runtime.evaluate", {"expression": _eval_focus_expr(), "returnByValue": True})
    insert_call = ("Input.insertText", {"text": secret})
    len_call = ("Runtime.evaluate", {"expression": _eval_length_expr(), "returnByValue": True})
    char_calls = [("Input.dispatchKeyEvent", {"type": "char", "text": ch}) for ch in secret]

    try:
        results = _run_async(
            _cdp_session_call(
                ws_url,
                [focus_call, insert_call, len_call],
                attach_target_id=attach_id,
            )
        )
        length = _length_from_eval(results[-1] if results else None)
        if length > 0:
            return True, "injected"
        results = _run_async(
            _cdp_session_call(
                ws_url,
                [focus_call, *char_calls, len_call],
                attach_target_id=attach_id,
            )
        )
        length = _length_from_eval(results[-1] if results else None)
        if length > 0:
            return True, "injected"
        return False, "field still empty"
    except Exception:
        logger.warning("secret-handoff: CDP inject failed")
        return False, "inject failed"


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

_QUESTION = (
    "Type the password here; it will not be saved to the transcript; "
    "reply `cancel` to abort."
)

REQUEST_SECRET_SCHEMA: dict[str, Any] = {
    "name": "request_secret",
    "description": (
        "Ask the user for a site password without persisting it. Focus the "
        "password field in the browser first, then call this. Stock clarify "
        "collects the reply; the plugin injects it via a direct CDP websocket "
        "and returns only a status JSON — the secret never enters the "
        "transcript or model context. Do not ask the user to paste a password "
        "in chat."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "service": {
                "type": "string",
                "description": "Site or account name (e.g. example.com).",
            },
            "target_id": {
                "type": "string",
                "description": "CDP target/tab id. Omit to use the focused/attached page.",
            },
            "frame_id": {
                "type": "string",
                "description": "OOPIF frame id (same constraint as browser_cdp).",
            },
            "cdp_url": {
                "type": "string",
                "description": "Optional explicit CDP endpoint. Defaults to BROWSER_CDP_URL / :9222.",
            },
        },
        "required": ["service"],
    },
}


def _opt_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _extract_clarify_response(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, dict):
        return str(raw.get("user_response") or "")
    text = str(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text.strip()
    if isinstance(data, dict):
        return str(data.get("user_response") or "")
    return text.strip()


def _clarify_looks_failed(response: str) -> Optional[dict[str, str]]:
    low = response.strip()
    if not low:
        return {"status": "failed", "detail": "empty"}
    if low.startswith("[user did not respond"):
        return {"status": "failed", "detail": "timed out"}
    if low.startswith("[clarify") or "not available" in low.lower():
        return {"status": "failed", "detail": "clarify unavailable"}
    return None


def handle_request_secret(args: dict, **kwargs: Any) -> str:
    service = str((args or {}).get("service") or "").strip()
    if not service:
        return json.dumps(
            {"status": "failed", "service": "", "detail": "service is required"}
        )

    session_key = resolve_session_key_for_tool(**kwargs)
    target_id = _opt_str((args or {}).get("target_id"))
    frame_id = _opt_str((args or {}).get("frame_id"))
    cdp_url = _opt_str((args or {}).get("cdp_url"))

    set_pending(
        session_key,
        {
            "service": service,
            "target_id": target_id,
            "frame_id": frame_id,
            "cdp_url": cdp_url,
            "created_at": time.time(),
        },
    )

    question = f"Password for {service}. {_QUESTION}"
    callback = kwargs.get("callback") or _find_clarify_callback(session_key)

    raw: Any = ""
    response = ""
    try:
        try:
            from tools.clarify_tool import clarify_tool

            raw = clarify_tool(question=question, choices=None, callback=callback)
        except Exception:
            logger.warning("secret-handoff: clarify_tool failed")
            return json.dumps(
                {"status": "failed", "service": service, "detail": "clarify unavailable"}
            )

        response = _extract_clarify_response(raw)
        raw = ""
        mapped = _clarify_looks_failed(response)
        if mapped:
            return json.dumps(
                {
                    "status": mapped["status"],
                    "service": service,
                    "detail": mapped["detail"],
                }
            )

        kind = classify_reply(response)
        if kind in {"cancel", "ignore"}:
            return json.dumps(
                {"status": "cancelled", "service": service, "detail": "user cancelled"}
            )

        pending = peek_pending(session_key) or {}
        try:
            ok, detail = inject_secret(
                response,
                cdp_url=cdp_url or pending.get("cdp_url"),
                target_id=target_id or pending.get("target_id"),
                frame_id=frame_id or pending.get("frame_id"),
            )
        except Exception:
            ok, detail = False, "inject failed"

        return json.dumps(
            {
                "status": "ok" if ok else "failed",
                "service": service,
                "detail": detail,
            }
        )
    except Exception:
        logger.warning("secret-handoff: request_secret failed")
        return json.dumps(
            {"status": "failed", "service": service, "detail": "inject failed"}
        )
    finally:
        raw = ""
        response = ""
        clear_all(session_key)


def _register_tool(ctx: Any) -> None:
    kwargs: dict[str, Any] = {
        "name": "request_secret",
        "handler": handle_request_secret,
        "schema": REQUEST_SECRET_SCHEMA,
        "toolset": "plugin",
        "timeout_s": TOOL_TIMEOUT_S,
        "description": REQUEST_SECRET_SCHEMA["description"],
        "emoji": "🔐",
    }
    try:
        sig = inspect.signature(ctx.register_tool)
        params = sig.parameters
        if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
            ctx.register_tool(**kwargs)
            return
        accepted = {
            name
            for name, p in params.items()
            if p.kind
            in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
        }
        ctx.register_tool(**{k: v for k, v in kwargs.items() if k in accepted})
    except TypeError:
        ctx.register_tool(
            name="request_secret",
            toolset="plugin",
            schema=REQUEST_SECRET_SCHEMA,
            handler=handle_request_secret,
            description=REQUEST_SECRET_SCHEMA["description"],
            emoji="🔐",
        )


def register(ctx: Any) -> None:
    _register_tool(ctx)
    logger.info("secret-handoff: registered request_secret")
