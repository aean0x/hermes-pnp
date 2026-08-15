"""Loopback Streamable-HTTP reverse proxy with JSON-RPC policy hooks."""

from __future__ import annotations

import json
import logging
import os
from http.client import HTTPConnection, HTTPSConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from filters import Denied, apply_call, filter_listed_tools

log = logging.getLogger("mcp-proxy")

HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}


def load_header_value(spec: dict[str, Any], credentials_dir: str | None) -> str:
    prefix = spec.get("prefix") or ""
    if "value" in spec:
        return prefix + str(spec["value"])
    path = spec.get("file")
    if not path and spec.get("credential"):
        if not credentials_dir:
            raise RuntimeError(f"credential {spec['credential']} needs CREDENTIALS_DIRECTORY")
        path = os.path.join(credentials_dir, spec["credential"])
    if not path:
        raise RuntimeError("header spec needs file, credential, or value")
    with open(path, encoding="utf-8") as handle:
        return prefix + handle.read().strip()


def resolve_headers(backend: dict[str, Any], credentials_dir: str | None) -> dict[str, str]:
    out = dict(backend.get("headers") or {})
    for name, spec in (backend.get("secrets") or {}).items():
        out[name] = load_header_value(spec, credentials_dir)
    return out


def backend_for_path(path: str, backends: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    matches: list[tuple[int, str, dict[str, Any]]] = []
    for name, backend in backends.items():
        mount = backend.get("path") or f"/{name}"
        if path == mount or path.startswith(mount + "/"):
            matches.append((len(mount), name, backend))
    if not matches:
        return None
    matches.sort(reverse=True)
    _, name, backend = matches[0]
    return name, backend


def parse_rpc(body: bytes) -> Any:
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def apply_rpc_request(payload: Any, backend: dict[str, Any], backend_name: str) -> tuple[Any, dict[str, Any] | None]:
    """Return (possibly rewritten payload, error response or None)."""
    if isinstance(payload, list):
        rewritten = []
        for item in payload:
            item, err = apply_rpc_request(item, backend, backend_name)
            if err is not None:
                return payload, err
            rewritten.append(item)
        return rewritten, None
    if not isinstance(payload, dict):
        return payload, None
    if payload.get("method") != "tools/call":
        return payload, None
    params = payload.get("params")
    if not isinstance(params, dict):
        return payload, {
            "jsonrpc": "2.0",
            "id": payload.get("id"),
            "error": {"code": -32602, "message": "mcp-proxy: tools/call params must be an object"},
        }
    name = params.get("name")
    try:
        decision = apply_call(name if isinstance(name, str) else "", params.get("arguments"), backend)
    except Denied as exc:
        log.warning("deny backend=%s tool=%s reason=%s", backend_name, name, exc.reason)
        return payload, {
            "jsonrpc": "2.0",
            "id": payload.get("id"),
            "error": {"code": -32000, "message": f"mcp-proxy: {exc.reason}"},
        }
    params["arguments"] = decision.arguments
    payload["params"] = params
    if decision.notes:
        log.info("rewrite backend=%s tool=%s %s", backend_name, name, ",".join(decision.notes))
    return payload, None


def _extract_sse_json(body: bytes) -> tuple[list[str], Any | None]:
    text = body.decode("utf-8", errors="replace")
    prefix: list[str] = []
    data_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
        else:
            prefix.append(line)
    if not data_lines:
        return prefix, None
    try:
        return prefix, json.loads("\n".join(data_lines))
    except json.JSONDecodeError:
        return prefix, None


def filter_rpc_response(body: bytes, content_type: str, backend: dict[str, Any]) -> bytes | None:
    ctype = content_type.lower()
    if "text/event-stream" in ctype:
        prefix, payload = _extract_sse_json(body)
        rewritten = _filter_tools_list_payload(payload, backend)
        if rewritten is None:
            return None
        lines = prefix + [f"data: {json.dumps(rewritten, separators=(',', ':'))}", ""]
        return ("\n".join(lines) + "\n").encode("utf-8")
    payload = parse_rpc(body)
    rewritten = _filter_tools_list_payload(payload, backend)
    if rewritten is None:
        return None
    return json.dumps(rewritten, separators=(",", ":")).encode("utf-8")


def _filter_tools_list_payload(payload: Any, backend: dict[str, Any]) -> Any | None:
    if not isinstance(payload, dict):
        return None
    result = payload.get("result")
    if not isinstance(result, dict) or "tools" not in result:
        return None
    tools = result.get("tools")
    if not isinstance(tools, list):
        return None
    filtered = filter_listed_tools(tools, backend)
    if filtered is tools or filtered == tools:
        return None
    result = dict(result)
    result["tools"] = filtered
    out = dict(payload)
    out["result"] = result
    return out


def _connect(parsed) -> HTTPConnection:
    timeout = 180
    if parsed.scheme == "https":
        return HTTPSConnection(parsed.hostname, parsed.port or 443, timeout=timeout)
    return HTTPConnection(parsed.hostname, parsed.port or 80, timeout=timeout)


class ProxyHandler(BaseHTTPRequestHandler):
    server_version = "mcp-proxy/1"
    protocol_version = "HTTP/1.1"

    @property
    def backends(self) -> dict[str, Any]:
        return self.server.backends  # type: ignore[attr-defined]

    @property
    def credentials_dir(self) -> str | None:
        return self.server.credentials_dir  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        log.info("%s - %s", self.address_string(), fmt % args)

    def _not_found(self) -> None:
        body = b'{"error":"no mcp-proxy backend for this path"}\n'
        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] in {"/healthz", "/health"}:
            body = b'{"ok":true}\n'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._proxy(b"")

    def do_DELETE(self) -> None:
        self._proxy(self._read_body())

    def do_POST(self) -> None:
        self._proxy(self._read_body())

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return b""
        return self.rfile.read(length)

    def _proxy(self, body: bytes) -> None:
        path = self.path.split("?", 1)[0]
        found = backend_for_path(path, self.backends)
        if found is None:
            self._not_found()
            return
        backend_name, backend = found
        rpc = parse_rpc(body) if self.command == "POST" else None
        if rpc is not None:
            rpc, err = apply_rpc_request(rpc, backend, backend_name)
            if err is not None:
                encoded = json.dumps(err).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
                return
            body = json.dumps(rpc, separators=(",", ":")).encode("utf-8")

        parsed = urlparse(backend["upstream"])
        inject = resolve_headers(backend, self.credentials_dir)
        fwd: list[tuple[str, str]] = []
        for key, value in self.headers.items():
            if key.lower() in HOP_BY_HOP:
                continue
            if key.lower() in {name.lower() for name in inject}:
                continue
            fwd.append((key, value))
        for key, value in inject.items():
            fwd.append((key, value))
        if body:
            fwd.append(("Content-Length", str(len(body))))

        conn = _connect(parsed)
        try:
            target = parsed.path or "/"
            if parsed.query:
                target = f"{target}?{parsed.query}"
            conn.request(self.command, target, body=body or None, headers=dict(fwd))
            resp = conn.getresponse()
            resp_body = resp.read()
            content_type = resp.getheader("Content-Type") or ""
            if self.command == "POST" and rpc is not None:
                filtered = filter_rpc_response(resp_body, content_type, backend)
                if filtered is not None:
                    resp_body = filtered
            self.send_response(resp.status, resp.reason)
            skip = HOP_BY_HOP | {"content-length"}
            for key, value in resp.getheaders():
                if key.lower() in skip:
                    continue
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(resp_body)))
            self.end_headers()
            self.wfile.write(resp_body)
        except Exception:
            log.exception("upstream %s failed", backend.get("upstream"))
            msg = b'{"error":"mcp-proxy upstream request failed"}\n'
            try:
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(msg)))
                self.end_headers()
                self.wfile.write(msg)
            except BrokenPipeError:
                pass
        finally:
            conn.close()


def serve(listen: str, backends: dict[str, Any], credentials_dir: str | None) -> ThreadingHTTPServer:
    host, _, port_s = listen.rpartition(":")
    server = ThreadingHTTPServer((host or "127.0.0.1", int(port_s)), ProxyHandler)
    server.backends = backends
    server.credentials_dir = credentials_dir
    log.info("listen %s backends=%s", listen, ",".join(backends))
    return server
