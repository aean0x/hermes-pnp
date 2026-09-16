"""tool-call-coherency — heal misrouted tool_call / deferred-tool invocations.

Models frequently:
1. Double-wrap MCP: tool_call(name="tool_call", arguments={name: "mcp__…", …})
2. Route core tools through the bridge: tool_call(name="cronjob", …)
3. Treat skill names as tools: tool_call(name="retrieval-reflex")
4. Emit structurally broken JSON for a bridge call. Cheap models group closers by type
   instead of nesting them (the ``calls`` array closed with ``}``, or an entry object
   closed with ``]``), drop a closer, or leave a branch unclosed — and the one shape the
   upstream repairer cannot touch: an entry's closing brace omitted altogether
   (``{"calls": [{"arguments": {…}, {"name": "mcp__…"}]}``). Upstream
   ``_repair_tool_call_arguments`` gives up on every one of these and substitutes ``{}``,
   which drops the call and forces a full regeneration.
5. Omit ``calls[i].name`` entirely, or split one call across two array entries
   (``{"calls": [{"arguments": {…}}, {"name": "mcp__…"}]}``) — the *name* is the model's
   one piece of boilerplate it skips; the arguments it gets right.

Upstream resolve_underlying_call hard-errors on (1) and (2). This plugin monkeypatches
the resolver + session scope set so those become transparent redispatches, extends the
argument repairer for (4), and restores a dropped name or a split entry for (5) from the
argument signature. No capability expansion beyond tools already in the session.
"""
from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

log = logging.getLogger("hermes.plugins.tool_call_coherency")

_PATCHED = False
_STATS = {
    "unwrap_nest": 0,
    "core_via_bridge": 0,
    "skill_rewrite": 0,
    "mcp_prefix_allow": 0,
    "args_struct_repair": 0,
    "entry_heal": 0,
}

# ---------------------------------------------------------------------------- (4) JSON shape

_OPEN_TO_CLOSE = {"{": "}", "[": "]"}
_CLOSE_TO_OPEN = {"}": "{", "]": "["}
# Delimiter a parse error implies, tried in this order at the error offset.
_INSERT_CANDIDATES = ("}", "]", ",")
_MAX_INSERT_DEPTH = 3
# Above this size the bounded insertion search is not worth its worst case; a payload this
# large that fails the single walk is model truncation, not a delimiter slip.
_MAX_SEARCH_CHARS = 65_536


def _decode_object(text: str) -> Optional[Dict[str, Any]]:
    """``text`` as a non-empty JSON object, else None.

    Bridge arguments are always a non-empty object; an empty one is what the caller's
    "unrepairable" fallback produces, so returning it as a *repair* would hide a real
    give-up behind a success log.
    """
    try:
        decoded = json.loads(text, strict=False)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return decoded if isinstance(decoded, dict) and decoded else None


def _parses_object(text: str) -> bool:
    """True when ``text`` is JSON that decodes to an object (bridge arguments always are)."""
    try:
        return isinstance(json.loads(text, strict=False), dict)
    except (json.JSONDecodeError, TypeError, ValueError):
        return False


def _pop_trailing_comma(out: List[str]) -> None:
    """Drop the separator that a closer just made illegal (``…, }`` from a grouped close)."""
    while out and out[-1] in " \t\r\n":
        out.pop()
    if out and out[-1] == ",":
        out.pop()


def _matching_open_index(stack: Sequence[str], closer: str) -> Optional[int]:
    """Index of the innermost container on ``stack`` that ``closer`` belongs to, else None."""
    for idx in range(len(stack) - 1, -1, -1):
        if stack[idx] == closer:
            return idx
    return None


def _walk_closers(text: str) -> Tuple[str, List[str], bool]:
    """One string-aware pass over ``text``.

    Legal nesting is emitted verbatim. A closer aimed at an outer container (the grouped
    emission) first closes every container inside it, in LIFO order; a closer with no open
    container is dropped. Once the root container has closed, anything after it is the
    model's leftover tail and is dropped too. Returns
    ``(text, expected_closers, open_string)`` — the caller appends the leftovers
    innermost-first, and refuses to invent a string terminator.
    """
    out: List[str] = []
    stack: List[str] = []
    in_str = False
    esc = False
    done = False
    for ch in text:
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if done:
            continue  # the value is complete; everything after it is leftover tail
        if ch == '"':
            in_str = True
            out.append(ch)
            continue
        if ch in _OPEN_TO_CLOSE:
            stack.append(_OPEN_TO_CLOSE[ch])
            out.append(ch)
            continue
        if ch in _CLOSE_TO_OPEN:
            _pop_trailing_comma(out)
            if stack and stack[-1] == ch:
                stack.pop()
                out.append(ch)
                done = len(stack) == 0
                continue
            idx = _matching_open_index(stack, ch)
            if idx is None:
                continue
            while len(stack) > idx:
                out.append(stack.pop())
            done = len(stack) == 0
            continue
        out.append(ch)
    return "".join(out), stack, in_str


def _closed_walk(text: str) -> Optional[str]:
    """``_walk_closers`` plus the closers still open at EOF; None when a string is open."""
    walked, stack, in_str = _walk_closers(text)
    if in_str:
        return None
    return walked + "".join(reversed(stack))


def _first_error_pos(text: str) -> Optional[int]:
    """Offset of the first JSON parse error in ``text``, or None when it parses."""
    try:
        json.loads(text, strict=False)
        return None
    except json.JSONDecodeError as exc:
        return exc.pos
    except (TypeError, ValueError):
        return None


def _insertion_search(text: str, depth: int = 0) -> Optional[str]:
    """Repair a delimiter *omission*: insert what the parse error implies at its offset.

    Explores the bounded tree and keeps the LONGEST valid repair, not the first one found:
    the shortest parse is usually the one that discards the most of the model's payload
    (a dropped ``, {"name": …}`` sibling parses sooner than closing the entry that should
    have held it), and that name entry is exactly what the caller needs.
    """
    if _decode_object(text) is not None:
        return text
    if depth >= _MAX_INSERT_DEPTH or len(text) > _MAX_SEARCH_CHARS:
        return None
    pos = _first_error_pos(text)
    if pos is None:
        return None
    best: Optional[str] = None
    for insertion in _INSERT_CANDIDATES:
        candidate = _closed_walk(text[:pos] + insertion + text[pos:])
        if candidate is None:
            continue
        repaired = _insertion_search(candidate, depth + 1)
        if repaired is not None and (best is None or len(repaired) > len(best)):
            best = repaired
    return best


def structural_repair(raw_args: Any) -> Optional[str]:
    """Repaired whitespace-stripped JSON text for structurally broken bridge arguments.

    ``None`` when the text is not a repairable delimiter slip — notably a string left open
    at EOF, i.e. the model ran out of output tokens, where a "repaired" payload would be a
    silently truncated one that the caller must not act on.
    """
    if not isinstance(raw_args, str):
        return None
    text = raw_args.strip()
    if not text or _parses_object(text):
        return None
    closed = _closed_walk(text)
    if closed is None:
        # A string is still open at EOF: the model ran out of output tokens mid-value.
        # No delimiter insertion can recover the missing text, and fabricating a terminator
        # would hand the caller a silently truncated payload.
        return None
    if _decode_object(closed) is not None:
        return closed
    repaired = _insertion_search(closed)
    return repaired if repaired is not None and _decode_object(repaired) is not None else None


# ------------------------------------------------------------------- (5) dropped call name

def _signature_matches(keys: frozenset, schema: Any) -> bool:
    """True when an argument-key set is exactly legal for ``schema``.

    Strict on purpose: every key must be a declared property and every required property
    must be present. A partial match is not evidence of intent.
    """
    fn = (schema or {}).get("function") if isinstance(schema, dict) else None
    params = (fn or {}).get("parameters") if isinstance(fn, dict) else None
    if not isinstance(params, dict):
        return False
    props, required = params.get("properties"), params.get("required")
    if not isinstance(props, dict) or not isinstance(required, list) or not required:
        return False
    if not keys <= set(props):
        return False
    return all(isinstance(r, str) and r in keys for r in required)


@lru_cache(maxsize=256)
def _infer_deferred_name(key_tuple: Tuple[str, ...]) -> Optional[str]:
    """The one deferred (``mcp__*``) tool whose schema exactly fits ``key_tuple``, else None.

    Ambiguity is not evidence: two candidates means no guess. Restricted to the bridge's
    deferred surface — a nameless *core*-tool call is a different failure mode, and the
    model reads the core tools off its own tool list.
    """
    try:
        from tools.registry import registry

        names = registry.get_all_tool_names()
    except Exception:
        return None
    keys = frozenset(key_tuple)
    match: Optional[str] = None
    for name in names:
        if not name.startswith("mcp__"):
            continue
        try:
            if not _signature_matches(keys, registry.get_schema(name)):
                continue
        except Exception:
            continue
        if match is not None:
            return None
        match = name
    return match


def _infer_tool_name(arguments: Dict[str, Any]) -> Optional[str]:
    keys = tuple(sorted(k for k in arguments if isinstance(k, str)))
    return _infer_deferred_name(keys) if keys else None


def _fill_missing_names(calls: List[Any]) -> int:
    """Name each nameless entry from its argument signature. Returns the count healed."""
    healed = 0
    for entry in calls:
        if not isinstance(entry, dict) or str(entry.get("name") or "").strip():
            continue
        arguments = entry.get("arguments")
        if not isinstance(arguments, dict) or not arguments:
            continue
        name = _infer_tool_name(arguments)
        if name:
            entry["name"] = name
            healed += 1
            log.warning("tool-call-coherency: entry had no name; arguments match %r", name)
    return healed


def _merge_split_entry(calls: List[Any]) -> int:
    """Rejoin one call the model wrote as two entries: ``{arguments}`` + ``{name}``."""
    if len(calls) != 2 or not all(isinstance(e, dict) for e in calls):
        return 0
    arg_only = [e for e in calls
                if not str(e.get("name") or "").strip() and isinstance(e.get("arguments"), dict)]
    name_only = [e for e in calls
                 if str(e.get("name") or "").strip() and not e.get("arguments")]
    if len(arg_only) != 1 or len(name_only) != 1:
        return 0
    name_only[0]["arguments"] = arg_only[0]["arguments"]
    calls.remove(arg_only[0])
    log.warning("tool-call-coherency: merged a split entry into %r", name_only[0]["name"])
    return 1


def _note(stat: str, count: int, template: str = "") -> None:
    """Record one heal: always in the stats table, in the log only when a template is given.

    Callers use this instead of an ``if count:`` block so the resolve wrapper stays inside
    the repo's complexity ceiling (ruff C901, max 20).
    """
    if not count:
        return
    _STATS[stat] = _STATS.get(stat, 0) + count
    if template:
        log.info("tool-call-coherency: " + template, count)


def heal_bridge_entries(args: Dict[str, Any]) -> int:
    """Fill/merge ``calls`` entries that arrived without a usable ``name`` (mutates ``args``)."""
    calls = args.get("calls") if isinstance(args, dict) else None
    if isinstance(calls, dict):
        calls = [calls]
        args["calls"] = calls
    if not isinstance(calls, list) or not calls:
        return 0
    return _merge_split_entry(calls) + _fill_missing_names(calls)


# ------------------------------------------------------------------------------ arg repair

def _parse_args_blob(raw: Any) -> Dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return raw if isinstance(raw, dict) else {}


def _skill_roots() -> list[Path]:
    roots: list[Path] = []

    v = os.environ.get("HERMES_HOME")
    if v:
        roots.append(Path(v) / "skills")
    roots.extend(
        [
            Path("/data/.hermes/skills"),
            Path.home() / ".hermes" / "skills",
        ]
    )
    try:
        from agent.skill_utils import get_external_skills_dirs

        for d in get_external_skills_dirs() or []:
            roots.append(Path(d))
    except Exception:
        pass
    seen = set()
    out = []
    for r in roots:
        try:
            key = str(r.resolve())
        except Exception:
            key = str(r)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _is_known_skill(name: str) -> bool:
    """True if a SKILL.md parent dir matches name (any nesting depth)."""
    if not name or not isinstance(name, str):
        return False
    name = name.strip()
    if not name or name.startswith("mcp__"):
        return False
    if ".." in name or "/" in name or "\\" in name:
        return False
    bare = name.split(":")[-1]
    # Hermes iterator first
    try:
        from agent.skill_utils import iter_skill_index_files

        for root in _skill_roots():
            if not root.is_dir():
                continue
            for skill_md in iter_skill_index_files(root, "SKILL.md"):
                if Path(skill_md).parent.name == bare:
                    return True
        return False
    except Exception:
        pass
    # Fallback rglob
    for root in _skill_roots():
        if not root.is_dir():
            continue
        try:
            for skill_md in root.rglob("SKILL.md"):
                if skill_md.parent.name == bare:
                    return True
        except OSError:
            continue
    return False


def _is_registered_non_bridge(name: str, bridge_names: set[str]) -> bool:
    if not name or name in bridge_names:
        return False
    try:
        from tools.registry import registry

        sch = registry.get_schema(name)
        if sch:
            return True
        # get_entry is another signal
        try:
            return registry.get_entry(name) is not None
        except Exception:
            return False
    except Exception:
        return False


def _peel_nested_bridge(
    function_args: Dict[str, Any], bridge_names: set[str]
) -> Tuple[Dict[str, Any], int]:
    """Peel tool_call(name=tool_call, arguments={name: real, ...}) nesting."""
    args = dict(function_args or {})
    peels = 0
    for _ in range(5):
        name = args.get("name") or args.get("tool") or args.get("tool_name")
        if not isinstance(name, str):
            break
        name = name.strip()
        nested = _parse_args_blob(
            args["arguments"] if "arguments" in args else args.get("args")
        )
        if name in bridge_names and nested.get("name"):
            args = nested
            peels += 1
            continue
        break
    return args, peels


def _rebind_repair_consumers(original, replacement) -> int:
    """Point modules that imported the repairer by name at ``replacement``."""
    import sys

    rebound = 0
    for module in list(sys.modules.values()):
        try:
            if getattr(module, "_repair_tool_call_arguments", None) is original:
                setattr(module, "_repair_tool_call_arguments", replacement)
                rebound += 1
        except Exception:
            continue
    return rebound


def _install_patches() -> None:
    global _PATCHED
    if _PATCHED:
        return

    import tools.tool_search as ts
    import agent.tool_executor as te
    import agent.message_sanitization as ms

    bridge = set(getattr(ts, "BRIDGE_TOOL_NAMES", None) or {
        "tool_call",
        "tool_describe",
        "tool_search",
    })
    _orig_resolve = ts.resolve_underlying_call
    _orig_scoped = ts.scoped_deferrable_names
    _orig_repair = ms._repair_tool_call_arguments

    def _patched_repair_args(raw_args: Any, tool_name: str = "?") -> str:
        """Structural repair before the upstream repairer gets to give up on the payload."""
        repaired = structural_repair(raw_args)
        if repaired is not None:
            _STATS["args_struct_repair"] += 1
            log.warning(
                "tool-call-coherency: repaired structurally broken arguments for %s "
                "(%d → %d chars)", tool_name, len(str(raw_args)), len(repaired),
            )
            return json.dumps(json.loads(repaired, strict=False), separators=(",", ":"))
        return _orig_repair(raw_args, tool_name)

    def _patched_resolve(function_args: Dict[str, Any]):
        cleaned, peels = _peel_nested_bridge(function_args or {}, bridge)
        _note("unwrap_nest", peels, "unwrapped nested bridge x%d")
        _note("entry_heal", heal_bridge_entries(cleaned))

        name = cleaned.get("name") or cleaned.get("tool") or cleaned.get("tool_name")
        if isinstance(name, str):
            name = name.strip()
        else:
            name = ""

        raw_args = _parse_args_blob(
            cleaned["arguments"] if "arguments" in cleaned else cleaned.get("args")
        )

        # Skill-as-tool → skill_view
        if name and name not in bridge and _is_known_skill(name):
            if not _is_registered_non_bridge(name, bridge):
                _STATS["skill_rewrite"] += 1
                log.info("tool-call-coherency: skill %r → skill_view", name)
                return "skill_view", {"name": name}, None

        # Original path (true deferred MCP after peel)
        u_name, u_args, err = _orig_resolve(cleaned)
        if err is None and u_name:
            return u_name, u_args, None

        # mcp__server__tool — allow even if registry momentarily cold
        if name and name.startswith("mcp__") and name.count("__") >= 2:
            _STATS["mcp_prefix_allow"] += 1
            log.info("tool-call-coherency: mcp prefix allow %r", name)
            return name, raw_args if isinstance(raw_args, dict) else {}, None

        # Core tool via bridge
        if name and name not in bridge and _is_registered_non_bridge(name, bridge):
            _STATS["core_via_bridge"] += 1
            log.info("tool-call-coherency: core via bridge %r", name)
            return name, raw_args if isinstance(raw_args, dict) else {}, None

        return u_name, u_args, err

    def _patched_scoped_names(tool_defs):
        """Include all non-bridge tools in session scope (core + deferred)."""
        names = set(_orig_scoped(tool_defs) or [])
        for td in tool_defs or []:
            if isinstance(td, str):
                if td not in bridge:
                    names.add(td)
                continue
            fn = td.get("function") or {}
            n = fn.get("name") or ""
            if n and n not in bridge:
                names.add(n)
        return frozenset(names)

    ts.resolve_underlying_call = _patched_resolve
    ts.scoped_deferrable_names = _patched_scoped_names
    ms._repair_tool_call_arguments = _patched_repair_args
    rebound = _rebind_repair_consumers(_orig_repair, _patched_repair_args)

    # tool_executor imported resolve/scoped by attribute lookup on ts module
    # at call time in most paths (import tools.tool_search as _ts) — good.
    # Rebind any local copies if present.
    if hasattr(te, "resolve_underlying_call"):
        te.resolve_underlying_call = _patched_resolve
    if hasattr(te, "scoped_deferrable_names"):
        te.scoped_deferrable_names = _patched_scoped_names

    _PATCHED = True
    log.info(
        "tool-call-coherency: patches installed (arg-repair rebound in %d module(s))", rebound
    )


def register(ctx) -> None:
    """Hermes plugin entrypoint."""
    _install_patches()
    # Expose stats for diagnostics via a no-op hook attachment marker
    try:
        ctx.register_hook(
            "on_session_end",
            lambda **kwargs: log.info(
                "tool-call-coherency stats: %s", _STATS
            ),
        )
    except Exception:
        # older hook names — ignore
        pass


# Allow import-time install for smoke tests
def install() -> None:
    _install_patches()
