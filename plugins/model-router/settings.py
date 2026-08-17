"""Load model-router settings from config.json and env overlays.

Exactly three named models: low < medium < high. Override models,
providers, labels, final-voice, and rest behaviour without editing
plugin code.

Resolution order (later wins per key):
  1. built-in defaults
  2. plugin-adjacent config.json
  3. MODEL_ROUTER_CONFIG path (JSON)
  4. MODEL_ROUTER_{LOW,MEDIUM,HIGH}_{MODEL,PROVIDER,LABEL}
     MODEL_ROUTER_FINAL / FINAL_VOICE / REST_ON_HIGH
"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

_PLUGIN_DIR = Path(__file__).resolve().parent

NAMES: tuple[str, ...] = ("low", "medium", "high")
RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2}

DEFAULT_MODELS: dict[str, dict[str, Any]] = {
    "low": {
        "label": "Low",
        "short": "Low",
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
    "medium": {
        "label": "Medium",
        "short": "Medium",
        "model": "deepseek-v4-pro",
        "provider": "deepseek",
        "role": "default workhorse",
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
    "high": {
        "label": "High",
        "short": "High",
        "model": "grok-4.6",
        "provider": "xai-oauth",
        "role": "high-stakes + final voice",
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


class SettingsError(ValueError):
    """Invalid model-router configuration."""


def as_name(raw: Any) -> str | None:
    """Map a config/env/classifier token onto low|medium|high, or None."""
    if raw is None:
        return None
    name = str(raw).strip().lower()
    return name if name in RANK else None


def _truthy(raw: str | None, default: bool) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def _coerce_models_map(raw: Any, *, origin: str) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    extra: list[str] = []
    for key, meta in raw.items():
        name = as_name(key)
        if name is None:
            extra.append(str(key))
            continue
        if isinstance(meta, dict):
            out[name] = dict(meta)
    if extra:
        raise SettingsError(
            f"model-router: {origin} declares unknown models {extra}; "
            "only low, medium, high are allowed"
        )
    if len(raw) > 3 or len(out) > 3:
        raise SettingsError(
            f"model-router: {origin} declares {len(raw)} models; exactly 3 are allowed"
        )
    return out


def _deep_merge(
    base: dict[str, dict[str, Any]], overlay: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    merged = deepcopy(base)
    for name, meta in overlay.items():
        if name in merged:
            merged[name] = {**merged[name], **meta}
        else:
            merged[name] = dict(meta)
    return merged


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _generated_classifier(models: dict[str, dict[str, Any]]) -> str:
    lines = [
        "You assign a single WORK routing model for the user's message.",
        "(The final user-facing reply may still be polished separately.)",
        "",
    ]
    for name in NAMES:
        meta = models[name]
        label = meta.get("label") or name.capitalize()
        role = meta.get("role") or ""
        best = meta.get("best_for") or []
        extra = "; ".join(str(x) for x in best[:8]) if best else role
        lines.append(f"{name} = {label} — {extra}")
    lines.extend(
        [
            "",
            "Rules:",
            "- When unsure between low and medium, pick medium for real work; pick low only for trivial turns.",
            "- When unsure between medium and high, pick medium unless architecture/security/high-stakes fits.",
            "- high is uncommon but not vanishingly rare.",
            "- if user message is >1 sentence, strongly consider medium.",
            "- Multi-sentence questions, critiques, and follow-ups are real work, not triage.",
            "- Respond with ONLY one word: low, medium, or high.",
        ]
    )
    return "\n".join(lines)


def _generated_final_voice(models: dict[str, dict[str, Any]], final: str) -> str:
    meta = models.get(final) or {}
    label = meta.get("label") or final.capitalize()
    return (
        f"You are the final user-facing voice ({label}). Rewrite the draft assistant "
        "reply for the user.\n"
        "Preserve every fact, path, command, URL, code block, number, and decision exactly.\n"
        "Improve clarity, structure, and voice. Do not invent new claims. Do not mention "
        "models, routing, or that a draft existed. Output ONLY the final reply.\n"
    )


def _apply_file(data: dict[str, Any], state: dict[str, Any], *, origin: str) -> None:
    if "models" in data:
        state["models"] = _deep_merge(
            state["models"], _coerce_models_map(data["models"], origin=f"{origin}.models")
        )
    if "provider_hosts" in data and isinstance(data["provider_hosts"], dict):
        for prov, spec in data["provider_hosts"].items():
            if isinstance(spec, dict):
                state["provider_hosts"][str(prov)] = {
                    "forbid": list(spec.get("forbid") or []),
                    "prefer": list(spec.get("prefer") or []),
                }
    if "final" in data:
        name = as_name(data["final"])
        if name:
            state["final"] = name
    if "final_voice" in data:
        state["final_voice"] = bool(data["final_voice"])
    if "rest_on_high" in data:
        state["rest_on_high"] = bool(data["rest_on_high"])
    if "escalate_max" in data:
        name = as_name(data["escalate_max"])
        if name:
            state["escalate_max"] = name
    if "escalation_errors" in data and isinstance(data["escalation_errors"], dict):
        errors: dict[str, int] = {}
        extra: list[str] = []
        for key, val in data["escalation_errors"].items():
            name = as_name(key)
            if name is None:
                extra.append(str(key))
                continue
            errors[name] = int(val)
        if extra:
            raise SettingsError(
                f"model-router: {origin}.escalation_errors has unknown keys {extra}; "
                "only low, medium, high are allowed"
            )
        state["escalation_errors"] = errors
    if "skip_platforms" in data and isinstance(data["skip_platforms"], list):
        state["skip_platforms"] = [str(x) for x in data["skip_platforms"]]
    if data.get("classifier_system"):
        state["classifier_system"] = str(data["classifier_system"])
    if data.get("final_voice_system"):
        state["final_voice_system"] = str(data["final_voice_system"])


def load_settings() -> dict[str, Any]:
    state: dict[str, Any] = {
        "models": deepcopy(DEFAULT_MODELS),
        "provider_hosts": deepcopy(DEFAULT_PROVIDER_HOSTS),
        "final": "high",
        "final_voice": False,
        "rest_on_high": True,
        "escalate_max": "high",
        "escalation_errors": {"low": 4, "medium": 3},
        "skip_platforms": ["cron", "subagent"],
        "classifier_system": None,
        "final_voice_system": None,
    }

    for candidate, origin in (
        (_PLUGIN_DIR / "config.json", "config.json"),
        (
            Path(os.environ["MODEL_ROUTER_CONFIG"]) if os.environ.get("MODEL_ROUTER_CONFIG") else None,
            "MODEL_ROUTER_CONFIG",
        ),
    ):
        if candidate is None:
            continue
        data = _load_json(candidate)
        if data:
            _apply_file(data, state, origin=origin)

    models = state["models"]
    extra = [name for name in models if name not in RANK]
    if extra:
        raise SettingsError(
            f"model-router: unknown models {extra}; only low, medium, high are allowed"
        )
    if len(models) > 3:
        raise SettingsError("model-router: a fourth model is not allowed")

    env_slots = (
        ("low", "MODEL_ROUTER_LOW_"),
        ("medium", "MODEL_ROUTER_MEDIUM_"),
        ("high", "MODEL_ROUTER_HIGH_"),
    )
    for name, prefix in env_slots:
        model = os.environ.get(prefix + "MODEL")
        provider = os.environ.get(prefix + "PROVIDER")
        label = os.environ.get(prefix + "LABEL")
        if model:
            models[name]["model"] = model.strip()
        if provider:
            models[name]["provider"] = provider.strip()
        if label:
            models[name]["label"] = label.strip()
            models[name].setdefault("short", label.strip().split()[0])

    env_final = os.environ.get("MODEL_ROUTER_FINAL")
    if env_final:
        name = as_name(env_final)
        if name:
            state["final"] = name
    state["final_voice"] = _truthy(os.environ.get("MODEL_ROUTER_FINAL_VOICE"), state["final_voice"])
    if os.environ.get("MODEL_ROUTER_REST_ON_HIGH") is not None:
        state["rest_on_high"] = _truthy(os.environ.get("MODEL_ROUTER_REST_ON_HIGH"), state["rest_on_high"])

    if state["final"] not in models:
        state["final"] = "high" if "high" in models else next(iter(models))
    if state["escalate_max"] not in models:
        state["escalate_max"] = "high" if "high" in models else state["final"]

    for name, meta in models.items():
        meta.setdefault("short", name.capitalize())
        meta.setdefault("label", name.capitalize())
        meta.setdefault("role", "")
        meta.setdefault("best_for", [])

    if not state["classifier_system"]:
        state["classifier_system"] = _generated_classifier(models)
    if not state["final_voice_system"]:
        state["final_voice_system"] = _generated_final_voice(models, state["final"])

    return state


_SETTINGS = load_settings()

MODELS: dict[str, dict[str, Any]] = _SETTINGS["models"]
FINAL: str = _SETTINGS["final"]
FINAL_VOICE: bool = _SETTINGS["final_voice"]
REST_ON_HIGH: bool = _SETTINGS["rest_on_high"]
ESCALATE_MAX: str = _SETTINGS["escalate_max"]
ESCALATION_ERRORS: dict[str, int] = _SETTINGS["escalation_errors"]
SKIP_PLATFORMS: frozenset[str] = frozenset(_SETTINGS["skip_platforms"])
PROVIDER_HOSTS: dict[str, dict[str, list[str]]] = _SETTINGS["provider_hosts"]
CLASSIFIER: str = _SETTINGS["classifier_system"]
FINAL_VOICE_SYSTEM: str = _SETTINGS["final_voice_system"]


def webui_models() -> list[dict[str, str]]:
    out = []
    for name in NAMES:
        if name not in MODELS:
            continue
        meta = MODELS[name]
        label = str(meta.get("label") or name.capitalize())
        out.append(
            {
                "cmd": f"/{name}",
                "label": label,
                "short": str(meta.get("short") or name.capitalize()),
                "model": str(meta.get("model") or ""),
                "title": f"Pin {label}",
            }
        )
    out.append(
        {
            "cmd": "/auto",
            "label": "Auto",
            "short": "Auto",
            "model": "",
            "title": "Resume per-turn routing",
        }
    )
    return out
