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


def _hardline(description: str) -> str:
    """The hardline floor's own template (hermes-agent ``tools/approval_floors.py``)."""
    return (
        f"BLOCKED (hardline): {description}. This command is on the unconditional blocklist and"
        " cannot be executed via the agent — not even with --yolo, /yolo, approvals.mode=off, or"
        " cron approve mode. If you genuinely need to run it, run it yourself in a terminal"
        " outside the agent."
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


class HardlineTemplateTests(unittest.TestCase):
    """The hardline floor renders a different template and had no advice at all."""

    def test_shutdown_family_names_the_heredoc_body_and_the_file_form(self) -> None:
        out = ra.transform(tool_name="terminal", result=_hardline("system shutdown/reboot"))
        self.assertIn("heredoc BODY line", out)
        self.assertIn("git commit -F <path>", out)
        self.assertNotIn("python3 <path>", out)
        self.assertTrue(out.startswith(_hardline("system shutdown/reboot")))

    def test_shutdown_family_match_is_case_insensitive(self) -> None:
        out = ra.transform(result=_hardline("System Shutdown/Reboot"))
        self.assertIn("git commit -F <path>", out)

    def test_other_hardline_families_say_a_retry_cannot_succeed(self) -> None:
        out = ra.transform(result=_hardline("rm -rf /"))
        self.assertIn("no approval path", out)
        self.assertIn("re-sending the same command cannot succeed", out)

    def test_self_advising_hardline_families_are_left_alone(self) -> None:
        for description in ("command parser limit", "malformed executable payload"):
            with self.subTest(description=description):
                self.assertIsNone(ra.transform(result=_hardline(description)))

    def test_hardline_rewrite_is_idempotent(self) -> None:
        once = ra.transform(result=_hardline("system shutdown/reboot"))
        self.assertIsNone(ra.transform(result=once))

    def test_hardline_advice_never_rewrites_the_command(self) -> None:
        result = _hardline("system shutdown/reboot")
        out = ra.transform(result=result)
        self.assertTrue(out.startswith(result))
        self.assertNotIn("BLOCKED (hardline):", out[len(result):])


class EnvelopeTests(unittest.TestCase):
    """The gates hand the model a JSON envelope; advice outside the braces breaks every parser."""

    @staticmethod
    def _envelope(message: str) -> str:
        import json

        return json.dumps({"output": "", "exit_code": -1, "error": message,
                           "status": "blocked"}, ensure_ascii=False)

    def test_hardline_advice_lands_inside_the_error_field(self) -> None:
        import json

        raw = self._envelope(_hardline("system shutdown/reboot"))
        out = ra.transform(tool_name="terminal", result=raw)
        payload = json.loads(out)
        self.assertEqual(payload["status"], "blocked")
        self.assertTrue(payload["error"].startswith("BLOCKED (hardline):"))
        self.assertIn("git commit -F <path>", payload["error"])

    def test_unattended_advice_lands_inside_the_error_field(self) -> None:
        import json

        raw = self._envelope(_refusal("script execution via heredoc"))
        payload = json.loads(ra.transform(result=raw))
        self.assertIn("script FILE", payload["error"])

    def test_envelope_rewrite_is_idempotent(self) -> None:
        raw = self._envelope(_hardline("system shutdown/reboot"))
        once = ra.transform(result=raw)
        self.assertIsNone(ra.transform(result=once))

    def test_envelope_without_a_message_field_is_left_as_prose(self) -> None:
        raw = '{"output": "", "exit_code": -1}'
        self.assertIsNone(ra.transform(result=raw))


if __name__ == "__main__":
    unittest.main()
