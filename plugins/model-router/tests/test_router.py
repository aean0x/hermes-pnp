"""Router pin / name helpers (no Hermes)."""

from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    os.environ.pop("MODEL_ROUTER_CONFIG", None)
    spec = importlib.util.spec_from_file_location("model_router_mod", ROOT / "__init__.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Pins(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load()

    def test_named_slash_pins(self) -> None:
        self.assertEqual(self.mod._detect_explicit_tier("/low"), "low")
        self.assertEqual(self.mod._detect_explicit_tier("/medium"), "medium")
        self.assertEqual(self.mod._detect_explicit_tier("/high please"), "high")

    def test_use_phrase(self) -> None:
        self.assertEqual(self.mod._detect_explicit_tier("please use medium"), "medium")
        self.assertEqual(self.mod._detect_explicit_tier("pin high"), "high")

    def test_bare_name_in_long_critique_is_not_a_pin(self) -> None:
        msg = "The low estimate in the budget is wrong because the medium path is already over cost."
        self.assertIsNone(self.mod._detect_explicit_tier(msg))

    def test_short_bare_name_is_a_pin(self) -> None:
        self.assertEqual(self.mod._detect_explicit_tier("medium"), "medium")


class Names(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load()

    def test_as_name_named_only(self) -> None:
        self.assertEqual(self.mod.as_name("low"), "low")
        self.assertEqual(self.mod.as_name("HIGH"), "high")
        self.assertIsNone(self.mod.as_name("ultra"))

    def test_higher_climbs_to_high(self) -> None:
        self.assertEqual(self.mod._higher("low"), "medium")
        self.assertEqual(self.mod._higher("medium"), "high")
        self.assertEqual(self.mod._higher("high"), "high")

    def test_router_does_not_touch_reasoning_config(self) -> None:
        src = (ROOT / "__init__.py").read_text(encoding="utf-8")
        self.assertNotIn("reasoning_config", src)
        self.assertNotIn(".reasoning_effort", src)


if __name__ == "__main__":
    unittest.main()
