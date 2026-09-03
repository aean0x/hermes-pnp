"""gbrain-retrieval-reflex — ambient push-context for Hermes.

Under HTTP sole-owner (gbrain serve --http), resolve IPC sock is NOT bound
(upstream 0.42.x: startResolveIpcServer only on stdio). So ambient path is:

  user turn → HTTP MCP volunteer_context (entity resolve, multi-turn window)
           → HTTP MCP query (hybrid topical fallback)
           → merge/dedupe, rank top-N by strength
           → inject ## Brain pages (ambient push) into pre_llm_call context

Optional fast path: if .gbrain-resolve.sock exists, use resolve IPC first.

Never shells gbrain CLI; never opens PGLite itself.
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Top-N ambient pointers (user-facing: top-5 ranked).
_MAX_POINTERS = int(os.environ.get("GBRAIN_RETRIEVAL_REFLEX_MAX_POINTERS", "5"))
_IPC_TIMEOUT_S = float(os.environ.get("GBRAIN_RESOLVE_IPC_TIMEOUT_S", "0.25"))
_HTTP_TIMEOUT_S = float(os.environ.get("GBRAIN_VOLUNTEER_HTTP_TIMEOUT_S", "8.0"))
# ~5 × 400-char previews + headers still small vs system/prompt bulk.
_MAX_CONTEXT_BYTES = int(os.environ.get("GBRAIN_RETRIEVAL_REFLEX_MAX_CONTEXT_BYTES", "3200"))
_SYNOPSIS_MAX = int(os.environ.get("GBRAIN_RETRIEVAL_REFLEX_SYNOPSIS_MAX", "400"))
_SYNOPSIS_MIN_SOFT = 50  # prefer longer when source has it; never pad
_MIN_MESSAGE_CHARS = 4
# Entity gate: stock default 0.7 drops slug-suffix (0.6) unless multi-turn boost.
# Ambient uses 0.6 so named entities (people/…, ops/…) still surface; query fills topical.
_VOLUNTEER_MIN_CONF = float(os.environ.get("GBRAIN_RETRIEVAL_REFLEX_MIN_CONF", "0.6"))
_HISTORY_TURNS = int(os.environ.get("GBRAIN_RETRIEVAL_REFLEX_HISTORY_TURNS", "6"))
def _audit_paths() -> tuple[Path, ...]:
    paths: list[Path] = []
    env = os.environ.get("GBRAIN_RETRIEVAL_AUDIT", "").strip()
    if env:
        paths.append(Path(env))
    home = os.environ.get("HOME")
    if home:
        paths.append(Path(home) / ".gbrain" / "retrieval-reflex-last.json")
    paths.extend(
        [
            Path("/var/lib/hermes/home/.gbrain/retrieval-reflex-last.json"),
            Path("/tmp/gbrain-retrieval-reflex-last.json"),
        ]
    )
    return tuple(paths)


_AUDIT_PATHS = _audit_paths()

_MCP_URL = os.environ.get("GBRAIN_MCP_URL", "http://127.0.0.1:3131/mcp").strip()
_SOCK_NAME = ".gbrain-resolve.sock"

_TRIVIAL_RE = re.compile(
    r"^(ok|okay|k|yes|y|no|n|thanks|thank you|thx|ty|ping|pong|hi|hello|"
    r"hey|sup|gm|gn|test|yo|sure|yep|nope|cool|got it|ack|lgtm|"
    r"\+1|-1|👍|🙏|✅|❌)[\s!.?]*$",
    re.IGNORECASE,
)

def _token_paths() -> tuple[Path | None, ...]:
    paths: list[Path | None] = []
    if os.environ.get("HOME"):
        paths.append(Path(os.environ["HOME"]) / ".gbrain" / "hermes-mcp.token")
    paths.extend(
        [
            Path("/var/lib/hermes/home/.gbrain/hermes-mcp.token"),
            Path("/home/hermes/.gbrain/hermes-mcp.token"),
        ]
    )
    return tuple(paths)


def _env_paths() -> tuple[Path | None, ...]:
    paths: list[Path | None] = []
    if os.environ.get("HERMES_HOME"):
        paths.append(Path(os.environ["HERMES_HOME"]) / ".env")
    paths.extend(
        [
            Path("/var/lib/hermes/.hermes/.env"),
            Path("/home/hermes/.hermes/.env"),
        ]
    )
    return tuple(paths)


_TOKEN_PATHS = _token_paths()
_ENV_PATHS = _env_paths()


def register(ctx: Any) -> None:
    ctx.register_hook("pre_llm_call", on_pre_llm_call)
    logger.info(
        "gbrain-retrieval-reflex: registered (max=%s synopsis_max=%s http_timeout=%ss mcp=%s)",
        _MAX_POINTERS,
        _SYNOPSIS_MAX,
        _HTTP_TIMEOUT_S,
        _MCP_URL,
    )


def on_pre_llm_call(
    *,
    user_message: Any = None,
    conversation_history: Any = None,
    **kwargs: Any,
) -> Optional[Dict[str, str]]:
    try:
        text = _normalize_user_message(user_message)
        if not text or len(text.strip()) < _MIN_MESSAGE_CHARS:
            return None
        if _TRIVIAL_RE.match(text.strip()):
            return None

        # 1) Entity push (multi-turn window when history available)
        window = _build_volunteer_window(text, conversation_history)
        volunteered = _volunteer_via_http(window)
        # 2) Hybrid topical query always run so topic + entity can both land
        queried = _query_via_http(text)
        pages, source = _merge_rank_pages(volunteered, queried)

        if pages:
            context = _format_pages(pages)
            if context:
                _audit(
                    {
                        "ok": True,
                        "source": source,
                        "n": len(pages),
                        "slugs": [p.get("slug") for p in pages],
                        "scores": [p.get("confidence") for p in pages],
                    }
                )
                logger.info(
                    "gbrain-retrieval-reflex: inject source=%s slugs=%s",
                    source,
                    [p.get("slug") for p in pages][:8],
                )
                return {"context": context, "target": "user_message"}

        # Optional IPC if sock exists (stdio serve builds only).
        sock = _resolve_socket_path()
        if sock is not None:
            block = _resolve_via_ipc(sock, _extract_candidates_light(text))
            if block:
                context = _format_block(block)
                if context:
                    _audit({"ok": True, "source": "ipc", "n": 1})
                    return {"context": context, "target": "user_message"}

        _audit({"ok": False, "reason": "no_pages", "text_len": len(text)})
        return None
    except Exception as exc:
        logger.warning("gbrain-retrieval-reflex: fail-open: %s", exc, exc_info=True)
        _audit({"ok": False, "reason": "exception", "error": str(exc)[:200]})
        return None


def _audit(payload: Dict[str, Any]) -> None:
    """Best-effort last-inject marker for live troubleshooting."""
    payload = {**payload, "ts": time.time()}
    raw = json.dumps(payload, ensure_ascii=False)
    for p in _AUDIT_PATHS:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(raw + "\n", encoding="utf-8")
            try:
                p.chmod(0o644)
            except OSError:
                pass
            return
        except OSError:
            continue


def _normalize_user_message(user_message: Any) -> str:
    if user_message is None:
        return ""
    if isinstance(user_message, str):
        return user_message
    if isinstance(user_message, list):
        parts: List[str] = []
        for item in user_message:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                t = item.get("text") or item.get("content") or ""
                if isinstance(t, str) and t:
                    parts.append(t)
        return "\n".join(parts)
    if isinstance(user_message, dict):
        t = user_message.get("text") or user_message.get("content") or ""
        if isinstance(t, str):
            return t
        if isinstance(t, list):
            return _normalize_user_message(t)
    return str(user_message)


def _strip_injected_blocks(text: str) -> str:
    """Drop prior ambient / MEMORY budget blocks so volunteer doesn't re-hit them."""
    if not text:
        return ""
    cut_markers = (
        "## Brain pages (ambient push)",
        "## Brain pages mentioned this turn",
        "## MEMORY budget",
    )
    out = text
    for m in cut_markers:
        if m in out:
            out = out.split(m, 1)[0]
    return out.strip()


def _build_volunteer_window(user_text: str, history: Any) -> str:
    """oldest → newest user:/assistant: lines for volunteer_context."""
    lines: List[str] = []
    if isinstance(history, list) and history:
        recent = history[-max(1, _HISTORY_TURNS) :]
        for msg in recent:
            if not isinstance(msg, dict):
                continue
            role = (msg.get("role") or "").strip().lower()
            if role not in ("user", "assistant"):
                continue
            content = msg.get("content")
            if content is None:
                content = msg.get("text")
            body = _normalize_user_message(content).strip()
            body = _strip_injected_blocks(body)
            if not body:
                continue
            if len(body) > 500:
                body = body[:500]
            lines.append(f"{role}: {body}")

    ut = _strip_injected_blocks(user_text.strip())
    if len(ut) > 800:
        ut = ut[:800]
    # Ensure current user turn is last (volunteer boosts newest-turn mentions).
    if not lines or not (lines[-1].startswith("user:") and ut[:80] in lines[-1]):
        lines.append(f"user: {ut}")
    return "\n".join(lines) if lines else f"user: {ut}"


def _read_bearer_token() -> str:
    # 1. Process env — Hermes loads $HERMES_HOME/.env into os.environ.
    tok = os.environ.get("GBRAIN_TOKEN", "").strip()
    if tok:
        return tok
    # 2. Durable token file (minted by gbrain auth create).
    for p in _TOKEN_PATHS:
        if p is None:
            continue
        try:
            if p.is_file():
                tok = p.read_text(encoding="utf-8").strip()
                if tok:
                    return tok
        except OSError:
            continue
    # 3. .env fallback (read directly).
    for p in _ENV_PATHS:
        if p is None:
            continue
        try:
            if not p.is_file():
                continue
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("GBRAIN_TOKEN="):
                    tok = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if tok:
                        return tok
        except OSError:
            continue
    return ""


def _parse_sse_json(raw: str) -> Optional[Dict[str, Any]]:
    """Parse streamable-HTTP / SSE body for first JSON-RPC object."""
    raw = raw.strip()
    if not raw:
        return None
    if "data:" in raw:
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload and payload != "[DONE]":
                    try:
                        return json.loads(payload)
                    except json.JSONDecodeError:
                        continue
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _mcp_http_call(method: str, params: Dict[str, Any], *, req_id: int = 1) -> Optional[Dict[str, Any]]:
    token = _read_bearer_token()
    if not token:
        logger.debug("gbrain-retrieval-reflex: no bearer token")
        return None
    body = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    try:
        req = urllib.request.Request(
            _MCP_URL,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        return _parse_sse_json(raw)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        logger.debug("gbrain-retrieval-reflex: http miss: %s", exc)
        return None


def _tools_call_text(name: str, arguments: Dict[str, Any]) -> Optional[str]:
    msg = _mcp_http_call(
        "tools/call",
        {"name": name, "arguments": arguments},
        req_id=3,
    )
    if not msg or not isinstance(msg, dict) or msg.get("error"):
        if msg and msg.get("error"):
            logger.debug("gbrain-retrieval-reflex: %s error: %s", name, msg.get("error"))
        return None
    result = msg.get("result") or {}
    if not isinstance(result, dict):
        return None
    content = result.get("content")
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(part.get("text") or "")
        return "".join(parts)
    return None


def _volunteer_via_http(window: str) -> List[Dict[str, Any]]:
    """Call volunteer_context over HTTP MCP. [] if empty or transport fail."""
    w = window.strip()
    if not w:
        return []
    if not w.startswith("user:") and not w.startswith("assistant:"):
        w = f"user: {w}"
    text_blob = _tools_call_text(
        "volunteer_context",
        {
            "window": w,
            "max_pages": _MAX_POINTERS,
            "min_confidence": _VOLUNTEER_MIN_CONF,
        },
    )
    if not text_blob:
        return []
    try:
        data = json.loads(text_blob)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    pages = data.get("pages")
    return pages if isinstance(pages, list) else []


def _query_via_http(user_text: str) -> List[Dict[str, Any]]:
    """Hybrid query for topical relevance (always complements entity volunteer)."""
    q = _strip_injected_blocks(user_text.strip())
    if len(q) > 240:
        q = q[:240]
    text_blob = _tools_call_text(
        "query",
        {"query": q, "limit": max(_MAX_POINTERS, 8)},
    )
    if not text_blob:
        return []
    try:
        data = json.loads(text_blob)
    except json.JSONDecodeError:
        return []
    hits: List[Any]
    if isinstance(data, list):
        hits = data
    elif isinstance(data, dict):
        hits = data.get("results") or data.get("hits") or data.get("pages") or []
    else:
        return []
    pages: List[Dict[str, Any]] = []
    for h in hits:
        if not isinstance(h, dict):
            continue
        slug = h.get("slug") or ""
        if not slug:
            continue
        # Prefer curated summary fields if present; else cleaned chunk_text.
        raw_syn = (
            h.get("synopsis")
            or h.get("summary")
            or h.get("description")
            or h.get("chunk_text")
            or h.get("snippet")
            or ""
        )
        score = h.get("rerank_score")
        if score is None:
            score = h.get("score")
        if score is None:
            score = h.get("confidence")
        pages.append(
            {
                "display": h.get("title") or slug,
                "slug": slug,
                "synopsis": _clean_synopsis(str(raw_syn) if raw_syn is not None else ""),
                "confidence": score,
                "source": "query",
            }
        )
    return pages


def _clean_synopsis(raw: str, *, max_len: int = 0) -> str:
    """Privacy-ish one-line preview: fm/body prose, no heading spam, clipped."""
    if not raw:
        return ""
    max_len = max_len or _SYNOPSIS_MAX
    s = raw.replace("\r\n", "\n").replace("\r", "\n")
    if s.lstrip().startswith("---"):
        parts = s.split("---", 2)
        if len(parts) >= 3:
            s = parts[2]
    # Prefer a frontmatter-like summary: line if present in free text.
    m = re.search(r"(?im)^(?:summary|description)\s*:\s*[\"']?(.+?)[\"']?\s*$", s)
    if m:
        s = m.group(1)
    lines: List[str] = []
    for line in s.split("\n"):
        t = line.strip()
        if not t or t.startswith("#") or t.startswith("<!--") or t.startswith("|"):
            continue
        if t in ("---", "***", "```"):
            continue
        t = re.sub(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]", r"\1", t)
        t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
        t = re.sub(r"[*_`]+", "", t)
        t = re.sub(r"^\s*[-*]\s+", "", t)
        if t:
            lines.append(t)
        if sum(len(x) for x in lines) >= max_len + 40:
            break
    prose = re.sub(r"\s+", " ", " ".join(lines)).strip()
    if not prose:
        return ""
    if len(prose) > max_len:
        cut = prose[: max_len - 1]
        if " " in cut:
            cut = cut.rsplit(" ", 1)[0]
        prose = cut.rstrip(" ,;:") + "…"
    return prose


def _as_score(val: Any) -> float:
    try:
        if val is None:
            return 0.0
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _normalize_page(p: Dict[str, Any], *, default_source: str) -> Optional[Dict[str, Any]]:
    slug = p.get("slug") or ""
    if not slug:
        return None
    syn = (
        p.get("synopsis")
        or p.get("summary")
        or p.get("description")
        or p.get("rationale")
        or p.get("chunk_text")
        or p.get("snippet")
        or ""
    )
    conf = p.get("confidence")
    if conf is None:
        conf = p.get("score")
    if conf is None:
        conf = p.get("rerank_score")
    return {
        "display": p.get("display") or p.get("title") or slug,
        "slug": slug,
        "synopsis": _clean_synopsis(str(syn) if syn is not None else ""),
        "confidence": conf,
        "source": p.get("source") or default_source,
        "arm": p.get("arm"),
        "rationale": p.get("rationale"),
    }


def _merge_rank_pages(
    volunteered: List[Any], queried: List[Any]
) -> Tuple[List[Dict[str, Any]], str]:
    """Entity hits first (precision), then topical query by score. Cap top-N."""
    out: List[Dict[str, Any]] = []
    seen: set = set()
    n_vol = 0
    n_q = 0

    for raw in volunteered:
        if not isinstance(raw, dict):
            continue
        page = _normalize_page(raw, default_source="volunteer")
        if not page or page["slug"] in seen:
            continue
        seen.add(page["slug"])
        out.append(page)
        n_vol += 1
        if len(out) >= _MAX_POINTERS:
            break

    q_norm: List[Dict[str, Any]] = []
    for raw in queried:
        if not isinstance(raw, dict):
            continue
        page = _normalize_page(raw, default_source="query")
        if not page or page["slug"] in seen:
            continue
        q_norm.append(page)
    q_norm.sort(key=lambda p: _as_score(p.get("confidence")), reverse=True)

    for page in q_norm:
        if page["slug"] in seen:
            continue
        seen.add(page["slug"])
        out.append(page)
        n_q += 1
        if len(out) >= _MAX_POINTERS:
            break

    if n_vol and n_q:
        source = "volunteer+query"
    elif n_vol:
        source = "volunteer_context"
    elif n_q:
        source = "query"
    else:
        source = "none"
    return out[:_MAX_POINTERS], source


def _format_score(val: Any) -> str:
    s = _as_score(val)
    if s <= 0:
        return "—"
    if s > 1.5:
        # unlikely raw; still show compact
        return f"{s:.2f}"
    return f"{s:.2f}"


def _format_pages(pages: List[Dict[str, Any]]) -> str:
    if not pages:
        return ""
    lines = [
        "## Brain pages (ambient push)",
        "Top matches by relevance (strength-ordered). Open with MCP get_page before relying on details.",
        "",
    ]
    for i, p in enumerate(pages[:_MAX_POINTERS], start=1):
        if not isinstance(p, dict):
            continue
        display = p.get("display") or p.get("slug") or "?"
        slug = p.get("slug") or ""
        syn = (p.get("synopsis") or "").replace("\n", " ").strip()
        if len(syn) > _SYNOPSIS_MAX:
            syn = syn[: _SYNOPSIS_MAX - 1].rsplit(" ", 1)[0] + "…"
        src = p.get("source") or "?"
        arm = p.get("arm")
        src_s = f"{src}/{arm}" if arm else str(src)
        strength = _format_score(p.get("confidence"))
        head = f"{i}. **{display}** → `{slug}` ({strength} · {src_s})"
        lines.append(f"{head} — {syn}" if syn else head)
    lines.append("Never shell gbrain CLI while serve is up.")
    context = "\n".join(lines)
    if len(context.encode("utf-8")) <= _MAX_CONTEXT_BYTES:
        return context
    # Budget overrun: shrink synopses progressively, keep ranking.
    budget = _MAX_CONTEXT_BYTES
    for syn_cap in (220, 120, 80, 0):
        lines = [
            "## Brain pages (ambient push)",
            "Top matches by relevance (strength-ordered). Open with MCP get_page before relying on details.",
            "",
        ]
        for i, p in enumerate(pages[:_MAX_POINTERS], start=1):
            if not isinstance(p, dict):
                continue
            display = p.get("display") or p.get("slug") or "?"
            slug = p.get("slug") or ""
            syn = (p.get("synopsis") or "").replace("\n", " ").strip()
            if syn_cap and len(syn) > syn_cap:
                syn = syn[: syn_cap - 1].rsplit(" ", 1)[0] + "…"
            elif not syn_cap:
                syn = ""
            src = p.get("source") or "?"
            arm = p.get("arm")
            src_s = f"{src}/{arm}" if arm else str(src)
            strength = _format_score(p.get("confidence"))
            head = f"{i}. **{display}** → `{slug}` ({strength} · {src_s})"
            lines.append(f"{head} — {syn}" if syn else head)
        lines.append("Never shell gbrain CLI while serve is up.")
        context = "\n".join(lines)
        if len(context.encode("utf-8")) <= budget:
            return context
    return context[:budget]


# ── Optional resolve IPC (stdio serve only) ───────────────────────────────


def _extract_candidates_light(text: str) -> List[Dict[str, str]]:
    """Minimal candidates for IPC path only."""
    out: List[Dict[str, str]] = []
    for m in re.finditer(r"[A-ZÀ-ÖØ-Þ][\w'’\-]{1,40}", text):
        s = m.group(0)
        out.append({"display": s, "query": s})
        if len(out) >= 8:
            break
    return out


def _socket_candidates() -> List[Path]:
    paths: List[Path] = []
    env = os.environ.get("GBRAIN_RESOLVE_SOCKET", "").strip()
    if env:
        paths.append(Path(env))
    home = os.environ.get("HOME") or ""
    bases = []
    if home:
        bases.append(Path(home) / ".gbrain")
    bases.extend(
        [
            Path("/home/hermes/.gbrain"),
            Path("/var/lib/hermes/home/.gbrain"),
        ]
    )
    for base in bases:
        paths.append(base / "brain.pglite" / _SOCK_NAME)
        paths.append(base / _SOCK_NAME)
    seen = set()
    uniq: List[Path] = []
    for p in paths:
        k = str(p)
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return uniq


def _resolve_socket_path() -> Optional[Path]:
    for p in _socket_candidates():
        try:
            if p.exists():
                return p
        except OSError:
            continue
    return None


def _resolve_via_ipc(
    sock_path: Path, candidates: List[Dict[str, str]]
) -> Optional[Dict[str, Any]]:
    if not candidates:
        return None
    req = {
        "candidates": candidates,
        "maxPointers": _MAX_POINTERS,
        "suppression": "slug-only",
    }
    payload = (json.dumps(req) + "\n").encode("utf-8")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(_IPC_TIMEOUT_S)
            sock.connect(str(sock_path))
            sock.sendall(payload)
            buf = b""
            while b"\n" not in buf and len(buf) < 256 * 1024:
                chunk = sock.recv(8192)
                if not chunk:
                    break
                buf += chunk
            if b"\n" not in buf:
                return None
            line = buf.split(b"\n", 1)[0].decode("utf-8", errors="replace")
            resp = json.loads(line)
            if not isinstance(resp, dict) or not resp.get("ok"):
                return None
            block = resp.get("block")
            return block if isinstance(block, dict) else None
    except (OSError, socket.timeout, json.JSONDecodeError, ValueError) as exc:
        logger.debug("gbrain-retrieval-reflex: ipc miss: %s", exc)
        return None


def _format_block(block: Dict[str, Any]) -> str:
    text = block.get("text")
    if isinstance(text, str) and text.strip():
        return text.strip()[:_MAX_CONTEXT_BYTES]
    pointers = block.get("pointers") or []
    if not isinstance(pointers, list):
        return ""
    pages = [p for p in pointers if isinstance(p, dict)]
    return _format_pages(pages)
