"""docindex — hybrid keyword + semantic search over the local document index.

The engine (``bin/docindex.py`` for FTS5 + OCR, ``bin/docvec.py`` for the
vector arm) runs as a subprocess under a configured interpreter, because
sqlite-vec and the OCR toolchain live outside the agent runtime. Nothing in
this module reads the index in-process.

Every knob is an env var with a working default; ``services.hermesPnP.docindex``
sets them. Unset values keep the historical host paths.

Env
---
DOCINDEX_DB             index database
DOCINDEX_PYTHON         interpreter for the FTS/OCR CLI (tesseract, poppler on PATH)
DOCVEC_PYTHON           interpreter for the vector CLI (carries sqlite-vec)
DOCINDEX_LIBSTDCPP      libstdc++ dir prepended to LD_LIBRARY_PATH for vectors
DOCINDEX_PATH_PREPEND   bin dir prepended to PATH (tesseract, pdftotext)
DOCINDEX_ROOTS          JSON {"root": ["exclude", ...]} for the index tool
DOCINDEX_WORKERS        OCR worker count
DOCINDEX_TIMEOUT        per-call timeout in seconds
DOCINDEX_VERIFY_TERMS_FILE
                        optional JSON list of [term, label] sanity pairs for
                        docindex_verify; keeps document-specific identifiers
                        out of this public repo
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

BIN = Path(__file__).resolve().parent / "bin"
OUTPUT_CLIP = 6000

DEFAULT_DB = "/data/docindex/index.db"
DEFAULT_PYTHON = "/data/toolbox/bin/python3"
DEFAULT_VEC_PYTHON = "/data/docindex/embed/venv/bin/python"
DEFAULT_PATH_PREPEND = "/data/toolbox/bin"
DEFAULT_WORKERS = 2
DEFAULT_TIMEOUT = 900
MAX_INDEX_TIMEOUT = 3600

WIDTHS = ("hybrid", "vector", "keyword")


def _int_env(name: str, default: int) -> int:
    """Read an int env var, falling back to *default* on junk."""
    try:
        return int(os.environ.get(name) or default)
    except (TypeError, ValueError):
        logger.warning("docindex: %s is not an integer; using %d", name, default)
        return default


def _cfg() -> Dict[str, Any]:
    """Read the environment. Cheap enough to skip caching."""
    return {
        "db": os.environ.get("DOCINDEX_DB") or DEFAULT_DB,
        "python": os.environ.get("DOCINDEX_PYTHON") or DEFAULT_PYTHON,
        "vec_python": os.environ.get("DOCVEC_PYTHON") or DEFAULT_VEC_PYTHON,
        "libstdcpp": os.environ.get("DOCINDEX_LIBSTDCPP", "").strip(),
        "path_prepend": os.environ.get("DOCINDEX_PATH_PREPEND") or DEFAULT_PATH_PREPEND,
        "workers": _int_env("DOCINDEX_WORKERS", DEFAULT_WORKERS),
        "timeout": _int_env("DOCINDEX_TIMEOUT", DEFAULT_TIMEOUT),
    }


def _env(cfg: Dict[str, Any]) -> Dict[str, str]:
    """Process env for an engine subprocess."""
    env = dict(os.environ)
    env["DOCINDEX_DB"] = cfg["db"]
    if cfg["libstdcpp"]:
        existing = env.get("LD_LIBRARY_PATH")
        env["LD_LIBRARY_PATH"] = (
            f"{cfg['libstdcpp']}:{existing}" if existing else cfg["libstdcpp"]
        )
    if cfg["path_prepend"]:
        env["PATH"] = f"{cfg['path_prepend']}:{env.get('PATH', '')}"
    return env


def _clip(text: str) -> str:
    if len(text) <= OUTPUT_CLIP:
        return text
    return text[:OUTPUT_CLIP] + f"\n... [{len(text) - OUTPUT_CLIP} chars clipped]"


def _run(
    cfg: Dict[str, Any],
    script: str,
    args: list[str],
    *,
    vector: bool = False,
    timeout: Optional[int] = None,
) -> Tuple[int, str]:
    """Run one engine subprocess. Never raises; returns (rc, output)."""
    interpreter = cfg["vec_python"] if vector else cfg["python"]
    limit = timeout or cfg["timeout"]
    try:
        proc = subprocess.run(
            [interpreter, str(BIN / script), *args],
            capture_output=True,
            text=True,
            timeout=limit,
            env=_env(cfg),
            cwd=str(BIN),
        )
    except subprocess.TimeoutExpired:
        return 1, f"Error: {script} exceeded {limit}s."
    except OSError as exc:
        return 1, f"Error: cannot run {interpreter}: {exc}"
    out = proc.stdout or ""
    if proc.stderr:
        out = f"{out}\n{proc.stderr}" if out else proc.stderr
    return proc.returncode, _clip(out.strip())


def _roots(cfg: Dict[str, Any], raw: Any) -> Dict[str, list]:
    """Scan roots as {root: [exclude, ...]} from the argument or the env."""
    if isinstance(raw, dict) and raw:
        return {str(k): [str(x) for x in (v or [])] for k, v in raw.items()}
    if raw:
        return {str(r): [] for r in raw}
    try:
        loaded = json.loads(os.environ.get("DOCINDEX_ROOTS") or "{}")
    except ValueError:
        logger.warning("docindex: DOCINDEX_ROOTS is not valid JSON; ignoring it")
        return {}
    if isinstance(loaded, dict):
        return {str(k): [str(x) for x in (v or [])] for k, v in loaded.items()}
    return {}


# -- tools -------------------------------------------------------------------


def docindex_search(args: Dict[str, Any], **_: Any) -> str:
    """Keyword, vector or fused search over indexed document pages."""
    query = str(args.get("query") or "").strip()
    if not query:
        return "Error: 'query' is required."
    mode = str(args.get("mode") or "hybrid").lower()
    if mode not in WIDTHS:
        return f"Error: mode must be one of {', '.join(WIDTHS)} (got {mode!r})."
    k = _int_env("DOCINDEX_K", 25)
    k = max(1, min(int(args.get("k") or k), 200))
    cfg = _cfg()
    if mode == "keyword":
        rc, out = _run(
            cfg, "docindex.py", ["search", query, "--limit", str(k)]
        )
    else:
        cmd = ["search", query, "--k", str(k), "--mode", mode]
        if args.get("rerank"):
            cmd.append("--rerank")
        rc, out = _run(cfg, "docvec.py", cmd, vector=True)
    return out or f"Error: no output from the {mode} arm (rc={rc})."


def docindex_stats(args: Optional[Dict[str, Any]] = None, **_: Any) -> str:
    """Index size, coverage, vector space and space provenance."""
    cfg = _cfg()
    rc_vec, vec = _run(cfg, "docvec.py", ["stats"], vector=True)
    rc_fts, fts = _run(cfg, "docindex.py", ["stats"])
    parts = [text for text, _rc in ((vec, rc_vec), (fts, rc_fts)) if text]
    return "\n".join(parts) or f"Error: no output (rc={rc_vec}/{rc_fts})."


def docindex_show(args: Dict[str, Any], **_: Any) -> str:
    """Read one indexed document's stored text, optionally a single page."""
    path = str(args.get("path") or "").strip()
    if not path:
        return "Error: 'path' is required."
    cmd = ["show", path]
    if args.get("page"):
        cmd += ["--page", str(int(args["page"]))]
    cfg = _cfg()
    rc, out = _run(cfg, "docindex.py", cmd)
    return out or f"Error: no output (rc={rc}); check the path."


def _scan_phase(cfg: Dict[str, Any], roots: Dict[str, list]) -> list:
    lines = []
    for root, excludes in roots.items():
        args = ["scan", root]
        for pattern in excludes:
            args += ["--exclude", pattern]
        rc, out = _run(cfg, "docindex.py", args)
        lines.append(f"[scan {root}] rc={rc} {out.splitlines()[-1] if out else ''}".strip())
    return lines


def docindex_index(args: Dict[str, Any], **_: Any) -> str:
    """Scan roots, extract/OCR what is pending, then embed the vector delta.

    Full-corpus runs belong to the nightly job; this is for targeted runs.
    """
    cfg = _cfg()
    roots = _roots(cfg, args.get("roots"))
    if not roots:
        return (
            "Error: no roots. Pass roots=[...] or set DOCINDEX_ROOTS "
            '(JSON {"root": ["exclude", ...]}).'
        )
    cap = min(cfg["timeout"] * 4, MAX_INDEX_TIMEOUT)
    lines = _scan_phase(cfg, roots)
    extract = ["extract", "--workers", str(cfg["workers"])]
    if args.get("limit"):
        extract += ["--limit", str(int(args["limit"]))]
    if args.get("match"):
        extract += ["--match", str(args["match"])]
    rc, out = _run(cfg, "docindex.py", extract, timeout=cap)
    lines.append(f"[extract] rc={rc} {out.splitlines()[-1] if out else ''}".strip())
    rc, out = _run(cfg, "docvec.py", ["embed", "--workers", str(cfg["workers"])], vector=True, timeout=cap)
    lines.append(f"[embed] rc={rc} {out.splitlines()[-1] if out else ''}".strip())
    return "\n".join(lines)


def docindex_verify(args: Optional[Dict[str, Any]] = None, **_: Any) -> str:
    """Coverage, statuses and search sanity. Non-zero rc means work is left."""
    cfg = _cfg()
    rc, out = _run(cfg, "verify.py", [])
    return f"rc={rc}\n{out}" if out else f"rc={rc} (no output)"


# -- registration ------------------------------------------------------------

_SCHEMAS = {
    "docindex_search": {
        "name": "docindex_search",
        "description": (
            "Search the local document index (scanned post, letters, tax and "
            "insurance paperwork, project notes). Hybrid fuses keyword and "
            "vector hits; use vector for paraphrases ('the fee for my dog'), "
            "keyword for exact identifiers (IBAN, invoice number), rerank to "
            "rescore a wide pool with a cross-encoder."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to find."},
                "mode": {
                    "type": "string",
                    "enum": list(WIDTHS),
                    "description": "Search arm. Default hybrid.",
                },
                "k": {
                    "type": "integer",
                    "description": "Results to return (default 25). Use >= 25 before reranking.",
                },
                "rerank": {
                    "type": "boolean",
                    "description": "Rescore the fused pool with the hosted cross-encoder.",
                },
            },
            "required": ["query"],
        },
    },
    "docindex_stats": {
        "name": "docindex_stats",
        "description": (
            "Document index size, coverage, the vector model/width in use and "
            "which configuration source won."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    "docindex_show": {
        "name": "docindex_show",
        "description": "Read the stored text of one indexed document, optionally one page.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Indexed path as returned by search."},
                "page": {"type": "integer", "description": "Page number, 1-based."},
            },
            "required": ["path"],
        },
    },
    "docindex_index": {
        "name": "docindex_index",
        "description": (
            "Scan roots for new or changed files, extract/OCR what is pending, "
            "then embed the vector delta. Targeted runs only; the nightly job "
            "owns full-corpus refresh."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "roots": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Roots to scan. Defaults to DOCINDEX_ROOTS.",
                },
                "match": {
                    "type": "string",
                    "description": "Only files whose path contains this text.",
                },
                "limit": {"type": "integer", "description": "Max files to extract."},
            },
        },
    },
    "docindex_verify": {
        "name": "docindex_verify",
        "description": (
            "Post-run gate: coverage, per-status counts, unparseable files and "
            "search sanity. Non-zero rc means unprocessed or failed files remain."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


def register(ctx: Any) -> None:
    """Register the docindex toolset."""
    handlers = {
        "docindex_search": docindex_search,
        "docindex_stats": docindex_stats,
        "docindex_show": docindex_show,
        "docindex_index": docindex_index,
        "docindex_verify": docindex_verify,
    }
    for name, handler in handlers.items():
        schema = _SCHEMAS[name]
        ctx.register_tool(
            name=name,
            toolset="docindex",
            schema=schema,
            handler=handler,
            description=schema["description"],
            emoji="\U0001f4c4",
        )
    logger.debug("docindex: registered %d tools", len(handlers))
