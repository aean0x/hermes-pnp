"""Load model-router settings from config.json and env overlays.

Defaults are a 3-tier cheap / work / voice map. Override models, providers,
labels, final-voice, and rest behaviour without editing plugin code.

Resolution order (later wins per key):
  1. built-in defaults
  2. plugin-adjacent config.json
  3. MODEL_ROUTER_CONFIG path (JSON)
  4. MODEL_ROUTER_T{n}_MODEL / _PROVIDER / _LABEL
     MODEL_ROUTER_FINAL_TIER / FINAL_VOICE / REST_ON_FINAL
"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

_PLUGIN_DIR = Path(__file__).resolve().parent

DEFAULT_TIERS: dict[int, dict[str, Any]] = {
    1: {
        "label": "T1 Flash",
        "short": "T1",
        "model": "deepseek-v4-flash",
        "provider": "deepseek",
        "role": "fast triage + cheap helper",
        "best_for": [
            "Short acknowledgements",
            "Intent classification",
            "Status checks",
            "Title generation",
            "Trivial Q&A / look-ups",
            "Documentation and drafting",
        ],
    },
    2: {
        "label": "T2 Pro",
        "short": "T2",
        "model": "deepseek-v4-pro",
        "provider": "deepseek",
        "role": "default workhorse — coding, review, docs",
        "best_for": [
            "Default day-to-day work",
            "Standard coding and research",
            "Debugging",
            "Code review",
            "Large-document synthesis",
            "Complex analysis",
            "Nuanced code review",
            "Algorithmic optimization",
            "Multi-file implementation",
        ],
    },
    3: {
        "label": "T3 Voice",
        "short": "T3",
        "model": "grok-4.6",
        "provider": "xai-oauth",
        "role": "high-stakes + final user-facing voice",
        "best_for": [
            "Architecture",
            "Migration planning",
            "Complex multi-step design",
            "Security-sensitive analysis",
            "High-stakes reasoning",
            "Monetary transactions",
            "Final user-facing response",
        ],
    },
}

# Provider → host heuristics for half-switch repair (model name set, old API host).
DEFAULT_PROVIDER_HOSTS: dict[str, dict[str, list[str]]] = {
    "deepseek": {"forbid": ["x.ai", "xai"], "prefer": ["deepseek"]},
    "deepseek-chat": {"forbid": ["x.ai", "xai"], "prefer": ["deepseek"]},
    "xai": {"forbid": ["deepseek.com"], "prefer": ["x.ai", "xai"]},
    "xai-oauth": {"forbid": ["deepseek.com"], "prefer": ["x.ai", "xai"]},
    "x-ai": {"forbid": ["deepseek.com"], "prefer": ["x.ai", "xai"]},
}


def _truthy(raw: str | None, default: bool) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def _as_int_keys(tiers: Any) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    if not isinstance(tiers, dict):
        return out
    for key, meta in tiers.items():
        try:
            n = int(key)
        except (TypeError, ValueError):
            continue
        if isinstance(meta, dict):
            out[n] = dict(meta)
    return out


def _deep_merge(base: dict[int, dict[str, Any]], overlay: dict[int, dict[str, Any]]) -> dict[int, dict[str, Any]]:
    merged = deepcopy(base)
    for n, meta in overlay.items():
        if n in merged:
            merged[n] = {**merged[n], **meta}
        else:
            merged[n] = dict(meta)
    return merged


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _generated_classifier(tiers: dict[int, dict[str, Any]]) -> str:
    lines = [
        "You assign a single WORK routing tier for the user's message.",
        "(The final user-facing reply may still be polished separately.)",
        "",
    ]
    nums = sorted(tiers)
    for n in nums:
        meta = tiers[n]
        label = meta.get("label") or f"T{n}"
        role = meta.get("role") or ""
        best = meta.get("best_for") or []
        extra = "; ".join(str(x) for x in best[:8]) if best else role
        lines.append(f"{n} = {label} — {extra}")
    lo, hi = nums[0], nums[-1]
    mid = nums[1] if len(nums) > 2 else hi
    lines.extend(
        [
            "",
            "Rules:",
            f"- When unsure between {lo} and {mid}, pick {mid} for real work; pick {lo} only for trivial turns.",
            f"- When unsure between {mid} and {hi}, pick {mid} unless architecture/security/high-stakes fits.",
            f"- Tier {hi} is uncommon but not vanishingly rare.",
            f"- if user message is >1 sentence, strongly consider {mid}.",
            "- Multi-sentence questions, critiques, and follow-ups are real work, not triage.",
            f"- Respond with ONLY a digit: {', '.join(str(n) for n in nums)}.",
        ]
    )
    return "\n".join(lines)


def _generated_final_voice(tiers: dict[int, dict[str, Any]], final_tier: int) -> str:
    meta = tiers.get(final_tier) or {}
    label = meta.get("label") or f"T{final_tier}"
    return (
        f"You are the final user-facing voice ({label}). Rewrite the draft assistant "
        "reply for the user.\n"
        "Preserve every fact, path, command, URL, code block, number, and decision exactly.\n"
        "Improve clarity, structure, and voice. Do not invent new claims. Do not mention "
        "models, tiers, routing, or that a draft existed. Output ONLY the final reply.\n"
    )


def load_settings() -> dict[str, Any]:
    tiers = deepcopy(DEFAULT_TIERS)
    provider_hosts = deepcopy(DEFAULT_PROVIDER_HOSTS)
    final_tier = 3
    final_voice = True
    rest_on_final = True
    escalate_max = 3
    escalation_errors = {1: 4, 2: 3}
    skip_platforms = ["cron", "subagent"]
    classifier_system = None
    final_voice_system = None

    for candidate in (
        _PLUGIN_DIR / "config.json",
        Path(os.environ["MODEL_ROUTER_CONFIG"]) if os.environ.get("MODEL_ROUTER_CONFIG") else None,
    ):
        if candidate is None:
            continue
        data = _load_json(candidate)
        if not data:
            continue
        if "tiers" in data:
            tiers = _deep_merge(tiers, _as_int_keys(data["tiers"]))
        if "provider_hosts" in data and isinstance(data["provider_hosts"], dict):
            for prov, spec in data["provider_hosts"].items():
                if isinstance(spec, dict):
                    provider_hosts[str(prov)] = {
                        "forbid": list(spec.get("forbid") or []),
                        "prefer": list(spec.get("prefer") or []),
                    }
        if "final_tier" in data:
            try:
                final_tier = int(data["final_tier"])
            except (TypeError, ValueError):
                pass
        if "final_voice" in data:
            final_voice = bool(data["final_voice"])
        if "rest_on_final_tier" in data:
            rest_on_final = bool(data["rest_on_final_tier"])
        if "escalate_max" in data:
            try:
                escalate_max = int(data["escalate_max"])
            except (TypeError, ValueError):
                pass
        if "escalation_errors" in data and isinstance(data["escalation_errors"], dict):
            escalation_errors = {
                int(k): int(v) for k, v in data["escalation_errors"].items()
            }
        if "skip_platforms" in data and isinstance(data["skip_platforms"], list):
            skip_platforms = [str(x) for x in data["skip_platforms"]]
        if data.get("classifier_system"):
            classifier_system = str(data["classifier_system"])
        if data.get("final_voice_system"):
            final_voice_system = str(data["final_voice_system"])

    for n in list(tiers):
        prefix = f"MODEL_ROUTER_T{n}_"
        model = os.environ.get(prefix + "MODEL")
        provider = os.environ.get(prefix + "PROVIDER")
        label = os.environ.get(prefix + "LABEL")
        if model:
            tiers[n]["model"] = model.strip()
        if provider:
            tiers[n]["provider"] = provider.strip()
        if label:
            tiers[n]["label"] = label.strip()
            tiers[n].setdefault("short", label.strip().split()[0])

    env_final = os.environ.get("MODEL_ROUTER_FINAL_TIER")
    if env_final:
        try:
            final_tier = int(env_final)
        except ValueError:
            pass
    final_voice = _truthy(os.environ.get("MODEL_ROUTER_FINAL_VOICE"), final_voice)
    rest_on_final = _truthy(os.environ.get("MODEL_ROUTER_REST_ON_FINAL"), rest_on_final)

    if final_tier not in tiers:
        final_tier = max(tiers) if tiers else 3
    escalate_max = max(tiers) if tiers else escalate_max

    for n, meta in tiers.items():
        meta.setdefault("short", f"T{n}")
        meta.setdefault("label", f"T{n}")
        meta.setdefault("role", "")
        meta.setdefault("best_for", [])

    if not classifier_system:
        classifier_system = _generated_classifier(tiers)
    if not final_voice_system:
        final_voice_system = _generated_final_voice(tiers, final_tier)

    return {
        "tiers": tiers,
        "final_tier": final_tier,
        "final_voice": final_voice,
        "rest_on_final_tier": rest_on_final,
        "escalate_max": escalate_max,
        "escalation_errors": escalation_errors,
        "skip_platforms": frozenset(skip_platforms),
        "provider_hosts": provider_hosts,
        "classifier_system": classifier_system,
        "final_voice_system": final_voice_system,
    }


_SETTINGS = load_settings()

TIERS: dict[int, dict[str, Any]] = _SETTINGS["tiers"]
FINAL_TIER: int = _SETTINGS["final_tier"]
FINAL_VOICE: bool = _SETTINGS["final_voice"]
REST_ON_FINAL: bool = _SETTINGS["rest_on_final_tier"]
ESCALATE_MAX: int = _SETTINGS["escalate_max"]
ESCALATION_ERROR_THRESHOLD_BY_TIER: dict[int, int] = _SETTINGS["escalation_errors"]
SKIP_PLATFORMS: frozenset[str] = _SETTINGS["skip_platforms"]
PROVIDER_HOSTS: dict[str, dict[str, list[str]]] = _SETTINGS["provider_hosts"]
CLASSIFIER: str = _SETTINGS["classifier_system"]
FINAL_VOICE_SYSTEM: str = _SETTINGS["final_voice_system"]
TIER_DIGITS = "".join(str(n) for n in sorted(TIERS))
MIN_TIER = min(TIERS) if TIERS else 1
MAX_TIER = max(TIERS) if TIERS else 3


def webui_tiers() -> list[dict[str, str]]:
    out = []
    for n, meta in sorted(TIERS.items()):
        out.append(
            {
                "cmd": f"/t{n}",
                "label": str(meta.get("label") or f"T{n}"),
                "short": str(meta.get("short") or f"T{n}"),
                "model": str(meta.get("model") or ""),
                "title": f"Pin {meta.get('label') or f'T{n}'}",
            }
        )
    return out
