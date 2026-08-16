"""tool-call-coherency — heal misrouted tool_call / deferred-tool invocations.

Models frequently:
1. Double-wrap MCP: tool_call(name="tool_call", arguments={name: "mcp__…", …})
2. Route core tools through the bridge: tool_call(name="cronjob", …)
3. Treat skill names as tools: tool_call(name="retrieval-reflex")

Upstream resolve_underlying_call hard-errors on (1) and (2). This plugin
monkeypatches the resolver + session scope set so those become transparent
redispatches. No capability expansion beyond tools already in the session.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

log = logging.getLogger("hermes.plugins.tool_call_coherency")

_PATCHED = False
_STATS = {
    "unwrap_nest": 0,
    "core_via_bridge": 0,
    "skill_rewrite": 0,
    "mcp_prefix_allow": 0,
}


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
    import os

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


def _install_patches() -> None:
    global _PATCHED
    if _PATCHED:
        return

    import tools.tool_search as ts
    import agent.tool_executor as te

    bridge = set(getattr(ts, "BRIDGE_TOOL_NAMES", None) or {
        "tool_call",
        "tool_describe",
        "tool_search",
    })
    _orig_resolve = ts.resolve_underlying_call
    _orig_scoped = ts.scoped_deferrable_names

    def _patched_resolve(function_args: Dict[str, Any]):
        cleaned, peels = _peel_nested_bridge(function_args or {}, bridge)
        if peels:
            _STATS["unwrap_nest"] += peels
            log.info("tool-call-coherency: unwrapped nested bridge x%d", peels)

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

    # tool_executor imported resolve/scoped by attribute lookup on ts module
    # at call time in most paths (import tools.tool_search as _ts) — good.
    # Rebind any local copies if present.
    if hasattr(te, "resolve_underlying_call"):
        te.resolve_underlying_call = _patched_resolve
    if hasattr(te, "scoped_deferrable_names"):
        te.scoped_deferrable_names = _patched_scoped_names

    _PATCHED = True
    log.info("tool-call-coherency: patches installed")


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
