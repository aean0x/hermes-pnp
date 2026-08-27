"""Unit tests for model-router settings (no Hermes)."""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_OOBE_IDS = ROOT / "tests" / "oobe-ids.json"

_ENV_KEYS = (
    "MODEL_ROUTER_CONFIG",
    "MODEL_ROUTER_LOW_MODEL",
    "MODEL_ROUTER_LOW_PROVIDER",
    "MODEL_ROUTER_LOW_BEST_FOR",
    "MODEL_ROUTER_MEDIUM_MODEL",
    "MODEL_ROUTER_HIGH_MODEL",
)


@contextmanager
def _clean_env(**overlay: str):
    old = {k: os.environ.get(k) for k in _ENV_KEYS}
    try:
        for k in _ENV_KEYS:
            os.environ.pop(k, None)
        os.environ.update(overlay)
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _load(name: str = "mr_settings", *, ids: bool = True):
    if ids and not os.environ.get("MODEL_ROUTER_CONFIG"):
        os.environ["MODEL_ROUTER_CONFIG"] = str(_OOBE_IDS)
    spec = importlib.util.spec_from_file_location(name, ROOT / "settings.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Defaults(unittest.TestCase):
    def test_three_named_models(self) -> None:
        with _clean_env():
            mod = _load("mr_defaults")
        self.assertEqual(mod.NAMES, ("low", "medium", "high"))
        self.assertIn("low", mod.MODELS)
        self.assertIn("medium", mod.MODELS)
        self.assertIn("high", mod.MODELS)
        self.assertEqual(mod.ESCALATE_MAX, "high")
        self.assertEqual(mod.ESCALATION_ERRORS["low"], 4)
        self.assertEqual(mod.ESCALATION_ERRORS["medium"], 3)
        self.assertNotIn("rocknas", mod.CLASSIFIER.lower())
        self.assertIn("low or medium or high", mod.CLASSIFIER)
        self.assertNotIn("ONLY a digit", mod.CLASSIFIER)
        self.assertNotIn("T1", mod.CLASSIFIER)
        self.assertFalse(hasattr(mod, "CLASSIFY_HIGH"))
        self.assertIn("prefer low", mod.CLASSIFIER)
        self.assertIn("Short single-file edits", mod.CLASSIFIER)
        self.assertIn("Small to medium-scoped research", mod.CLASSIFIER)
        self.assertIn("Broad-subject conceptual or deep research", mod.CLASSIFIER)
        self.assertIn("Monetary transactions", mod.CLASSIFIER)
        self.assertNotIn("Architecture", mod.CLASSIFIER)
        self.assertNotIn("Trivial Q&A", mod.CLASSIFIER)
        self.assertNotIn("Rules:", mod.CLASSIFIER)
        cmds = [row["cmd"] for row in mod.webui_models()]
        self.assertEqual(cmds, ["/low", "/medium", "/high", "/auto"])
        labels = [row["label"] for row in mod.webui_models()[:3]]
        self.assertEqual(labels, ["Quick", "Standard", "Expert"])

    def test_defaults_match_config_json(self) -> None:
        with _clean_env():
            mod = _load("mr_defaults_json")
        cfg = json.loads((ROOT / "config.default.json").read_text(encoding="utf-8"))
        for name in ("low", "medium", "high"):
            for key in ("label", "short", "best_for"):
                self.assertEqual(mod.MODELS[name][key], cfg["models"][name][key])
            self.assertNotIn("model", cfg["models"][name])
            self.assertNotIn("provider", cfg["models"][name])
        self.assertNotIn("classify_high", cfg)

    def test_settings_source_has_no_model_ids(self) -> None:
        src = (ROOT / "settings.py").read_text(encoding="utf-8")
        self.assertNotIn("deepseek-v4", src)
        self.assertNotIn("grok-4", src)
        self.assertNotIn("classify_high", src)
        self.assertNotIn("CLASSIFY_HIGH", src)

    def test_missing_model_ids_raise(self) -> None:
        with _clean_env():
            with self.assertRaises(Exception) as ctx:
                _load("mr_no_ids", ids=False)
        self.assertIn("no model id", str(ctx.exception))

    def test_declared_models_replace_catalog_ids(self) -> None:
        catalog = json.loads((ROOT / "config.default.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "cfg.json"
            cfg.write_text(
                json.dumps(
                    {
                        "models": {
                            "low": {"model": "cheap", "provider": "p-low"},
                            "medium": {"model": "work", "provider": "p-med"},
                            "high": {"model": "voice", "provider": "p-high"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            with _clean_env(MODEL_ROUTER_CONFIG=str(cfg)):
                mod = _load("mr_declared_overlay")
        self.assertEqual(mod.MODELS["low"]["model"], "cheap")
        self.assertEqual(mod.MODELS["medium"]["model"], "work")
        self.assertEqual(mod.MODELS["high"]["model"], "voice")
        self.assertEqual(mod.MODELS["high"]["provider"], "p-high")
        self.assertEqual(
            mod.MODELS["low"]["best_for"], catalog["models"]["low"]["best_for"]
        )


class EnvOverlay(unittest.TestCase):
    def test_named_env_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "cfg.json"
            cfg.write_text(
                json.dumps(
                    {
                        "models": {
                            "low": {"model": "test-low", "provider": "test"},
                            "medium": {"model": "test-medium", "provider": "test"},
                            "high": {"model": "some-voice", "provider": "other"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            with _clean_env(MODEL_ROUTER_CONFIG=str(cfg)):
                mod = _load("mr_named_overlay")
            self.assertEqual(mod.MODELS["high"]["model"], "some-voice")
            self.assertEqual(mod.MODELS["high"]["provider"], "other")

    def test_low_model_env(self) -> None:
        with _clean_env(MODEL_ROUTER_LOW_MODEL="flash-override"):
            mod = _load("mr_low_env")
        self.assertEqual(mod.MODELS["low"]["model"], "flash-override")


class RejectFourth(unittest.TestCase):
    def test_fourth_named_model_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "cfg.json"
            cfg.write_text(
                json.dumps(
                    {
                        "models": {
                            "low": {"model": "a"},
                            "medium": {"model": "b"},
                            "high": {"model": "c"},
                            "ultra": {"model": "d"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            with _clean_env(MODEL_ROUTER_CONFIG=str(cfg)):
                with self.assertRaises(Exception) as ctx:
                    _load("mr_fourth")
            self.assertIn("ultra", str(ctx.exception))


class BestFor(unittest.TestCase):
    def test_file_overlay_rebuilds_classifier(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "cfg.json"
            cfg.write_text(
                json.dumps(
                    {
                        "models": {
                            "low": {
                                "model": "test-low",
                                "provider": "test",
                                "best_for": ["Only pings"],
                            },
                            "medium": {"model": "test-medium", "provider": "test"},
                            "high": {
                                "model": "test-high",
                                "provider": "test",
                                "best_for": ["Only architecture"],
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            with _clean_env(MODEL_ROUTER_CONFIG=str(cfg)):
                mod = _load("mr_best_for_file")
        self.assertEqual(mod.MODELS["low"]["best_for"], ["Only pings"])
        self.assertIn("Only pings", mod.CLASSIFIER)
        self.assertIn("Only architecture", mod.CLASSIFIER)
        self.assertNotIn("Trivial Q&A", mod.CLASSIFIER)
        self.assertIn("Multi-step reasoning", mod.CLASSIFIER)

    def test_env_json_overlay(self) -> None:
        payload = json.dumps(["Status only"])
        with _clean_env(MODEL_ROUTER_LOW_BEST_FOR=payload):
            mod = _load("mr_best_for_env")
        self.assertEqual(mod.MODELS["low"]["best_for"], ["Status only"])
        self.assertIn("Status only", mod.CLASSIFIER)


if __name__ == "__main__":
    unittest.main()
