"""Hook surface: registration, non-browser passthrough, veto, turn-end release, opt-out."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import lease as l  # noqa: E402


class FakeCtx:
    """Minimal stand-in for Hermes' PluginContext."""

    def __init__(self) -> None:
        self.hooks: dict[str, object] = {}

    def register_hook(self, name: str, callback) -> None:
        self.hooks[name] = callback


class HookTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.lock = Path(self._tmp.name) / "browser-lease.lock"
        self.real_cfg = l._cfg
        l._cfg = lambda: {"enabled": True, "wait_s": 0.3, "sticky_s": 30.0,
                          "lock_path": str(self.lock)}
        l.STATE = l._State()

    def tearDown(self) -> None:
        l._cfg = self.real_cfg
        l.STATE = l._State()
        self._tmp.cleanup()

    def test_registers_the_three_hooks(self) -> None:
        ctx = FakeCtx()
        l.register(ctx)
        self.assertEqual(set(ctx.hooks), {"pre_tool_call", "post_tool_call", "post_llm_call"})

    def test_non_browser_tool_passes_through(self) -> None:
        self.assertIsNone(l.pre_tool_call(tool_name="terminal", session_id="a"))
        self.assertFalse(self.lock.exists(), "no lease file for a non-browser tool")

    def test_browser_tool_takes_then_releases_at_turn_end(self) -> None:
        self.assertIsNone(l.pre_tool_call(tool_name="browser_exec", session_id="a"))
        self.assertTrue(self.lock.exists())
        l.post_llm_call(session_id="a")
        self.assertIsNone(l.pre_tool_call(tool_name="browser_exec", session_id="b"),
                          "lease must be free after the other session's turn ended")

    def test_contending_session_is_blocked_with_the_holder_named(self) -> None:
        l.pre_tool_call(tool_name="browser_exec", session_id="session-a")
        directive = l.pre_tool_call(tool_name="browser_exec", session_id="session-b")
        self.assertIsInstance(directive, dict)
        self.assertEqual(directive["action"], "block")
        self.assertIn("session-a", directive["message"])
        self.assertIn("Retry", directive["message"])

    def test_same_session_renews_without_waiting(self) -> None:
        l.pre_tool_call(tool_name="browser_exec", session_id="a")
        l.post_tool_call(tool_name="browser_exec", session_id="a")
        self.assertIsNone(l.pre_tool_call(tool_name="browser_snapshot", session_id="a"))

    def test_disabled_config_disables_the_gate(self) -> None:
        l._cfg = lambda: {"enabled": False, "wait_s": 0.3, "sticky_s": 30.0, "lock_path": ""}
        l.pre_tool_call(tool_name="browser_exec", session_id="session-a")
        self.assertIsNone(l.pre_tool_call(tool_name="browser_exec", session_id="session-b"))

    def test_lock_path_defaults_under_the_hermes_home(self) -> None:
        l._cfg = self.real_cfg
        previous = os.environ.get("HERMES_HOME")
        os.environ["HERMES_HOME"] = self._tmp.name
        try:
            self.assertEqual(l._lock_path(l._cfg()),
                             Path(self._tmp.name) / "cache" / "browser-lease.lock")
        finally:
            if previous is None:
                os.environ.pop("HERMES_HOME", None)
            else:
                os.environ["HERMES_HOME"] = previous


if __name__ == "__main__":
    unittest.main()
