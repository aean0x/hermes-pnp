import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from http_proxy import (
    apply_rpc_request,
    backend_for_path,
    load_client_token,
    resolve_headers,
    serve,
)


class AuthHeaders(unittest.TestCase):
    def test_inject_loads_secret(self):
        headers = resolve_headers(
            {
                "secrets": {"Authorization": {"value": "ck_host", "prefix": "Bearer "}},
            },
            None,
        )
        self.assertEqual(headers["Authorization"], "Bearer ck_host")

    def test_passthrough_skips_secrets(self):
        headers = resolve_headers(
            {
                "auth": {"mode": "passthrough"},
                "secrets": {"Authorization": {"value": "ck_host", "prefix": "Bearer "}},
                "headers": {"X-Static": "1"},
            },
            None,
        )
        self.assertNotIn("Authorization", headers)
        self.assertEqual(headers["X-Static"], "1")


class ClientToken(unittest.TestCase):
    def test_none_mode_returns_none(self):
        self.assertIsNone(load_client_token(None, None))
        self.assertIsNone(load_client_token({"mode": "none"}, None))

    def test_token_value(self):
        self.assertEqual(load_client_token({"mode": "token", "value": "abc"}, None), "abc")

    def test_token_empty_value_raises(self):
        with self.assertRaises(RuntimeError):
            load_client_token({"mode": "token", "value": "  "}, None)


class PathMatch(unittest.TestCase):
    def test_longest_prefix(self):
        backends = {
            "a": {"path": "/mcp"},
            "b": {"path": "/mcp/composio"},
        }
        name, backend = backend_for_path("/mcp/composio", backends)
        self.assertEqual(name, "b")
        self.assertEqual(backend["path"], "/mcp/composio")


class RpcRewrite(unittest.TestCase):
    def test_rewrites_inner_gmail_query(self):
        backend = {
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
                    "prefix": "GMAIL_",
                    "args": {"query": {"requireTokens": ["-label:x"]}},
                }
            },
        }
        payload = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "COMPOSIO_MULTI_EXECUTE_TOOL",
                "arguments": {
                    "tools": [
                        {
                            "tool_slug": "GMAIL_FETCH_EMAILS",
                            "arguments": {"query": "is:unread"},
                        }
                    ]
                },
            },
        }
        out, err = apply_rpc_request(payload, backend, "composio")
        self.assertIsNone(err)
        q = out["params"]["arguments"]["tools"][0]["arguments"]["query"]
        self.assertEqual(q, "is:unread -label:x")

    def test_deny_returns_rpc_error(self):
        backend = {"tools": {"deny": ["SECRET_*"]}}
        payload = {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {"name": "SECRET_LEAK", "arguments": {}},
        }
        _, err = apply_rpc_request(payload, backend, "x")
        self.assertEqual(err["id"], 9)
        self.assertIn("denied", err["error"]["message"])


class EchoUpstream(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        msg = json.loads(raw.decode("utf-8"))
        if msg.get("method") == "initialize":
            result = {
                "jsonrpc": "2.0",
                "id": msg.get("id"),
                "result": {"protocolVersion": "2025-03-26", "capabilities": {}, "serverInfo": {"name": "echo"}},
            }
        else:
            result = {
                "jsonrpc": "2.0",
                "id": msg.get("id"),
                "result": {
                    "echo": msg,
                    "auth": self.headers.get("Authorization"),
                    "proxy_token": self.headers.get("X-MCP-Proxy-Token"),
                },
            }
        body = json.dumps(result).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class EndToEnd(unittest.TestCase):
    def test_secret_injection_and_filter(self):
        upstream = HTTPServer(("127.0.0.1", 0), EchoUpstream)
        up_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        up_thread.start()
        up_port = upstream.server_address[1]
        backend = {
            "path": "/composio",
            "upstream": f"http://127.0.0.1:{up_port}/mcp",
            "secrets": {"Authorization": {"value": "ck_test", "prefix": "Bearer "}},
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
                    "prefix": "GMAIL_",
                    "args": {
                        "query": {
                            "requireTokens": ["-label:old"],
                            "denyTokens": ["label:old"],
                        }
                    },
                }
            },
        }
        proxy = serve("127.0.0.1:0", {"composio": backend}, None)
        px_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
        px_thread.start()
        px_port = proxy.server_address[1]
        try:
            call = {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "COMPOSIO_MULTI_EXECUTE_TOOL",
                    "arguments": {
                        "tools": [
                            {
                                "tool_slug": "GMAIL_FETCH_EMAILS",
                                "arguments": {"query": "label:old is:unread"},
                            }
                        ]
                    },
                },
            }
            req = Request(
                f"http://127.0.0.1:{px_port}/composio",
                data=json.dumps(call).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req, timeout=5) as resp:
                out = json.loads(resp.read().decode("utf-8"))
            echo = out["result"]["echo"]
            q = echo["params"]["arguments"]["tools"][0]["arguments"]["query"]
            self.assertEqual(q, "is:unread -label:old")
            self.assertEqual(out["result"]["auth"], "Bearer ck_test")
        finally:
            proxy.shutdown()
            upstream.shutdown()

    def test_passthrough_forwards_client_authorization(self):
        upstream = HTTPServer(("127.0.0.1", 0), EchoUpstream)
        threading.Thread(target=upstream.serve_forever, daemon=True).start()
        backend = {
            "path": "/composio",
            "upstream": f"http://127.0.0.1:{upstream.server_address[1]}/mcp",
            "auth": {"mode": "passthrough"},
            "secrets": {"Authorization": {"value": "ck_host", "prefix": "Bearer "}},
        }
        proxy = serve("127.0.0.1:0", {"composio": backend}, None)
        threading.Thread(target=proxy.serve_forever, daemon=True).start()
        try:
            req = Request(
                f"http://127.0.0.1:{proxy.server_address[1]}/composio",
                data=json.dumps(
                    {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}}
                ).encode(),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer client-token",
                },
                method="POST",
            )
            with urlopen(req, timeout=5) as resp:
                out = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(out["result"]["auth"], "Bearer client-token")
        finally:
            proxy.shutdown()
            upstream.shutdown()

    def test_client_token_required_and_stripped(self):
        upstream = HTTPServer(("127.0.0.1", 0), EchoUpstream)
        threading.Thread(target=upstream.serve_forever, daemon=True).start()
        backend = {
            "path": "/composio",
            "upstream": f"http://127.0.0.1:{upstream.server_address[1]}/mcp",
            "auth": {"mode": "passthrough"},
        }
        proxy = serve(
            "127.0.0.1:0",
            {"composio": backend},
            None,
            {"mode": "token", "value": "proxy-secret"},
        )
        threading.Thread(target=proxy.serve_forever, daemon=True).start()
        ping = {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}}
        try:
            denied = Request(
                f"http://127.0.0.1:{proxy.server_address[1]}/composio",
                data=json.dumps(ping).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(HTTPError) as raised:
                urlopen(denied, timeout=5)
            self.assertEqual(raised.exception.code, 401)
            health = Request(
                f"http://127.0.0.1:{proxy.server_address[1]}/healthz",
                method="GET",
            )
            with urlopen(health, timeout=5) as resp:
                self.assertEqual(json.loads(resp.read().decode())["ok"], True)
            ok = Request(
                f"http://127.0.0.1:{proxy.server_address[1]}/composio",
                data=json.dumps(ping).encode(),
                headers={
                    "Content-Type": "application/json",
                    "X-MCP-Proxy-Token": "proxy-secret",
                    "Authorization": "Bearer client-token",
                },
                method="POST",
            )
            with urlopen(ok, timeout=5) as resp:
                out = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(out["result"]["auth"], "Bearer client-token")
            self.assertIsNone(out["result"]["proxy_token"])
        finally:
            proxy.shutdown()
            upstream.shutdown()


if __name__ == "__main__":
    unittest.main()
