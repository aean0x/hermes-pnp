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
# Live registry shape: the MCP client registers a FLAT dict (no "function" wrapper) —
# verified against tools/mcp_tool_schema.py::_convert_mcp_schema and the wire schema of
# the running composio server.
WORKBENCH_FLAT = {
    "name": "mcp__composio__COMPOSIO_REMOTE_WORKBENCH",
    "description": "[authed via proxy] Process REMOTE FILES …",
    "parameters": {"type": "object", "properties": {
        "code_to_execute": {"type": "string"}, "thought": {"type": "string"},
        "current_step": {"type": "string"}, "current_step_metric": {"type": "string"},
        "session_id": {"type": "string"}},
        "required": ["code_to_execute"], "additionalProperties": False}}
WORKBENCH_FLAT_BASH = {
    "name": "mcp__composio__COMPOSIO_REMOTE_BASH_TOOL",
    "description": "[authed via proxy] bash",
    "parameters": {"type": "object", "properties": {"command": {"type": "string"},
                                                   "session_id": {"type": "string"}},
                   "required": ["command"]}}
# The real 2026-09-23 inbox-triage emission (state.db msg 297689 / 297693 / 297695):
# the entry carries the arguments, the name sits at the top level, and the call is
# rejected with "missing required argument(s): code_to_execute" five times in one run.
HOISTED_WORKBENCH = {"calls": [{"arguments": {
    "code_to_execute": "import json;D=json.load(open('/mnt/files/mex/trip.json'))",
    "current_step": "PARSING_RESULTS", "thought": "Dump all Gmail, Outlook and calendar rows"}}],
    "name": "mcp__composio__COMPOSIO_REMOTE_WORKBENCH"}


def _schema_name(schema):
    """Tool name from either registry schema shape (flat MCP registration or wrapped)."""
    fn = schema.get("function")
    return (fn or {}).get("name") if isinstance(fn, dict) else schema.get("name")


class _Entry:
    """Registry entry stand-in for ``is_deferrable_tool_name``."""

    def __init__(self, schema):
        self.schema = schema
        self.toolset = "mcp-composio"
        self.name = schema.get("name")


class _FakeRegistry:
    def __init__(self, schemas):
        self._schemas = schemas

    def get_all_tool_names(self):
        return list(self._schemas)

    def get_schema(self, name):
        return self._schemas.get(name)

    def get_entry(self, name, scope=None):
        schema = self._schemas.get(name)
        return _Entry(schema) if schema else None

    def get_toolset_for_tool(self, name):
        return "mcp-composio" if name in self._schemas else None


class _RegistryCase(unittest.TestCase):
    """Install a fake ``tools.registry`` so name recall can be exercised offline."""

    def _registry(self, *schemas):
        fake = types.ModuleType("tools.registry")
        fake.registry = _FakeRegistry({_schema_name(s): s for s in schemas})
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


class TestLiveRegistrySchemaShape(_RegistryCase):
    """The MCP client registers a FLAT schema dict; name recall must read that shape too.

    Reading only the ``{"type": "function", "function": {...}}`` wrapper its own fixtures
    use meant recall never matched a real MCP tool: a nameless entry stayed nameless, the
    resolver errored, and the fallback then rejected the call for a missing required
    argument the model had in fact supplied.
    """

    def test_flat_mcp_schema_recalls_the_workbench(self):
        self._registry(WORKBENCH_FLAT, WORKBENCH_FLAT_BASH, MULTI_EXECUTE)
        args = {"calls": [{"arguments": dict(HOISTED_WORKBENCH["calls"][0]["arguments"])}]}
        self.assertEqual(tcc.heal_bridge_entries(args), 1)
        self.assertEqual(args["calls"][0]["name"], "mcp__composio__COMPOSIO_REMOTE_WORKBENCH")

    def test_flat_schema_without_a_unique_fit_is_refused(self):
        self._registry(WORKBENCH_FLAT_BASH, MULTI_EXECUTE)
        args = {"calls": [{"arguments": {"code_to_execute": "print(1)"}}]}
        self.assertEqual(tcc.heal_bridge_entries(args), 0)
        self.assertNotIn("name", args["calls"][0])


class TestHoistedName(_RegistryCase):
    """(5) the name hoisted to the top level beside ``calls`` — the 2026-09-23 emission."""

    def test_single_nameless_entry_takes_the_hoisted_name(self):
        self._registry(WORKBENCH_FLAT, WORKBENCH_FLAT_BASH)
        args = json.loads(json.dumps(HOISTED_WORKBENCH))
        self.assertEqual(tcc.heal_bridge_entries(args), 1)
        entry = args["calls"][0]
        self.assertEqual(entry["name"], "mcp__composio__COMPOSIO_REMOTE_WORKBENCH")
        self.assertEqual(entry["arguments"],
                         HOISTED_WORKBENCH["calls"][0]["arguments"])

    def test_two_entries_are_never_named_from_the_top_level(self):
        self._registry(WORKBENCH_FLAT_BASH)  # no signature fit for either entry
        args = {"calls": [{"arguments": {"zqx": 1}}, {"arguments": {"qqq": 2}}],
                "name": "mcp__composio__COMPOSIO_REMOTE_WORKBENCH"}
        self.assertEqual(tcc.heal_bridge_entries(args), 0)
        self.assertNotIn("name", args["calls"][0])
        self.assertNotIn("name", args["calls"][1])

    def test_bridge_name_is_never_hoisted(self):
        self._registry(WORKBENCH_FLAT_BASH)
        args = {"calls": [{"arguments": {"zqx": 1}}], "name": "tool_call"}
        self.assertEqual(tcc.heal_bridge_entries(args), 0)
        self.assertNotIn("name", args["calls"][0])

    def test_legacy_single_shape_is_untouched(self):
        self._registry(WORKBENCH_FLAT)
        args = {"name": "mcp__composio__COMPOSIO_REMOTE_WORKBENCH",
                "arguments": {"code_to_execute": "print(1)"}}
        self.assertEqual(tcc.heal_bridge_entries(args), 0)
        self.assertEqual(args["arguments"], {"code_to_execute": "print(1)"})


class TestFallbackArgs(unittest.TestCase):
    """The mcp-prefix / core-via-bridge fallback must not fabricate an empty call."""

    def test_batch_shape_entry_arguments_are_used(self):
        self.assertEqual(tcc._call_args({"calls": [{"arguments": {"code_to_execute": "x"}}]}),
                         {"code_to_execute": "x"})

    def test_legacy_top_level_arguments_win(self):
        self.assertEqual(tcc._call_args({"name": "x", "arguments": {"a": 1}}), {"a": 1})

    def test_multi_entry_batch_stays_empty(self):
        self.assertEqual(
            tcc._call_args({"calls": [{"arguments": {"a": 1}}, {"arguments": {"b": 2}}]}), {})

    def test_no_arguments_anywhere(self):
        self.assertEqual(tcc._call_args({"calls": [{"name": "x"}]}), {})


class TestEndToEndWorkbenchCall(unittest.TestCase):
    """The real payload through the patched resolver and the deferred-schema probe.

    Skips when the hermes package is unavailable (the flake check has no hermes).
    """

    def _fake_registry(self, *schemas):
        fake = types.ModuleType("tools.registry")
        fake.registry = _FakeRegistry({_schema_name(s): s for s in schemas})
        self.addCleanup(sys.modules.pop, "tools.registry", None)
        sys.modules["tools.registry"] = fake
        tcc._infer_deferred_name.cache_clear()
        self.addCleanup(tcc._infer_deferred_name.cache_clear)

    def _hermes_modules(self):
        try:
            import agent.message_sanitization as ms
            import tools.tool_search as ts
            import tools.tool_search_validation as tsv
        except Exception as exc:  # pragma: no cover — flake check has no hermes package
            self.skipTest(f"hermes package unavailable: {exc}")
        # This test installs the real patches; put every module back afterwards so the
        # install test below still sees an unpatched baseline whatever order we run in.
        snapshot = (ts.resolve_underlying_call, ts.scoped_deferrable_names,
                    ms._repair_tool_call_arguments, tcc._PATCHED)

        def restore():
            (ts.resolve_underlying_call, ts.scoped_deferrable_names,
             ms._repair_tool_call_arguments, tcc._PATCHED) = snapshot

        self.addCleanup(restore)
        tcc._install_patches()
        return ts, tsv

    def test_hoisted_payload_dispatches_after_the_heal(self):
        ts, tsv = self._hermes_modules()
        self._fake_registry(WORKBENCH_FLAT)
        args = json.loads(json.dumps(HOISTED_WORKBENCH))
        name, resolved, err = ts.resolve_underlying_call(args)
        self.assertIsNone(err)
        self.assertEqual(name, "mcp__composio__COMPOSIO_REMOTE_WORKBENCH")
        self.assertEqual(resolved["code_to_execute"],
                         HOISTED_WORKBENCH["calls"][0]["arguments"]["code_to_execute"])
        self.assertIsNone(tsv.validate_deferred_call_args(name, resolved))

    def test_nameless_entry_without_a_hoist_reports_the_real_defect(self):
        """No name anywhere: the model must hear that, not a required-argument error."""
        ts, _ = self._hermes_modules()
        self._fake_registry(WORKBENCH_FLAT_BASH)  # no signature fit → no recall
        _, _, err = ts.resolve_underlying_call(
            {"calls": [{"arguments": {"code_to_execute": "print(1)"}}]})
        self.assertIsNotNone(err)
        self.assertIn("requires a 'name'", err)


if __name__ == "__main__":
    unittest.main()
