"""Pure unit tests for secret-handoff. Stock CPython — no Hermes, no CDP."""

from __future__ import annotations

import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import handoff as h  # noqa: E402


def _install_clarify(fn) -> None:
    tools_mod = sys.modules.get("tools")
    if tools_mod is None:
        tools_mod = types.ModuleType("tools")
        sys.modules["tools"] = tools_mod
    clarify_mod = types.ModuleType("tools.clarify_tool")
    clarify_mod.clarify_tool = fn
    sys.modules["tools.clarify_tool"] = clarify_mod
    setattr(tools_mod, "clarify_tool", clarify_mod)


class ClassifyReply(unittest.TestCase):
    def test_done(self) -> None:
        self.assertEqual(h.classify_reply("hunter2"), "inject")
        self.assertEqual(h.classify_reply("  hunter2  "), "inject")

    def test_cancel(self) -> None:
        for text in ("", "   ", None, "cancel", "Cancel", "n", "N", "no", "NO"):
            self.assertEqual(h.classify_reply(text), "cancel", text)

    def test_secret(self) -> None:
        self.assertEqual(h.classify_reply("s3cret-P@ssw0rd!"), "inject")

    def test_classify_ignore_slash(self) -> None:
        self.assertEqual(h.classify_reply("/stop"), "ignore")
        self.assertEqual(h.classify_reply("/new"), "ignore")


class PickTargetAndCdpUrl(unittest.TestCase):
    def test_pick_explicit_target(self) -> None:
        targets = [
            {"id": "a", "type": "page"},
            {"id": "b", "type": "page", "attached": True},
        ]
        picked = h.pick_target(targets, target_id="a")
        assert picked is not None
        self.assertEqual(picked["id"], "a")

    def test_pick_frame_id_prefers_iframe(self) -> None:
        targets = [
            {"id": "page", "type": "page"},
            {"id": "ifr", "type": "iframe"},
        ]
        picked = h.pick_target(targets, frame_id="ifr")
        assert picked is not None
        self.assertEqual(picked["id"], "ifr")

    def test_pick_attached_page(self) -> None:
        targets = [
            {"id": "bg", "type": "background_page"},
            {"id": "p1", "type": "page"},
            {"id": "p2", "type": "page", "attached": True},
        ]
        picked = h.pick_target(targets)
        assert picked is not None
        self.assertEqual(picked["id"], "p2")

    def test_resolve_cdp_explicit_wins(self) -> None:
        self.assertEqual(
            h.resolve_cdp_http_base("ws://10.0.0.8:9333/devtools/browser/abc"),
            "http://10.0.0.8:9333",
        )

    def test_resolve_cdp_env(self) -> None:
        with patch.dict("os.environ", {"BROWSER_CDP_URL": "http://127.0.0.1:9222"}):
            self.assertEqual(h.resolve_cdp_http_base(None), "http://127.0.0.1:9222")


class NeverReturnsSecret(unittest.TestCase):
    def setUp(self) -> None:
        h.reset_state()

    def tearDown(self) -> None:
        h.reset_state()

    def test_cli_tool_path_injects_then_omits_secret(self) -> None:
        secret = "cli-only-password-zzz"
        captured: list[str] = []

        def fake_clarify(**_kwargs):
            return json.dumps({"user_response": secret})

        def fake_inject(text: str, **_kwargs) -> tuple[bool, str]:
            captured.append(text)
            return True, "cdp Input.insertText"

        with (
            patch.object(h, "resolve_session_key_for_tool", return_value="cli"),
            patch.object(h, "inject_secret", side_effect=fake_inject),
        ):
            _install_clarify(fake_clarify)
            out = h.handle_request_secret(
                {"service": "ticketmaster"},
                callback=lambda *_a, **_k: secret,
            )
        data = json.loads(out)
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["service"], "ticketmaster")
        self.assertEqual(data["detail"], "cdp Input.insertText")
        self.assertEqual(captured, [secret])
        self.assertNotIn(secret, out)
        self.assertNotIn(secret, json.dumps(data))
        self.assertIsNone(h.peek_pending("cli"))

    def test_slash_in_clarify_does_not_inject(self) -> None:
        secret = "/stop"
        captured: list[str] = []

        def fake_clarify(**_kwargs):
            return json.dumps({"user_response": "/stop"})

        def fake_inject(text: str, **_kwargs) -> tuple[bool, str]:
            captured.append(text)
            return True, "injected"

        with (
            patch.object(h, "resolve_session_key_for_tool", return_value="cli"),
            patch.object(h, "inject_secret", side_effect=fake_inject),
        ):
            _install_clarify(fake_clarify)
            out = h.handle_request_secret(
                {"service": "example.com"},
                callback=lambda *_a, **_k: secret,
            )
        data = json.loads(out)
        self.assertEqual(data["status"], "cancelled")
        self.assertEqual(captured, [])
        self.assertNotIn(secret, json.dumps(data))
        self.assertIsNone(h.peek_pending("cli"))

    def test_cancel_skips_inject(self) -> None:
        secret = "cancel-must-not-leak"
        captured: list[str] = []

        def fake_clarify(**_kwargs):
            return json.dumps({"user_response": "cancel"})

        def fake_inject(text: str, **_kwargs) -> tuple[bool, str]:
            captured.append(text)
            return True, "injected"

        with (
            patch.object(h, "resolve_session_key_for_tool", return_value="cli"),
            patch.object(h, "inject_secret", side_effect=fake_inject),
        ):
            _install_clarify(fake_clarify)
            out = h.handle_request_secret(
                {"service": "example.com"},
                callback=lambda *_a, **_k: secret,
            )
        data = json.loads(out)
        self.assertEqual(data["status"], "cancelled")
        self.assertEqual(captured, [])
        self.assertNotIn(secret, out)
        self.assertNotIn(secret, json.dumps(data))
        self.assertIsNone(h.peek_pending("cli"))

    def test_timeout_skips_inject(self) -> None:
        secret = "timeout-must-not-leak"
        captured: list[str] = []

        def fake_clarify(**_kwargs):
            return json.dumps({"user_response": "[user did not respond in time]"})

        def fake_inject(text: str, **_kwargs) -> tuple[bool, str]:
            captured.append(text)
            return True, "injected"

        with (
            patch.object(h, "resolve_session_key_for_tool", return_value="cli"),
            patch.object(h, "inject_secret", side_effect=fake_inject),
        ):
            _install_clarify(fake_clarify)
            out = h.handle_request_secret(
                {"service": "example.com"},
                callback=lambda *_a, **_k: secret,
            )
        data = json.loads(out)
        self.assertEqual(data["status"], "failed")
        self.assertEqual(captured, [])
        self.assertNotIn(secret, out)
        self.assertNotIn(secret, json.dumps(data))
        self.assertIsNone(h.peek_pending("cli"))

    def test_inject_failure_omits_secret(self) -> None:
        secret = "another-secret-value"

        def fake_clarify(**_kwargs):
            return json.dumps({"user_response": secret})

        def fake_inject(text: str, **_kwargs) -> tuple[bool, str]:
            return False, "boom"

        with (
            patch.object(h, "resolve_session_key_for_tool", return_value="cli"),
            patch.object(h, "inject_secret", side_effect=fake_inject),
        ):
            _install_clarify(fake_clarify)
            out = h.handle_request_secret(
                {"service": "bank"},
                callback=lambda *_a, **_k: secret,
            )
        data = json.loads(out)
        self.assertEqual(data["status"], "failed")
        self.assertEqual(data["detail"], "boom")
        self.assertNotIn(secret, out)
        self.assertNotIn(secret, json.dumps(data))
        self.assertIsNone(h.peek_pending("cli"))


if __name__ == "__main__":
    unittest.main()
