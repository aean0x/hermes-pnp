"""Refusal rewriting: the gate-clean alternative lands on the refusal, once, and only there."""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("refusal_advice", ROOT / "__init__.py")
assert spec is not None and spec.loader is not None
ra = importlib.util.module_from_spec(spec)
sys.modules["refusal_advice"] = ra
spec.loader.exec_module(ra)


def _refusal(description: str) -> str:
    return (
        f"BLOCKED: Command flagged as dangerous ({description}) but cron jobs run without a"
        " user present to approve it. Find an alternative approach that avoids this command."
        " To allow dangerous commands in cron jobs, set approvals.cron_mode: approve in"
        " config.yaml."
    )


class FakeCtx:
    """Minimal stand-in for Hermes' PluginContext."""

    def __init__(self) -> None:
        self.hooks: dict[str, object] = {}

    def register_hook(self, name: str, callback) -> None:
        self.hooks[name] = callback


class TransformTests(unittest.TestCase):
    def test_heredoc_refusal_names_the_script_file_shape(self) -> None:
        out = ra.transform(tool_name="terminal", result=_refusal("script execution via heredoc"))
        self.assertIn("script FILE", out)
        self.assertIn("python3 <path>", out)
        self.assertTrue(out.startswith(_refusal("script execution via heredoc")))

    def test_flag_refusal_names_the_script_file_shape(self) -> None:
        for description in ("script execution via -e/-c flag", "shell command via -c/-lc flag"):
            with self.subTest(description=description):
                self.assertIn("script FILE", ra.transform(result=_refusal(description)))

    def test_quoted_payload_match_tells_the_model_to_reword(self) -> None:
        out = ra.transform(result=_refusal("stop/restart system service"))
        self.assertIn("quoted arguments included", out)
        self.assertIn("reword the payload", out)

    def test_unknown_description_still_gets_an_alternative(self) -> None:
        out = ra.transform(result=_refusal("format filesystem"))
        self.assertIn("python3 <path>", out)

    def test_non_refusal_results_are_untouched(self) -> None:
        for result in ("exit 0\nhello", "", '{"error": "syntax error"}', None, {"a": 1}):
            with self.subTest(result=result):
                self.assertIsNone(ra.transform(tool_name="terminal", result=result))

    def test_rewrite_is_idempotent(self) -> None:
        once = ra.transform(result=_refusal("script execution via heredoc"))
        self.assertIsNone(ra.transform(result=once))

    def test_register_wires_the_transform_hook(self) -> None:
        ctx = FakeCtx()
        ra.register(ctx)
        self.assertIs(ctx.hooks["transform_tool_result"], ra.transform)


if __name__ == "__main__":
    unittest.main()
