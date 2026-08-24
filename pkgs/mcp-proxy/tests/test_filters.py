import unittest

from filters import (
    AUTH_INJECT_TAG,
    Denied,
    apply_call,
    auth_mode,
    denial_result,
    filter_listed_tools,
    looks_like_permanent_denial,
    rewrite_call_result_if_denied,
    rewrite_tokens,
)


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

    def test_deny_unwrapped_inner_slug(self):
        backend = {
            **COMPOSIO,
            "tools": {"deny": ["GMAIL_LIST_LABELS", "GMAIL_GET_ATTACHMENT"]},
        }
        with self.assertRaises(Denied) as ctx:
            apply_call(
                "COMPOSIO_MULTI_EXECUTE_TOOL",
                {"tools": [{"tool_slug": "GMAIL_LIST_LABELS", "arguments": {}}]},
                backend,
            )
        self.assertIn("GMAIL_LIST_LABELS", str(ctx.exception))
        apply_call(
            "COMPOSIO_MULTI_EXECUTE_TOOL",
            {
                "tools": [
                    {
                        "tool_slug": "GMAIL_FETCH_EMAILS",
                        "arguments": {"query": "is:unread"},
                    }
                ]
            },
            backend,
        )

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

    def test_inject_tag_when_secrets_configured(self):
        tools = [
            {"name": "A", "description": "Find tools."},
            {"name": "B", "description": "Run tools."},
        ]
        out = filter_listed_tools(
            tools,
            {"secrets": {"Authorization": {"file": "/run/cred"}}},
        )
        self.assertTrue(out[0]["description"].startswith(AUTH_INJECT_TAG))
        self.assertTrue(out[1]["description"].startswith(AUTH_INJECT_TAG))
        self.assertIn("Find tools.", out[0]["description"])

    def test_no_tag_on_passthrough_even_with_secrets(self):
        tools = [{"name": "A", "description": "Find tools."}]
        out = filter_listed_tools(
            tools,
            {
                "secrets": {"Authorization": {"file": "/run/cred"}},
                "auth": {"mode": "passthrough"},
            },
        )
        self.assertEqual(out[0]["description"], "Find tools.")

    def test_no_tag_when_no_secrets(self):
        tools = [{"name": "A", "description": "Find tools."}]
        out = filter_listed_tools(tools, {})
        self.assertEqual(out[0]["description"], "Find tools.")
        self.assertEqual(auth_mode({}), "passthrough")
        self.assertEqual(auth_mode({"secrets": {"Authorization": {}}}), "inject")
        self.assertEqual(
            auth_mode({"secrets": {"Authorization": {}}, "auth": {"mode": "passthrough"}}),
            "passthrough",
        )


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

    def test_mail_surface_query_rewrite(self):
        backend = {
            **COMPOSIO,
            "toolkits": {
                "gmail": {
                    "match": {"names": ["GMAIL_FETCH_EMAILS", "GMAIL_LIST_THREADS"]},
                    "args": {
                        "query": {
                            "prepend": "(in:inbox OR in:sent OR in:drafts) ",
                            "requireTokens": ["-label:agent-blocked"],
                            "denyTokens": [
                                "label:agent-blocked",
                                "in:anywhere",
                                "in:spam",
                            ],
                        },
                        "label_ids": {"unset": True},
                        "include_spam_trash": {"denyValues": [True]},
                    },
                }
            },
        }
        decision = apply_call(
            "COMPOSIO_MULTI_EXECUTE_TOOL",
            {
                "tools": [
                    {
                        "tool_slug": "GMAIL_FETCH_EMAILS",
                        "arguments": {
                            "query": "in:anywhere label:agent-blocked is:unread",
                            "label_ids": ["Label_16"],
                        },
                    }
                ]
            },
            backend,
        )
        args = decision.arguments["tools"][0]["arguments"]
        self.assertEqual(
            args["query"],
            "(in:inbox OR in:sent OR in:drafts) is:unread -label:agent-blocked",
        )
        self.assertNotIn("label_ids", args)
        with self.assertRaises(Denied):
            apply_call(
                "COMPOSIO_MULTI_EXECUTE_TOOL",
                {
                    "tools": [
                        {
                            "tool_slug": "GMAIL_FETCH_EMAILS",
                            "arguments": {"include_spam_trash": True},
                        }
                    ]
                },
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


class PermanentDenial(unittest.TestCase):
    def test_proxy_message_is_permanent(self) -> None:
        self.assertTrue(looks_like_permanent_denial("mcp-proxy: tool GMAIL_X is denied"))
        self.assertTrue(looks_like_permanent_denial("do NOT retry this request"))
        self.assertFalse(looks_like_permanent_denial("rate limited, try again later"))

    def test_rewrite_upstream_composio_denial(self) -> None:
        payload = {
            "jsonrpc": "2.0",
            "id": 3,
            "result": {
                "content": [
                    {"type": "text", "text": "Permission denied. do NOT retry."}
                ],
                "isError": True,
            },
        }
        out = rewrite_call_result_if_denied(payload)
        self.assertIsNotNone(out)
        assert out is not None
        self.assertFalse(out["result"]["isError"])
        self.assertTrue(out["result"]["content"][0]["text"].startswith("POLICY_DENIED:"))

    def test_denial_result_is_not_rpc_error(self) -> None:
        body = denial_result(4, "mcp-proxy: tool X is denied")
        self.assertNotIn("error", body)
        self.assertFalse(body["result"]["structuredContent"]["retry"])


if __name__ == "__main__":
    unittest.main()
