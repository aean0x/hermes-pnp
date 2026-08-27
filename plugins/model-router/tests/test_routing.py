"""Turn-start routing, escalation rank, and handoff engine."""

from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
HERMES_SRC = Path("/data/src/hermes-agent")


def _load():
    os.environ.pop("MODEL_ROUTER_CONFIG", None)
    spec = importlib.util.spec_from_file_location("model_router_routing", ROOT / "__init__.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TargetTier(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load()

    def setUp(self) -> None:
        with self.mod._lock:
            self.mod._last_msg.clear()
            self.mod._last_tier.clear()
            self.mod._pinned.clear()
            self.mod._checkpoint.clear()
            self.mod._tool_errors.clear()

    def test_explicit_high_is_not_clamped(self) -> None:
        name, reason = self.mod._target_tier("s1", "/high please", [])
        self.assertEqual(name, "high")
        self.assertEqual(reason, "explicit")

    def test_classify_high_is_clamped(self) -> None:
        with patch.object(self.mod, "_classify", return_value="high"):
            name, reason = self.mod._target_tier(
                "s2", "please think hard about this architecture problem", []
            )
        self.assertEqual(name, "medium")
        self.assertIn("clamp", reason)

    def test_floor_promotes_low(self) -> None:
        with patch.object(self.mod, "_classify", return_value="low"):
            long = (
                "First sentence is long enough to count. "
                "Second sentence makes this multi-sentence work."
            )
            name, reason = self.mod._target_tier("s3", long, [])
        self.assertEqual(name, "medium")
        self.assertEqual(reason, "classify+floor")

    def test_ack_stays_low(self) -> None:
        name, reason = self.mod._target_tier("s4", "ok", [])
        self.assertEqual(name, "low")
        self.assertEqual(reason, "ack")

    def test_cached_message_reuses_tier(self) -> None:
        with self.mod._lock:
            self.mod._last_msg["s5"] = ("same", "medium")
        name, reason = self.mod._target_tier("s5", "same", [])
        self.assertEqual(name, "medium")
        self.assertEqual(reason, "cached")


class Escalate(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load()

    def setUp(self) -> None:
        with self.mod._lock:
            self.mod._last_msg.clear()
            self.mod._last_tier.clear()
            self.mod._pinned.clear()
            self.mod._checkpoint.clear()
            self.mod._tool_errors.clear()
            self.mod._last_user_sid = ""

    def test_higher_ladder(self) -> None:
        self.assertEqual(self.mod._higher("low"), "medium")
        self.assertEqual(self.mod._higher("medium"), "high")
        self.assertEqual(self.mod._higher("high"), "high")

    def test_pinned_refuses_escalation(self) -> None:
        with self.mod._lock:
            self.mod._pinned["sid"] = True
            self.mod._last_tier["sid"] = "medium"
            self.mod._last_user_sid = "sid"
        out = self.mod._handle_escalate_model(
            session_id="sid",
            summary="s",
            task_state="t",
            failure_point="f",
        )
        self.assertIn("Pinned", out)
        with self.mod._lock:
            self.assertEqual(self.mod._last_tier["sid"], "medium")

    def test_already_high_refuses(self) -> None:
        with self.mod._lock:
            self.mod._last_tier["sid"] = "high"
        out = self.mod._handle_escalate_model(
            session_id="sid",
            summary="s",
            task_state="t",
            failure_point="f",
        )
        self.assertIn("highest tier", out)

    def test_escalate_medium_to_high_stashes_handoff(self) -> None:
        engine = SimpleNamespace(handoff=None)
        agent = SimpleNamespace(context_compressor=engine, session_id="sid")
        with self.mod._lock:
            self.mod._last_tier["sid"] = "medium"
        with (
            patch.object(self.mod, "_get_agent", return_value=agent),
            patch.object(self.mod, "_set_tier") as set_tier,
        ):
            out = self.mod._handle_escalate_model(
                session_id="sid",
                summary="we decided X",
                task_state="trying Y",
                tried_so_far="Z failed",
                failure_point="exact error: boom",
                next_hypothesis="try W",
            )
        self.assertIn("Escalated", out)
        set_tier.assert_called_once_with("sid", "high", "escalate_model")
        self.assertEqual(engine.handoff["failure_point"], "exact error: boom")
        self.assertEqual(engine.handoff["summary"], "we decided X")
        self.assertEqual(engine.handoff["from_tier"], "medium")
        self.assertEqual(engine.handoff["to_tier"], "high")
        self.assertEqual(engine.handoff["to_model"], "grok-4.6")

    def test_auto_after_high_pin_bumps_down(self) -> None:
        # Live 0.5.0 bug: /auto after /high left the router on grok because the
        # 3-way classifier kept returning high. 0.7.0 clears the pin+cached
        # tier on /auto, and even a high-leaning classify is clamped to medium.
        with self.mod._lock:
            self.mod._pinned["s"] = True
            self.mod._last_tier["s"] = "high"
            self.mod._last_msg["s"] = ("previous turn", "high")
            self.mod._tool_errors["s"] = 4
            self.mod._last_user_sid = "s"
        out = self.mod._cmd_auto("")
        self.assertIn("resumed", out)
        with self.mod._lock:
            self.assertFalse("s" in self.mod._pinned)
            self.assertFalse("s" in self.mod._last_tier)
            self.assertFalse("s" in self.mod._last_msg)
            self.assertFalse("s" in self.mod._tool_errors)
        with patch.object(self.mod, "_classify", return_value="high"):
            name, reason = self.mod._target_tier(
                "s", "please review this architecture decision carefully", []
            )
        self.assertEqual(name, "medium")
        self.assertIn("clamp", reason)


@unittest.skipUnless(HERMES_SRC.is_dir(), "hermes-agent source not present")
class HandoffEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if str(HERMES_SRC) not in sys.path:
            sys.path.insert(0, str(HERMES_SRC))
        spec = importlib.util.spec_from_file_location("mr_engine", ROOT / "engine.py")
        assert spec is not None and spec.loader is not None
        cls.eng = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.eng)

    def test_no_handoff_is_noop(self) -> None:
        engine = self.eng.ModelRouterContextEngine(model="grok-4.6")
        req = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
        ]
        self.assertIsNone(engine.select_context(req))

    def test_handoff_replaces_request_and_keeps_system(self) -> None:
        engine = self.eng.ModelRouterContextEngine(model="grok-4.6")
        engine.handoff = {
            "from_tier": "medium",
            "to_tier": "high",
            "to_model": "grok-4.6",
            "summary": "established A",
            "task_state": "doing B",
            "tried_so_far": "C",
            "failure_point": "Error: boom",
            "next_hypothesis": "try D",
        }
        req = [
            {"role": "system", "content": "stable prefix"},
            {"role": "user", "content": "old user"},
            {"role": "assistant", "content": "old asst"},
            {"role": "tool", "content": "Error: boom"},
        ]
        out = engine.select_context(req)
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out[0]["role"], "system")
        self.assertEqual(out[0]["content"], "stable prefix")
        self.assertEqual(out[1]["role"], "user")
        self.assertIn("Error: boom", out[1]["content"])
        self.assertIn("established A", out[1]["content"])
        self.assertIn("medium → high (grok-4.6)", out[1]["content"])
        self.assertTrue(any(m.get("content") == "Error: boom" for m in out[2:]))
        self.assertIsNone(engine.handoff)

    def test_handoff_is_one_shot(self) -> None:
        engine = self.eng.ModelRouterContextEngine(model="grok-4.6")
        engine.handoff = {"summary": "s", "task_state": "t", "failure_point": "f"}
        req = [{"role": "system", "content": "sys"}]
        self.assertIsNotNone(engine.select_context(req))
        self.assertIsNone(engine.select_context(req))


if __name__ == "__main__":
    unittest.main()
