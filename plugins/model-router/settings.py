"""model-router settings — three named tiers (low < medium < high).

Models, providers, and routing behaviour live here as defaults, overridable
via a plugin-adjacent config.json, a MODEL_ROUTER_CONFIG path, or
MODEL_ROUTER_* env vars. No Hermes/WebUI core files are edited.

The `best_for` list on each tier is the single source of truth for the
classifier prompt — nothing else steers it.
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
        "best_for": [
            "Short acknowledgements",
            "Status checks",
            "Trivial Q&A / look-ups",
            "Title generation",
        ],
    },
    "medium": {
        "label": "Medium",
        "short": "Medium",
        "model": "deepseek-v4-pro",
        "provider": "deepseek",
        "best_for": [
            "Default day-to-day work",
            "Coding, research, debugging",
            "Code review",
            "Large-document synthesis",
        ],
    },
    "high": {
        "label": "High",
        "short": "High",
        "model": "grok-4.6",
        "provider": "xai-oauth",
        "best_for": [
            "Architecture",
            "Migration planning",
            "Complex multi-step design",
            "Security-sensitive analysis",
            "Monetary transactions",
        ],
    },
}

# provider -> host heuristics for half-switch repair (model set, old API host).
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


def as_best_for(raw: Any) -> list[str]:
    """Coerce a config/env best_for value to a list of descriptors."""
    if raw is None:
        return []
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                raw = json.loads(text)
            except json.JSONDecodeError:
                return [text]
        else:
            return [part.strip() for part in text.split(";") if part.strip()]
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    return [str(raw).strip()] if str(raw).strip() else []


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
    """Build the triage prompt from each tier's `best_for` list — nothing else."""
    lines = ["Pick the model for this turn.", ""]
    for name in NAMES:
        meta = models[name]
        label = meta.get("label") or name.capitalize()
        best = meta.get("best_for") or []
        extra = "; ".join(str(x) for x in best) if best else label
        lines.append(f"{name} = {label} — {extra}")
    lines.extend(["", "Respond with ONLY one word: low, medium, or high."])
    return "\n".join(lines)


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


def load_settings() -> dict[str, Any]:
    state: dict[str, Any] = {
        "models": deepcopy(DEFAULT_MODELS),
        "provider_hosts": deepcopy(DEFAULT_PROVIDER_HOSTS),
        "escalate_max": "high",
        "escalation_errors": {"low": 4, "medium": 3},
        "skip_platforms": ["cron", "subagent"],
        "classifier_system": None,
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

    for name, prefix in (
        ("low", "MODEL_ROUTER_LOW_"),
        ("medium", "MODEL_ROUTER_MEDIUM_"),
        ("high", "MODEL_ROUTER_HIGH_"),
    ):
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
        raw_best = os.environ.get(prefix + "BEST_FOR")
        if raw_best and raw_best.strip():
            models[name]["best_for"] = as_best_for(raw_best)

    if state["escalate_max"] not in models:
        state["escalate_max"] = "high" if "high" in models else next(iter(models))

    for name, meta in models.items():
        meta.setdefault("short", name.capitalize())
        meta.setdefault("label", name.capitalize())
        meta["best_for"] = as_best_for(meta.get("best_for"))

    if not state["classifier_system"]:
        state["classifier_system"] = _generated_classifier(models)

    return state


_SETTINGS = load_settings()

MODELS: dict[str, dict[str, Any]] = _SETTINGS["models"]
ESCALATE_MAX: str = _SETTINGS["escalate_max"]
ESCALATION_ERRORS: dict[str, int] = _SETTINGS["escalation_errors"]
SKIP_PLATFORMS: frozenset[str] = frozenset(_SETTINGS["skip_platforms"])
PROVIDER_HOSTS: dict[str, dict[str, list[str]]] = _SETTINGS["provider_hosts"]
CLASSIFIER: str = _SETTINGS["classifier_system"]


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
