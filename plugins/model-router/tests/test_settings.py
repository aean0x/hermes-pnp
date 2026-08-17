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
        self.assertIn("low, medium, or high", mod.CLASSIFIER)
        self.assertNotIn("ONLY a digit", mod.CLASSIFIER)
        self.assertNotIn("T1", mod.CLASSIFIER)
        # best_for is the sole prompt source — descriptors appear, no steering block.
        self.assertIn("Architecture", mod.CLASSIFIER)
        self.assertIn("Trivial Q&A", mod.CLASSIFIER)
        self.assertNotIn("Rules:", mod.CLASSIFIER)
        self.assertNotIn("high-stakes", mod.CLASSIFIER.lower())
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
                    {"models": {"high": {"model": "some-voice", "provider": "other"}}}
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


if __name__ == "__main__":
    unittest.main()
