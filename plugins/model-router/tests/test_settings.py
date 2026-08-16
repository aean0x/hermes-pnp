"""Unit tests for model-router settings (no Hermes)."""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("mr_settings", ROOT / "settings.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Defaults(unittest.TestCase):
    def test_three_tiers_and_voice(self) -> None:
        os.environ.pop("MODEL_ROUTER_CONFIG", None)
        os.environ.pop("MODEL_ROUTER_T3_MODEL", None)
        # settings.py caches at import; load a fresh copy via file exec
        mod = _load()
        self.assertIn(1, mod.TIERS)
        self.assertIn(2, mod.TIERS)
        self.assertIn(3, mod.TIERS)
        self.assertEqual(mod.FINAL_TIER, 3)
        self.assertTrue(mod.FINAL_VOICE)
        self.assertNotIn("Archimedes", mod.FINAL_VOICE_SYSTEM)
        self.assertNotIn("rocknas", mod.CLASSIFIER.lower())


class EnvOverlay(unittest.TestCase):
    def test_model_env_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "cfg.json"
            cfg.write_text(
                json.dumps(
                    {
                        "tiers": {"3": {"model": "some-voice", "provider": "other"}},
                        "final_voice": False,
                    }
                ),
                encoding="utf-8",
            )
            old = os.environ.get("MODEL_ROUTER_CONFIG")
            os.environ["MODEL_ROUTER_CONFIG"] = str(cfg)
            try:
                # Force re-exec
                spec = importlib.util.spec_from_file_location(
                    "mr_settings_overlay", ROOT / "settings.py"
                )
                assert spec is not None and spec.loader is not None
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                self.assertEqual(mod.TIERS[3]["model"], "some-voice")
                self.assertEqual(mod.TIERS[3]["provider"], "other")
                self.assertFalse(mod.FINAL_VOICE)
            finally:
                if old is None:
                    os.environ.pop("MODEL_ROUTER_CONFIG", None)
                else:
                    os.environ["MODEL_ROUTER_CONFIG"] = old


if __name__ == "__main__":
    unittest.main()
