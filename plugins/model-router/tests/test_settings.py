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

_ENV_KEYS = (
    "MODEL_ROUTER_CONFIG",
    "MODEL_ROUTER_LOW_MODEL",
    "MODEL_ROUTER_LOW_PROVIDER",
    "MODEL_ROUTER_MEDIUM_MODEL",
    "MODEL_ROUTER_HIGH_MODEL",
    "MODEL_ROUTER_T1_MODEL",
    "MODEL_ROUTER_T3_MODEL",
    "MODEL_ROUTER_FINAL",
    "MODEL_ROUTER_FINAL_TIER",
    "MODEL_ROUTER_FINAL_VOICE",
    "MODEL_ROUTER_REST_ON_HIGH",
    "MODEL_ROUTER_REST_ON_FINAL",
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


def _load(name: str = "mr_settings"):
    spec = importlib.util.spec_from_file_location(name, ROOT / "settings.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Defaults(unittest.TestCase):
    def test_three_named_models_and_voice(self) -> None:
        with _clean_env():
            mod = _load("mr_defaults")
        self.assertEqual(mod.NAMES, ("low", "medium", "high"))
        self.assertIn("low", mod.MODELS)
        self.assertIn("medium", mod.MODELS)
        self.assertIn("high", mod.MODELS)
        self.assertEqual(mod.FINAL, "high")
        self.assertTrue(mod.FINAL_VOICE)
        self.assertTrue(mod.REST_ON_HIGH)
        self.assertEqual(mod.ESCALATE_MAX, "high")
        self.assertEqual(mod.ESCALATION_ERRORS["low"], 4)
        self.assertEqual(mod.ESCALATION_ERRORS["medium"], 3)
        self.assertNotIn("Archimedes", mod.FINAL_VOICE_SYSTEM)
        self.assertNotIn("rocknas", mod.CLASSIFIER.lower())
        self.assertIn("low, medium, or high", mod.CLASSIFIER)
        self.assertNotIn("ONLY a digit", mod.CLASSIFIER)
        self.assertNotIn("T1", mod.CLASSIFIER)
        cmds = [row["cmd"] for row in mod.webui_models()]
        self.assertEqual(cmds, ["/low", "/medium", "/high", "/auto"])
        labels = [row["label"] for row in mod.webui_models()[:3]]
        self.assertEqual(labels, ["Low", "Medium", "High"])


class EnvOverlay(unittest.TestCase):
    def test_named_env_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "cfg.json"
            cfg.write_text(
                json.dumps(
                    {
                        "models": {"high": {"model": "some-voice", "provider": "other"}},
                        "final_voice": False,
                    }
                ),
                encoding="utf-8",
            )
            with _clean_env(MODEL_ROUTER_CONFIG=str(cfg)):
                mod = _load("mr_named_overlay")
            self.assertEqual(mod.MODELS["high"]["model"], "some-voice")
            self.assertEqual(mod.MODELS["high"]["provider"], "other")
            self.assertFalse(mod.FINAL_VOICE)

    def test_low_model_env(self) -> None:
        with _clean_env(MODEL_ROUTER_LOW_MODEL="flash-override"):
            mod = _load("mr_low_env")
        self.assertEqual(mod.MODELS["low"]["model"], "flash-override")

    def test_t1_env_alias(self) -> None:
        with _clean_env(MODEL_ROUTER_T1_MODEL="legacy-flash"):
            mod = _load("mr_t1_env")
        self.assertEqual(mod.MODELS["low"]["model"], "legacy-flash")

    def test_named_env_wins_over_t1_alias(self) -> None:
        with _clean_env(
            MODEL_ROUTER_LOW_MODEL="named-wins",
            MODEL_ROUTER_T1_MODEL="alias-loses",
        ):
            mod = _load("mr_env_precedence")
        self.assertEqual(mod.MODELS["low"]["model"], "named-wins")


class LegacyConfig(unittest.TestCase):
    def test_old_tiers_map_to_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "cfg.json"
            cfg.write_text(
                json.dumps(
                    {
                        "tiers": {
                            "1": {"model": "old-flash", "provider": "deepseek"},
                            "2": {"model": "old-pro", "provider": "deepseek"},
                            "3": {"model": "old-voice", "provider": "xai-oauth"},
                        },
                        "final_tier": 3,
                        "rest_on_final_tier": True,
                        "escalate_max": 3,
                        "escalation_errors": {"1": 4, "2": 3},
                    }
                ),
                encoding="utf-8",
            )
            with _clean_env(MODEL_ROUTER_CONFIG=str(cfg)):
                mod = _load("mr_legacy_tiers")
            self.assertEqual(mod.MODELS["low"]["model"], "old-flash")
            self.assertEqual(mod.MODELS["medium"]["model"], "old-pro")
            self.assertEqual(mod.MODELS["high"]["model"], "old-voice")
            self.assertEqual(mod.FINAL, "high")
            self.assertTrue(mod.REST_ON_HIGH)
            self.assertEqual(mod.ESCALATION_ERRORS["low"], 4)

    def test_named_models_win_over_legacy_tiers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "cfg.json"
            cfg.write_text(
                json.dumps(
                    {
                        "tiers": {"1": {"model": "legacy-flash"}},
                        "models": {"low": {"model": "named-flash"}},
                    }
                ),
                encoding="utf-8",
            )
            with _clean_env(MODEL_ROUTER_CONFIG=str(cfg)):
                mod = _load("mr_named_wins")
            self.assertEqual(mod.MODELS["low"]["model"], "named-flash")


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

    def test_legacy_fourth_tier_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "cfg.json"
            cfg.write_text(
                json.dumps(
                    {
                        "tiers": {
                            "1": {"model": "a"},
                            "2": {"model": "b"},
                            "3": {"model": "c"},
                            "4": {"model": "d"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            with _clean_env(MODEL_ROUTER_CONFIG=str(cfg)):
                with self.assertRaises(Exception) as ctx:
                    _load("mr_fourth_tier")
            msg = str(ctx.exception)
            self.assertTrue("4" in msg or "unknown" in msg)


if __name__ == "__main__":
    unittest.main()
