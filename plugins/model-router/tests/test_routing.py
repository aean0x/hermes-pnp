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
HERMES_SRC = Path("/home/hermes/src/hermes-agent")
if not HERMES_SRC.is_dir():
    HERMES_SRC = Path("/data/src/hermes-agent")


def _load():
    os.environ["MODEL_ROUTER_CONFIG"] = str(ROOT / "tests" / "oobe-ids.json")
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

    def test_classify_high_is_accepted(self) -> None:
        with patch.object(self.mod, "_classify", return_value="high"):
            name, reason = self.mod._target_tier(
                "s2", "please execute this monetary transfer now", []
            )
        self.assertEqual(name, "high")
        self.assertEqual(reason, "classify")

    def test_no_floor_on_multi_sentence_low(self) -> None:
        with patch.object(self.mod, "_classify", return_value="low"):
            long = (
                "First sentence is long enough to count. "
                "Second sentence makes this multi-sentence work."
            )
            name, reason = self.mod._target_tier("s3", long, [])
        self.assertEqual(name, "low")
        self.assertEqual(reason, "classify")

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

    def test_explicit_slash_pins_session(self) -> None:
        with patch.object(self.mod, "_get_agent", return_value=None):
            self.mod.on_pre_llm_call(
                user_message="/high",
                conversation_history=[],
                session_id="pin1",
            )
        with self.mod._lock:
            self.assertTrue(self.mod._pinned.get("pin1"))
            self.assertEqual(self.mod._last_tier.get("pin1"), "high")

    def test_mid_paragraph_high_does_not_pin(self) -> None:
        msg = (
            "Agree with your assessment. Execute in a PR please.\n\n"
            "check the session where I did an explicit /high and it finished as pro."
        )
        with (
            patch.object(self.mod, "_classify", return_value="medium"),
            patch.object(self.mod, "_get_agent", return_value=None),
        ):
            self.mod.on_pre_llm_call(
                user_message=msg,
                conversation_history=[],
                session_id="pin2",
            )
        with self.mod._lock:
            self.assertFalse(self.mod._pinned.get("pin2", False))
            self.assertEqual(self.mod._last_tier.get("pin2"), "medium")


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
        self.assertEqual(engine.handoff["to_model"], self.mod.MODELS["high"]["model"])

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
        with patch.object(self.mod, "_classify", return_value="low"):
            name, reason = self.mod._target_tier(
                "s", "please review this architecture decision carefully", []
            )
        self.assertEqual(name, "low")
        self.assertEqual(reason, "classify")


class ClassifierSignal(unittest.TestCase):
    """The classifier sees the previous tier and is biased to keep it."""

    @classmethod
    def setUpClass(cls) -> None:
        if str(HERMES_SRC) not in sys.path:
            sys.path.insert(0, str(HERMES_SRC))
        cls.mod = _load()

    def setUp(self) -> None:
        with self.mod._lock:
            self.mod._last_msg.clear()
            self.mod._last_tier.clear()

    def _capture(self, sid: str, user_message: str = "hello there") -> dict:
        captured: dict = {}

        def fake_call_llm(**kwargs):
            captured["messages"] = kwargs.get("messages")
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="low"))]
            )

        def fake_resolve(name, agent):
            captured["resolve_name"] = name
            return None

        with (
            patch("agent.auxiliary_client.call_llm", fake_call_llm),
            patch.object(self.mod, "_resolve_tier_runtime", side_effect=fake_resolve),
        ):
            self.mod._classify(user_message, [], sid)
        return captured

    def test_no_prev_tier_omits_signal(self) -> None:
        captured = self._capture("s1")
        system = captured["messages"][0]["content"]
        self.assertNotIn("Previous turn tier", system)

    def test_prev_medium_signals_and_runs_medium(self) -> None:
        with self.mod._lock:
            self.mod._last_tier["s2"] = "medium"
        captured = self._capture("s2")
        self.assertIn("Previous turn tier: medium", captured["messages"][0]["content"])
        self.assertEqual(captured["resolve_name"], "medium")

    def test_prev_high_signals_high_but_runs_medium(self) -> None:
        with self.mod._lock:
            self.mod._last_tier["s3"] = "high"
        captured = self._capture("s3")
        self.assertIn("Previous turn tier: high", captured["messages"][0]["content"])
        self.assertEqual(captured["resolve_name"], "medium")


class ForceCompaction(unittest.TestCase):
    """A classifier-driven tier change requests compaction; other paths don't."""

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

    def _agent(self, sid: str) -> SimpleNamespace:
        engine = SimpleNamespace(force_compress_once=False)
        return SimpleNamespace(context_compressor=engine, session_id=sid)

    def test_classify_change_requests_compaction(self) -> None:
        agent = self._agent("s1")
        with self.mod._lock:
            self.mod._last_tier["s1"] = "low"
        with (
            patch.object(self.mod, "_classify", return_value="medium"),
            patch.object(self.mod, "_get_agent", return_value=agent),
            patch.object(self.mod, "_set_tier"),
        ):
            self.mod.on_pre_llm_call(
                user_message="do a big multi-file refactor",
                conversation_history=[],
                session_id="s1",
            )
        self.assertTrue(agent.context_compressor.force_compress_once)

    def test_no_change_does_not_request_compaction(self) -> None:
        agent = self._agent("s2")
        with self.mod._lock:
            self.mod._last_tier["s2"] = "medium"
        with (
            patch.object(self.mod, "_classify", return_value="medium"),
            patch.object(self.mod, "_get_agent", return_value=agent),
            patch.object(self.mod, "_set_tier"),
        ):
            self.mod.on_pre_llm_call(
                user_message="continue where we left off",
                conversation_history=[],
                session_id="s2",
            )
        self.assertFalse(agent.context_compressor.force_compress_once)

    def test_explicit_pin_does_not_request_compaction(self) -> None:
        agent = self._agent("s3")
        with self.mod._lock:
            self.mod._last_tier["s3"] = "low"
        with (
            patch.object(self.mod, "_get_agent", return_value=agent),
            patch.object(self.mod, "_set_tier"),
        ):
            self.mod.on_pre_llm_call(
                user_message="/high please",
                conversation_history=[],
                session_id="s3",
            )
        self.assertFalse(agent.context_compressor.force_compress_once)

    def test_first_turn_does_not_request_compaction(self) -> None:
        agent = self._agent("s4")
        with (
            patch.object(self.mod, "_classify", return_value="medium"),
            patch.object(self.mod, "_get_agent", return_value=agent),
            patch.object(self.mod, "_set_tier"),
        ):
            self.mod.on_pre_llm_call(
                user_message="first message ever",
                conversation_history=[],
                session_id="s4",
            )
        self.assertFalse(agent.context_compressor.force_compress_once)


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

    def test_force_compress_defaults_false(self) -> None:
        engine = self.eng.ModelRouterContextEngine(model="grok-4.6")
        self.assertFalse(engine.force_compress_once)

    def test_force_compress_once_returns_true_then_delegates(self) -> None:
        engine = self.eng.ModelRouterContextEngine(model="grok-4.6")
        engine.force_compress_once = True
        self.assertEqual(engine.should_compress_info(0), (True, None))
        self.assertFalse(engine.force_compress_once)
        # Second call falls through to the inherited threshold logic (0 tokens
        # is always under threshold).
        self.assertEqual(engine.should_compress_info(0), (False, None))


if __name__ == "__main__":
    unittest.main()
