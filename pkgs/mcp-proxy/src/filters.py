"""Declarative MCP tool/argument policy. No I/O — apply() is pure."""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from typing import Any


class Denied(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


DENY_COOLDOWN_S = 60

_HARD_STOP_RE = re.compile(
    r"do\s*not\s*retry|don'?t\s*retry|policy[_\s-]?denied|"
    r"permanent\s+(auth|failure|denial)|not\s+authenticated|"
    r"access\s+denied|authorization\s+failed|tool\s+\S+\s+is\s+denied",
    re.IGNORECASE,
)

_TRANSIENT_RE = re.compile(
    r"rate\s*limit|try\s+again|temporar|timeout|503|429|unavailable",
    re.IGNORECASE,
)


def denial_result(rpc_id: Any, reason: str) -> dict[str, Any]:
    """JSON-RPC *result* for a policy denial — not a protocol error.

    A JSON-RPC error looks like a transport failure and the agent retries
    it. A completed tools/call result is the below-model stop for this
    turn. Identical arguments are held for DENY_COOLDOWN_S, then a new
    attempt is allowed — this is not a process-lifetime lockout.
    """
    text = (
        f"POLICY_DENIED: {reason}. "
        f"Do not retry this call immediately. The same arguments are "
        f"held for {DENY_COOLDOWN_S}s, then a new attempt is allowed."
    )
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "result": {
            "content": [{"type": "text", "text": text}],
            "isError": False,
            "structuredContent": {
                "denied": True,
                "retry": False,
                "cooldown_s": DENY_COOLDOWN_S,
                "reason": reason,
            },
        },
    }


def result_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    result = payload.get("result")
    if not isinstance(result, dict):
        return ""
    chunks: list[str] = []
    content = result.get("content")
    if isinstance(content, str):
        chunks.append(content)
    elif isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("text"):
                chunks.append(str(item.get("text") or ""))
            elif isinstance(item, str):
                chunks.append(item)
    for key in ("message", "error"):
        val = result.get(key)
        if isinstance(val, str):
            chunks.append(val)
    return "\n".join(chunks)


def looks_like_hard_stop(text: str) -> bool:
    body = (text or "").strip()
    if not body:
        return False
    if _TRANSIENT_RE.search(body) and not _HARD_STOP_RE.search(body):
        return False
    if body.startswith("POLICY_DENIED:"):
        return True
    return bool(_HARD_STOP_RE.search(body))


def rewrite_call_result_if_denied(payload: Any) -> dict[str, Any] | None:
    """If an upstream tools/call result is a hard stop, normalize it."""
    if not isinstance(payload, dict):
        return None
    result = payload.get("result")
    if not isinstance(result, dict):
        return None
    if "content" not in result and not result.get("isError"):
        return None
    text = result_text(payload)
    if not looks_like_hard_stop(text):
        return None
    if text.startswith("POLICY_DENIED:"):
        return None
    reason = text.strip().splitlines()[0][:240]
    return denial_result(payload.get("id"), reason)


@dataclass
class Decision:
    arguments: dict[str, Any]
    notes: list[str] = field(default_factory=list)


def _listed(name: str, patterns: list[str] | None) -> bool:
    if not name or not patterns:
        return False
    return any(fnmatch.fnmatch(name, pat) for pat in patterns)


def name_matches(name: str, match: dict[str, Any] | None) -> bool:
    if not name or not match:
        return False
    prefix = match.get("prefix")
    if prefix and name.startswith(prefix):
        return True
    suffix = match.get("suffix")
    if suffix and name.endswith(suffix):
        return True
    names = match.get("names")
    if names and name in names:
        return True
    glob = match.get("glob")
    if glob and fnmatch.fnmatch(name, glob):
        return True
    regex = match.get("regex")
    if regex and re.search(regex, name):
        return True
    return False


def get_path(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def rewrite_tokens(value: str, require: list[str], deny: list[str]) -> str:
    parts = value.split() if value else []
    if deny:
        parts = [part for part in parts if part not in deny]
    for token in require:
        if token not in parts:
            parts.append(token)
    return " ".join(parts)


def apply_field(args: dict[str, Any], field_name: str, rule: dict[str, Any], notes: list[str]) -> None:
    if rule.get("unset"):
        if field_name in args:
            args.pop(field_name, None)
            notes.append(f"unset {field_name}")
        return

    if "set" in rule:
        args[field_name] = rule["set"]
        notes.append(f"set {field_name}")
        return

    if rule.get("denyIfPresent") and field_name in args:
        raise Denied(f"argument {field_name} is not allowed")

    current = args.get(field_name)
    if current in (None, "") and "default" in rule:
        args[field_name] = rule["default"]
        current = args[field_name]
        notes.append(f"default {field_name}")

    deny_values = rule.get("denyValues") or []
    if deny_values and current is not None:
        if isinstance(current, list):
            filtered = [item for item in current if item not in deny_values]
            if filtered != current:
                args[field_name] = filtered
                current = filtered
                notes.append(f"denyValues {field_name}")
        elif current in deny_values:
            raise Denied(f"argument {field_name} value is denied")

    require_values = rule.get("requireValues") or []
    if require_values:
        existing = args.get(field_name)
        if existing is None:
            args[field_name] = list(require_values)
            notes.append(f"requireValues {field_name}")
        elif isinstance(existing, list):
            changed = False
            for item in require_values:
                if item not in existing:
                    existing.append(item)
                    changed = True
            if changed:
                notes.append(f"requireValues {field_name}")
        else:
            raise Denied(f"argument {field_name} must be an array for requireValues")

    deny_tokens = rule.get("denyTokens") or []
    require_tokens = rule.get("requireTokens") or []
    if deny_tokens or require_tokens:
        raw = args.get(field_name)
        if raw is None:
            raw = ""
        if not isinstance(raw, str):
            raise Denied(f"argument {field_name} must be a string for token rules")
        rewritten = rewrite_tokens(raw, require_tokens, deny_tokens)
        if rewritten != raw:
            args[field_name] = rewritten
            notes.append(f"tokens {field_name}")

    if "append" in rule:
        raw = args.get(field_name)
        if raw is None:
            args[field_name] = rule["append"]
        elif isinstance(raw, str):
            args[field_name] = raw + rule["append"]
        else:
            raise Denied(f"argument {field_name} must be a string for append")
        notes.append(f"append {field_name}")

    if "prepend" in rule:
        raw = args.get(field_name)
        if raw is None:
            args[field_name] = rule["prepend"]
        elif isinstance(raw, str):
            args[field_name] = rule["prepend"] + raw
        else:
            raise Denied(f"argument {field_name} must be a string for prepend")
        notes.append(f"prepend {field_name}")


def _surface_allowed(name: str, tools_cfg: dict[str, Any] | None) -> None:
    if not tools_cfg:
        return
    allow = tools_cfg.get("allow") or []
    deny = tools_cfg.get("deny") or []
    if deny and _listed(name, deny):
        raise Denied(f"tool {name} is denied")
    if allow and not _listed(name, allow):
        raise Denied(f"tool {name} is not on the allowlist")


def _toolkit_for(name: str, toolkits: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    for tk_name, tk in toolkits.items():
        match = tk.get("match") or {}
        if "prefix" in tk and "prefix" not in match:
            match = {**match, "prefix": tk["prefix"]}
        if name_matches(name, match):
            return tk_name, tk
    return None


def apply_tool_args(name: str, arguments: dict[str, Any], toolkits: dict[str, Any], notes: list[str]) -> None:
    hit = _toolkit_for(name, toolkits)
    if hit is None:
        return
    tk_name, tk = hit
    allow = tk.get("allow") or []
    deny = tk.get("deny") or []
    if deny and _listed(name, deny):
        raise Denied(f"tool {name} denied by toolkit {tk_name}")
    if allow and not _listed(name, allow):
        raise Denied(f"tool {name} not allowlisted in toolkit {tk_name}")

    rules = dict(tk.get("args") or {})
    by_tool = (tk.get("byTool") or {}).get(name) or {}
    if by_tool.get("deny"):
        raise Denied(f"tool {name} denied by toolkit {tk_name}")
    for field_name, rule in (by_tool.get("args") or {}).items():
        base = dict(rules.get(field_name) or {})
        base.update(rule)
        rules[field_name] = base

    for field_name, rule in rules.items():
        apply_field(arguments, field_name, rule, notes)


def _ensure_args(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        import json

        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise Denied("arguments is not valid JSON") from exc
    if not isinstance(value, dict):
        raise Denied("arguments must be an object")
    return value


def iter_effective_calls(
    mcp_name: str, arguments: dict[str, Any], unwraps: list[dict[str, Any]]
) -> list[tuple[str, dict[str, Any]]]:
    for spec in unwraps:
        tool_pat = spec.get("tool")
        if not tool_pat or not _listed(mcp_name, [tool_pat]):
            continue
        each = spec.get("each")
        name_key = spec.get("name") or "name"
        args_key = spec.get("args") or "arguments"
        if each:
            items = get_path(arguments, each)
            if not isinstance(items, list):
                raise Denied(f"unwrap {each} is not an array")
            out: list[tuple[str, dict[str, Any]]] = []
            for item in items:
                if not isinstance(item, dict):
                    raise Denied("unwrap item is not an object")
                inner_name = item.get(name_key)
                if not isinstance(inner_name, str) or not inner_name:
                    raise Denied("unwrap item is missing a tool name")
                inner_args = _ensure_args(item.get(args_key))
                item[args_key] = inner_args
                out.append((inner_name, inner_args))
            return out
        inner_name = get_path(arguments, name_key)
        if not isinstance(inner_name, str) or not inner_name:
            raise Denied("unwrap is missing a tool name")
        inner_args = _ensure_args(get_path(arguments, args_key))
        parent = arguments
        parts = args_key.split(".")
        for part in parts[:-1]:
            nxt = parent.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                parent[part] = nxt
            parent = nxt
        parent[parts[-1]] = inner_args
        return [(inner_name, inner_args)]
    return [(mcp_name, arguments)]


def apply_call(mcp_name: str, arguments: Any, backend: dict[str, Any]) -> Decision:
    if not mcp_name:
        raise Denied("tools/call is missing a tool name")
    args = _ensure_args(arguments)
    notes: list[str] = []
    tools_cfg = backend.get("tools")
    _surface_allowed(mcp_name, tools_cfg)
    effective = iter_effective_calls(mcp_name, args, backend.get("unwrap") or [])
    toolkits = backend.get("toolkits") or {}
    deny = (tools_cfg or {}).get("deny") or []
    for inner_name, inner_args in effective:
        # tools.deny applies to unwrapped inner slugs (GMAIL_*, …) as well as
        # the surface MCP name. tools.allow stays surface-only so an allowlist
        # of COMPOSIO_MULTI_EXECUTE_TOOL does not block every nested call.
        if inner_name != mcp_name and deny and _listed(inner_name, deny):
            raise Denied(f"tool {inner_name} is denied")
        apply_tool_args(inner_name, inner_args, toolkits, notes)
    return Decision(arguments=args, notes=notes)


AUTH_INJECT_TAG = "[auth via proxy] "


def auth_mode(backend: dict[str, Any]) -> str:
    """Resolved mode: inject | passthrough."""
    explicit = ((backend.get("auth") or {}).get("mode") or "auto").lower()
    has_secrets = bool(backend.get("secrets"))
    if explicit == "passthrough":
        return "passthrough"
    if explicit == "inject":
        return "inject"
    return "inject" if has_secrets else "passthrough"


def is_injecting(backend: dict[str, Any]) -> bool:
    return auth_mode(backend) == "inject" and bool(backend.get("secrets"))


def inject_tag(backend: dict[str, Any]) -> str:
    if not is_injecting(backend):
        return ""
    auth = backend.get("auth") or {}
    if "tag" in auth:
        return auth.get("tag") or ""
    return AUTH_INJECT_TAG


def _apply_description(tool: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    if not spec:
        return tool
    current = tool.get("description") or ""
    if not isinstance(current, str):
        current = ""
    if "set" in spec and spec["set"] is not None:
        new = spec["set"]
    else:
        new = current
        if spec.get("prepend"):
            new = spec["prepend"] + new
        if spec.get("append"):
            new = new + spec["append"]
    if new == current:
        return tool
    out = dict(tool)
    out["description"] = new
    return out


def annotate_listed_tools(tools: list[dict[str, Any]], backend: dict[str, Any]) -> list[dict[str, Any]]:
    """Rewrite advertised tool descriptions (tools/list).

    Hermes tool_search's per-turn blurb is the first ~60 chars / first
    sentence of each description. A short inject tag is prepended only
    when this backend is actually injecting secrets.
    """
    adv = backend.get("advertise") or {}
    tag = inject_tag(backend)
    user_pre = adv.get("prepend") or ""
    if tag and user_pre.startswith(tag):
        tag = ""
    prepend = f"{tag}{user_pre}" if (tag or user_pre) else None
    shared = {k: adv[k] for k in ("append", "set") if adv.get(k)}
    if prepend:
        shared["prepend"] = prepend
    if not shared and not (adv.get("byTool")):
        return tools
    by_tool = adv.get("byTool") or {}
    out = []
    for tool in tools:
        name = tool.get("name") if isinstance(tool, dict) else None
        spec = dict(shared)
        if isinstance(name, str) and name in by_tool:
            extra = {k: v for k, v in (by_tool[name] or {}).items() if v}
            extra_pre = extra.pop("prepend", None)
            spec.update(extra)
            if extra_pre:
                spec["prepend"] = (spec.get("prepend") or "") + extra_pre
        out.append(_apply_description(tool, spec))
    return out


def filter_listed_tools(tools: list[dict[str, Any]], backend: dict[str, Any]) -> list[dict[str, Any]]:
    tools_cfg = backend.get("tools") or {}
    allow = tools_cfg.get("allow") or []
    deny = tools_cfg.get("deny") or []
    out = []
    for tool in tools:
        name = tool.get("name") if isinstance(tool, dict) else None
        if not isinstance(name, str):
            continue
        if deny and _listed(name, deny):
            continue
        if allow and not _listed(name, allow):
            continue
        out.append(tool)
    return annotate_listed_tools(out, backend)
