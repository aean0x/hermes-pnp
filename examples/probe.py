"""Initialize + tools/list against a Streamable HTTP MCP URL (optional tools/call)."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request


def post(url: str, payload: dict, headers: dict, session: str | None) -> tuple[dict, dict]:
    data = json.dumps(payload).encode("utf-8")
    hdrs = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        **headers,
    }
    if session:
        hdrs["Mcp-Session-Id"] = session
    req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
    with urllib.request.urlopen(req, timeout=90) as resp:
        raw = resp.read()
        out_headers = {k.lower(): v for k, v in resp.headers.items()}
        status = resp.status
    if not raw:
        return {"ok": True, "empty": True, "status": status}, out_headers
    body = raw.decode("utf-8", errors="replace")
    chunks = [line[5:].strip() for line in body.splitlines() if line.startswith("data:")]
    text = "\n".join(chunks) if chunks else body
    if not text.strip():
        return {"ok": True, "empty": True, "status": status}, out_headers
    return json.loads(text), out_headers


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:3140/composio")
    parser.add_argument("--bearer", default="", help="Optional Authorization Bearer (usually unused; proxy injects secrets)")
    parser.add_argument("--call", default="", help="Optional tools/call name")
    parser.add_argument("--args", default="{}", help="JSON object for tools/call arguments")
    args = parser.parse_args()
    headers = {}
    if args.bearer:
        headers["Authorization"] = f"Bearer {args.bearer}"

    _, hdrs = post(
        args.url,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "mcp-proxy-probe", "version": "0.1"},
            },
        },
        headers,
        None,
    )
    session = hdrs.get("mcp-session-id")
    post(args.url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, headers, session)

    listed, _ = post(
        args.url,
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        headers,
        session,
    )
    names = [t.get("name") for t in (listed.get("result") or {}).get("tools") or []]
    print("tools", names)

    if not args.call:
        print("OK", args.url)
        return 0

    call, _ = post(
        args.url,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": args.call, "arguments": json.loads(args.args)},
        },
        headers,
        session,
    )
    print(json.dumps(call, indent=2)[:8000])
    if call.get("error") or (call.get("result") or {}).get("isError"):
        print("FAIL", file=sys.stderr)
        return 1
    print("OK", args.url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
