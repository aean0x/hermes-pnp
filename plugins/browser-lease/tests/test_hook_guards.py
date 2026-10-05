"""Hook guards: a bug in this plugin must never block a tool call.

``pre_tool_call`` fails CLOSED in Hermes, so an exception escaping the callback
blocks every subsequent tool call in the session, not just browser ones. These
tests pin the guard, not the lease logic.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import lease  # noqa: E402


def _explode(*_args, **_kwargs):
    raise RuntimeError("boom")


class _BoomLeases:
    """A leases mapping whose every lookup raises."""

    def get(self, *_args, **_kwargs):
        raise RuntimeError("boom")


class HookGuardTests(unittest.TestCase):
    def test_pre_tool_call_swallows_errors_and_does_not_block(self) -> None:
        original = getattr(lease, "_is_browser_tool")
        setattr(lease, "_is_browser_tool", _explode)
        try:
            self.assertIsNone(lease.pre_tool_call(tool_name="browser_goto", session_id="s1"))
        finally:
            setattr(lease, "_is_browser_tool", original)

    def test_post_tool_call_swallows_errors(self) -> None:
        original = lease.STATE.leases
        setattr(lease.STATE, "leases", _BoomLeases())
        try:
            self.assertIsNone(lease.post_tool_call(tool_name="browser_goto", session_id="s1"))
        finally:
            setattr(lease.STATE, "leases", original)

    def test_post_llm_call_swallows_errors(self) -> None:
        original = lease.STATE.leases
        setattr(lease.STATE, "leases", _BoomLeases())
        try:
            self.assertIsNone(lease.post_llm_call(session_id="s1"))
        finally:
            setattr(lease.STATE, "leases", original)


if __name__ == "__main__":
    unittest.main()
