"""Unit tests for the tool_call argument heal (structural repair + dropped-name recall).

Fixtures are the real emission shapes from errors.log (2026-09-08…09-13), one per class.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]


def _load_plugin():
    spec = importlib.util.spec_from_file_location("tcc_under_test", PLUGIN_DIR / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tcc = _load_plugin()

MULTI_EXECUTE = {"type": "function", "function": {
    "name": "mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL",
    "parameters": {"type": "object", "properties": {
        "tools": {"type": "array"}, "thought": {"type": "string"},
        "sync_response_to_workbench": {"type": "boolean"},
        "current_step": {"type": "string"}, "session_id": {"type": "string"},
        "current_step_metric": {"type": "string"}},
        "required": ["tools", "sync_response_to_workbench"]}}}
PUT_PAGE = {"type": "function", "function": {
    "name": "mcp__gbrain__put_page",
    "parameters": {"type": "object", "properties": {
        "slug": {"type": "string"}, "content": {"type": "string"},
        "allow_empty": {"type": "boolean"}}, "required": ["slug", "content"]}}}
GET_PAGE = {"type": "function", "function": {
    "name": "mcp__gbrain__get_page",
    "parameters": {"type": "object", "properties": {"slug": {"type": "string"}},
                   "required": ["slug"]}}}
CORE_WRITE = {"type": "function", "function": {
    "name": "write_file",
    "parameters": {"type": "object", "properties": {
        "path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"]}}}


class _FakeRegistry:
    def __init__(self, schemas):
        self._schemas = schemas

    def get_all_tool_names(self):
        return list(self._schemas)

    def get_schema(self, name):
        return self._schemas.get(name)


class _RegistryCase(unittest.TestCase):
    """Install a fake ``tools.registry`` so name recall can be exercised offline."""

    def _registry(self, *schemas):
        fake = types.ModuleType("tools.registry")
        fake.registry = _FakeRegistry({s["function"]["name"]: s for s in schemas})
        sys.modules["tools.registry"] = fake
        tcc._infer_deferred_name.cache_clear()
        self.addCleanup(sys.modules.pop, "tools.registry", None)
        self.addCleanup(tcc._infer_deferred_name.cache_clear)


class TestStructuralRepair(unittest.TestCase):
    def assert_repaired(self, raw, expected):
        repaired = tcc.structural_repair(raw)
        self.assertIsNotNone(repaired, f"not repaired: {raw!r}")
        self.assertEqual(json.loads(repaired, strict=False), expected)

    def test_calls_array_closed_with_brace(self):
        """Errors.log class 1: the `calls` array closer replaced by '}' (25/28 emissions)."""
        raw = (
            '{"calls": [{"name": "mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL", "arguments": '
            '{"tools": [{"tool_slug": "GMAIL_FETCH_EMAILS", "arguments": {"max_results": 50}}], '
            '"thought": "t", "current_step": "FETCHING_EMAILS"}}}'
        )
        self.assert_repaired(raw, {
            "calls": [{"name": "mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL", "arguments": {
                "tools": [{"tool_slug": "GMAIL_FETCH_EMAILS", "arguments": {"max_results": 50}}],
                "thought": "t", "current_step": "FETCHING_EMAILS"}}]})

    def test_entry_closed_with_bracket(self):
        """Errors.log class 2: an entry object closed with ']' instead of '}'."""
        raw = (
            '{"calls": [{"arguments": {"tools": [{"tool_slug": "TRELLO_GET_CARDS", '
            '"arguments": {"idList": "abc"}}], "thought": "t"}]}]}'
        )
        self.assert_repaired(raw, {
            "calls": [{"arguments": {"tools": [{"tool_slug": "TRELLO_GET_CARDS",
                                                "arguments": {"idList": "abc"}}],
                                     "thought": "t"}}]})

    def test_missing_entry_closer(self):
        """Errors.log class 3: entry brace omitted, `name` left as a sibling element."""
        raw = ('{"calls": [{"arguments": {"content": "body", "slug": "ops/x"}, '
               '{"name": "mcp__gbrain__put_page"}]}')
        self.assert_repaired(raw, {"calls": [
            {"arguments": {"content": "body", "slug": "ops/x"}},
            {"name": "mcp__gbrain__put_page"}]})

    def test_unclosed_at_eof_closes_lifo(self):
        """Errors.log class 4: stream ended with valid values, containers still open."""
        raw = '{"calls": [{"arguments": {"thought": "t", "tools": [{"tool_slug": "A"}]}'
        self.assert_repaired(raw, {"calls": [
            {"arguments": {"thought": "t", "tools": [{"tool_slug": "A"}]}}]})

    def test_legacy_single_shape_with_stray_closers(self):
        """Errors.log class 5: name as a sibling of `arguments` plus stray ']' pairs."""
        raw = ('{"arguments": {"tools": [{"tool_slug": "GMAIL_SEND_EMAIL", "arguments": '
               '{"user_id": "me"}}]}], "name": "mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL"}')
        self.assert_repaired(raw, {
            "arguments": {"tools": [{"tool_slug": "GMAIL_SEND_EMAIL",
                                     "arguments": {"user_id": "me"}}]},
            "name": "mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL"})

    def test_trailing_comma_before_closer(self):
        raw = '{"calls": [{"name": "x", "arguments": {"a": 1},}]}'
        self.assert_repaired(raw, {"calls": [{"name": "x", "arguments": {"a": 1}}]})

    def test_valid_json_is_untouched(self):
        """The upstream repairer owns everything that already parses."""
        for raw in ('{"calls": [{"name": "x", "arguments": {}}]}',
                    '{"calls": [{"name": "x", "arguments": {"s": "a]b, c}"}}]}'):
            self.assertIsNone(tcc.structural_repair(raw))

    def test_truncated_string_is_refused(self):
        """Max-tokens truncation: repairing it would silently store a truncated payload."""
        raw = '{"calls": [{"name": "mcp__gbrain__put_page", "arguments": {"slug": "a", "content": "half a sen'
        self.assertIsNone(tcc.structural_repair(raw))

    def test_junk_inputs(self):
        self.assertIsNone(tcc.structural_repair(None))
        self.assertIsNone(tcc.structural_repair(""))
        self.assertIsNone(tcc.structural_repair("   "))
        self.assertIsNone(tcc.structural_repair("not json at all"))
        self.assertIsNone(tcc.structural_repair("{]}}]"))

    def test_repair_is_idempotent(self):
        raw = '{"calls": [{"name": "x", "arguments": {"a": [1, 2]}}}'
        once = tcc.structural_repair(raw)
        self.assertIsNotNone(once)
        self.assertIsNone(tcc.structural_repair(once))


class TestDroppedNameRecall(_RegistryCase):
    def test_single_deferred_match_is_recalled(self):
        self._registry(MULTI_EXECUTE, GET_PAGE, CORE_WRITE)
        args = {"calls": [{"arguments": {"tools": [{"tool_slug": "GMAIL_FETCH_EMAILS"}],
                                         "sync_response_to_workbench": False,
                                         "current_step": "FETCHING_EMAILS"}}]}
        self.assertEqual(tcc.heal_bridge_entries(args), 1)
        self.assertEqual(args["calls"][0]["name"],
                         "mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL")

    def test_ambiguous_signature_is_not_guessed(self):
        self._registry(PUT_PAGE, {"type": "function", "function": {
            "name": "mcp__other__write_page",
            "parameters": {"type": "object", "properties": {
                "slug": {"type": "string"}, "content": {"type": "string"}},
                "required": ["slug", "content"]}}})
        args = {"calls": [{"arguments": {"slug": "a", "content": "b"}}]}
        self.assertEqual(tcc.heal_bridge_entries(args), 0)
        self.assertNotIn("name", args["calls"][0])

    def test_core_tool_signature_is_out_of_scope(self):
        """Only the bridge's deferred surface is recallable; core names are not guessed."""
        self._registry(CORE_WRITE)
        args = {"calls": [{"arguments": {"path": "/tmp/x", "content": "b"}}]}
        self.assertEqual(tcc.heal_bridge_entries(args), 0)

    def test_partial_signature_is_not_evidence(self):
        self._registry(PUT_PAGE)
        args = {"calls": [{"arguments": {"content": "b"}}]}  # slug missing
        self.assertEqual(tcc.heal_bridge_entries(args), 0)

    def test_named_entry_is_untouched(self):
        self._registry(PUT_PAGE)
        args = {"calls": [{"name": "mcp__gbrain__put_page",
                           "arguments": {"slug": "a", "content": "b"}}]}
        self.assertEqual(tcc.heal_bridge_entries(args), 0)
        self.assertEqual(args["calls"][0]["name"], "mcp__gbrain__put_page")

    def test_split_entry_is_merged(self):
        self._registry(PUT_PAGE)
        args = {"calls": [{"arguments": {"slug": "a", "content": "b"}},
                          {"name": "mcp__gbrain__put_page"}]}
        self.assertEqual(tcc.heal_bridge_entries(args), 1)
        self.assertEqual(args["calls"], [{"name": "mcp__gbrain__put_page",
                                          "arguments": {"slug": "a", "content": "b"}}])

    def test_split_entry_with_extra_call_recalls_name_without_merging(self):
        """Three entries: the nameless one is named from its signature, nothing is merged."""
        self._registry(PUT_PAGE)
        args = {"calls": [{"arguments": {"slug": "a", "content": "b"}},
                          {"name": "mcp__gbrain__put_page"},
                          {"name": "mcp__gbrain__get_page", "arguments": {"slug": "a"}}]}
        self.assertEqual(tcc.heal_bridge_entries(args), 1)
        self.assertEqual(len(args["calls"]), 3)
        self.assertEqual(args["calls"][0]["name"], "mcp__gbrain__put_page")

    def test_no_calls_key_is_a_noop(self):
        self.assertEqual(tcc.heal_bridge_entries({"name": "x", "arguments": {}}), 0)
        self.assertEqual(tcc.heal_bridge_entries({}), 0)

    def test_singleton_calls_object_is_normalized(self):
        self._registry(PUT_PAGE)
        args = {"calls": {"arguments": {"slug": "a", "content": "b"}}}
        self.assertEqual(tcc.heal_bridge_entries(args), 1)
        self.assertEqual(args["calls"][0]["name"], "mcp__gbrain__put_page")


class TestPatchInstall(unittest.TestCase):
    """The heal only counts if it reaches the modules that hold the repairer's name.

    Not a ``_RegistryCase``: this one needs the *real* ``tools.registry`` importable.
    """

    def test_installed_repairer_is_used_by_its_importers(self):
        try:
            import agent.conversation_loop as conversation_loop
            import agent.message_sanitization as sanitization
        except Exception as exc:  # pragma: no cover — flake check has no hermes package
            self.skipTest(f"hermes package unavailable: {exc}")
        original = sanitization._repair_tool_call_arguments
        broken = '{"calls": [{"name": "x", "arguments": {"a": 1}}]}'
        tcc._install_patches()
        self.addCleanup(setattr, sanitization, "_repair_tool_call_arguments", original)
        patched = sanitization._repair_tool_call_arguments
        self.assertIsNot(patched, original)
        self.assertIs(conversation_loop._repair_tool_call_arguments, patched)
        self.assertEqual(json.loads(patched(broken)), {"calls": [{"name": "x", "arguments": {"a": 1}}]})
        # already-valid and genuinely unrepairable input still goes to the upstream path
        self.assertEqual(patched('{"calls": []}'), '{"calls":[]}')
        self.assertEqual(patched('{"calls": [{"content": "truncat'), "{}")


if __name__ == "__main__":
    unittest.main()
