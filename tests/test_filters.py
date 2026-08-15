import unittest

from filters import Denied, apply_call, filter_listed_tools, rewrite_tokens


GMAIL_EXCLUDE = "-label:archive"
GMAIL_INCLUDE = "label:archive"

COMPOSIO = {
    "unwrap": [
        {
            "tool": "COMPOSIO_MULTI_EXECUTE_TOOL",
            "each": "tools",
            "name": "tool_slug",
            "args": "arguments",
        }
    ],
    "toolkits": {
        "gmail": {
            "match": {"prefix": "GMAIL_"},
            "args": {
                "query": {
                    "requireTokens": [GMAIL_EXCLUDE],
                    "denyTokens": [GMAIL_INCLUDE],
                }
            },
        }
    },
}


class TokenRewrite(unittest.TestCase):
    def test_exact_tokens_not_substrings(self):
        self.assertEqual(
            rewrite_tokens(GMAIL_INCLUDE, [GMAIL_EXCLUDE], [GMAIL_INCLUDE]),
            GMAIL_EXCLUDE,
        )

    def test_keeps_existing_exclude(self):
        q = f"is:unread {GMAIL_EXCLUDE}"
        self.assertEqual(rewrite_tokens(q, [GMAIL_EXCLUDE], [GMAIL_INCLUDE]), q)

    def test_appends_when_missing(self):
        self.assertEqual(
            rewrite_tokens("is:unread", [GMAIL_EXCLUDE], [GMAIL_INCLUDE]),
            f"is:unread {GMAIL_EXCLUDE}",
        )


class SurfaceTools(unittest.TestCase):
    def test_deny_surface_tool(self):
        backend = {"tools": {"deny": ["COMPOSIO_REMOTE_BASH_TOOL"]}}
        with self.assertRaises(Denied):
            apply_call("COMPOSIO_REMOTE_BASH_TOOL", {"command": "id"}, backend)

    def test_allowlist(self):
        backend = {"tools": {"allow": ["COMPOSIO_MULTI_EXECUTE_TOOL"]}}
        apply_call("COMPOSIO_MULTI_EXECUTE_TOOL", {"tools": []}, {**backend, "unwrap": []})
        with self.assertRaises(Denied):
            apply_call("COMPOSIO_REMOTE_BASH_TOOL", {}, backend)

    def test_list_filter(self):
        tools = [{"name": "keep"}, {"name": "drop_me"}]
        out = filter_listed_tools(tools, {"tools": {"deny": ["drop_*"]}})
        self.assertEqual([t["name"] for t in out], ["keep"])

    def test_advertise_by_tool_prepend(self):
        tools = [
            {"name": "COMPOSIO_SEARCH_TOOLS", "description": "Find tools across apps."},
            {"name": "COMPOSIO_MULTI_EXECUTE_TOOL", "description": "Run tools."},
        ]
        out = filter_listed_tools(
            tools,
            {
                "advertise": {
                    "byTool": {
                        "COMPOSIO_SEARCH_TOOLS": {
                            "prepend": "Host MCP auth is already injected. "
                        }
                    }
                }
            },
        )
        self.assertTrue(out[0]["description"].startswith("Host MCP auth is already injected."))
        self.assertEqual(out[1]["description"], "Run tools.")


class ToolkitGmail(unittest.TestCase):
    def _exec(self, slug: str, arguments: dict) -> dict:
        decision = apply_call(
            "COMPOSIO_MULTI_EXECUTE_TOOL",
            {
                "tools": [{"tool_slug": slug, "arguments": arguments}],
                "sync_response_to_workbench": False,
            },
            COMPOSIO,
        )
        return decision.arguments["tools"][0]["arguments"]

    def test_injects_exclude_when_query_missing(self):
        args = self._exec("GMAIL_FETCH_EMAILS", {"max_results": 5})
        self.assertEqual(args["query"], GMAIL_EXCLUDE)
        self.assertEqual(args["max_results"], 5)

    def test_strips_positive_label_and_requires_negative(self):
        args = self._exec("GMAIL_FETCH_EMAILS", {"query": f"is:unread {GMAIL_INCLUDE}"})
        self.assertEqual(args["query"], f"is:unread {GMAIL_EXCLUDE}")
        self.assertNotIn(GMAIL_INCLUDE, args["query"].split())

    def test_applies_to_any_gmail_query_tool(self):
        args = self._exec("GMAIL_LIST_THREADS", {"query": "newer_than:7d"})
        self.assertEqual(args["query"], f"newer_than:7d {GMAIL_EXCLUDE}")

    def test_non_gmail_untouched(self):
        args = self._exec("SLACK_SEND_MESSAGE", {"text": "hi", "query": "x"})
        self.assertEqual(args["query"], "x")

    def test_list_labels_not_injected_when_names_match(self):
        backend = {
            **COMPOSIO,
            "toolkits": {
                "gmail": {
                    "match": {"names": ["GMAIL_FETCH_EMAILS", "GMAIL_LIST_THREADS"]},
                    "args": {"query": {"requireTokens": [GMAIL_EXCLUDE]}},
                }
            },
        }
        decision = apply_call(
            "COMPOSIO_MULTI_EXECUTE_TOOL",
            {"tools": [{"tool_slug": "GMAIL_LIST_LABELS", "arguments": {}}]},
            backend,
        )
        self.assertNotIn("query", decision.arguments["tools"][0]["arguments"])

    def test_direct_tool_name_without_unwrap(self):
        backend = {
            "toolkits": {
                "gmail": {
                    "prefix": "GMAIL_",
                    "args": {"query": {"requireTokens": [GMAIL_EXCLUDE]}},
                }
            }
        }
        decision = apply_call("GMAIL_FETCH_EMAILS", {"query": "is:unread"}, backend)
        self.assertEqual(decision.arguments["query"], f"is:unread {GMAIL_EXCLUDE}")

    def test_toolkit_deny_glob(self):
        backend = {
            **COMPOSIO,
            "toolkits": {
                "gmail": {
                    "match": {"prefix": "GMAIL_"},
                    "deny": ["GMAIL_DELETE_*"],
                }
            },
        }
        with self.assertRaises(Denied):
            apply_call(
                "COMPOSIO_MULTI_EXECUTE_TOOL",
                {"tools": [{"tool_slug": "GMAIL_DELETE_MESSAGE", "arguments": {}}]},
                backend,
            )

    def test_by_tool_override_set(self):
        backend = {
            "toolkits": {
                "gmail": {
                    "match": {"prefix": "GMAIL_"},
                    "args": {"query": {"requireTokens": ["-label:global"]}},
                    "byTool": {
                        "GMAIL_FETCH_EMAILS": {
                            "args": {"query": {"set": "is:inbox"}},
                        }
                    },
                }
            }
        }
        decision = apply_call("GMAIL_FETCH_EMAILS", {"query": "nope"}, backend)
        self.assertEqual(decision.arguments["query"], "is:inbox")


if __name__ == "__main__":
    unittest.main()
