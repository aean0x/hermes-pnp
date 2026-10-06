"""The transform hook sits inline on the tool-result path and must never raise."""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("refusal_advice_guard", ROOT / "__init__.py")
assert spec is not None and spec.loader is not None
ra = importlib.util.module_from_spec(spec)
sys.modules["refusal_advice_guard"] = ra
spec.loader.exec_module(ra)


def _refusal(description: str) -> str:
    return f"BLOCKED: Command flagged as dangerous ({description}) but cron jobs run without a user."


def _explode(*_args, **_kwargs):
    raise RuntimeError("boom")


class TransformGuardTests(unittest.TestCase):
    def test_transform_swallows_errors_and_leaves_the_result_untouched(self) -> None:
        original = getattr(ra, "advice_for")
        setattr(ra, "advice_for", _explode)
        try:
            self.assertIsNone(ra.transform(tool_name="terminal", result=_refusal("script execution")))
        finally:
            setattr(ra, "advice_for", original)

    def test_transform_returns_none_for_a_non_refusal(self) -> None:
        self.assertIsNone(ra.transform(tool_name="terminal", result="plain output"))
        self.assertIsNone(ra.transform(tool_name="terminal", result=None))


if __name__ == "__main__":
    unittest.main()
