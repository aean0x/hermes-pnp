#!/usr/bin/env python3
"""Import cookies into the sticky Hermes CDP browser (Network.setCookie).

Accepts:
  - Netscape cookies.txt (tab-separated)
  - Playwright / Chrome DevTools JSON list
  - Single cookie JSON object

Usage:
  hermes-browser-import-cookies /path/to/cookies.txt
  hermes-browser-import-cookies /path/to/cookies.json
  hermes-browser-import-cookies --cdp http://127.0.0.1:9222 cookies.json

Browser must be running with remote debugging (hermes-browser.service).
Keeps the session warm — does not restart Brave.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def die(msg: str, code: int = 1) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def _http_json(url: str) -> Any:
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        die(f"CDP HTTP failed at {url}: {exc}")


def fetch_page_ws_url(cdp_base: str) -> str:
    """Network.setCookie is a page/target method, not browser-level CDP.

    Prefer an existing page target; fall back to creating about:blank via
    the browser websocket if none exist.
    """
    base = cdp_base.rstrip("/")
    # Confirm CDP is up
    _http_json(f"{base}/json/version")

    targets = _http_json(f"{base}/json/list")
    if isinstance(targets, list):
        pages = [
            t
            for t in targets
            if isinstance(t, dict)
            and t.get("type") == "page"
            and t.get("webSocketDebuggerUrl")
        ]
        # Prefer non-devtools pages
        pages.sort(key=lambda t: 0 if "devtools" not in str(t.get("url", "")) else 1)
        if pages:
            return str(pages[0]["webSocketDebuggerUrl"])

    # Create a blank page via PUT /json/new (Chromium/Brave HTTP API)
    new_url = f"{base}/json/new?about:blank"
    try:
        req = urllib.request.Request(new_url, method="PUT")
        with urllib.request.urlopen(req, timeout=10) as resp:
            created = json.loads(resp.read().decode())
        ws = created.get("webSocketDebuggerUrl")
        if ws:
            return str(ws)
    except Exception:
        pass

    # Last resort: browser-level WS + Target.createTarget (needs websocket)
    try:
        import websocket  # type: ignore[import-untyped]
    except ImportError:
        die("no page target and cannot create one (websocket-client missing)")

    version = _http_json(f"{base}/json/version")
    browser_ws = version.get("webSocketDebuggerUrl")
    if not browser_ws:
        die("no browser webSocketDebuggerUrl")
    ws = websocket.create_connection(str(browser_ws), timeout=15)
    try:
        msg_id = 1
        ws.send(
            json.dumps(
                {
                    "id": msg_id,
                    "method": "Target.createTarget",
                    "params": {"url": "about:blank"},
                }
            )
        )
        deadline = time.time() + 15
        target_id = None
        while time.time() < deadline:
            data = json.loads(ws.recv())
            if data.get("id") == msg_id:
                if data.get("error"):
                    die(f"Target.createTarget failed: {data['error']}")
                target_id = data.get("result", {}).get("targetId")
                break
        if not target_id:
            die("Target.createTarget timed out")
    finally:
        ws.close()

    # Re-list for the new page websocket
    time.sleep(0.3)
    targets = _http_json(f"{base}/json/list")
    for t in targets if isinstance(targets, list) else []:
        if isinstance(t, dict) and t.get("id") == target_id and t.get("webSocketDebuggerUrl"):
            return str(t["webSocketDebuggerUrl"])
    for t in targets if isinstance(targets, list) else []:
        if isinstance(t, dict) and t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
            return str(t["webSocketDebuggerUrl"])
    die("created target but no page websocket found")


def parse_netscape(text: str) -> list[dict[str, Any]]:
    cookies: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            # Netscape HttpOnly extension: #HttpOnly_.domain.com ...
            if line.startswith("#HttpOnly_"):
                line = line[len("#HttpOnly_") :]
                http_only = True
            else:
                continue
        else:
            http_only = False
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, _include, path, secure, expires, name, value = parts[:7]
        if domain.startswith("#HttpOnly_"):
            domain = domain[len("#HttpOnly_") :]
            http_only = True
        try:
            exp = float(expires)
        except ValueError:
            exp = -1.0
        cookie: dict[str, Any] = {
            "name": name,
            "value": value,
            "domain": domain,
            "path": path or "/",
            "secure": secure.upper() == "TRUE",
            "httpOnly": http_only,
        }
        if exp > 0:
            cookie["expires"] = exp
        cookies.append(cookie)
    return cookies


def parse_json_cookies(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        # Playwright storage state: { cookies: [...], origins: [...] }
        if "cookies" in raw and isinstance(raw["cookies"], list):
            raw = raw["cookies"]
        else:
            raw = [raw]
    if not isinstance(raw, list):
        die("JSON cookies must be a list, a single object, or {cookies: [...]}")
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        value = item.get("value")
        if name is None or value is None:
            continue
        cookie: dict[str, Any] = {
            "name": str(name),
            "value": str(value),
            "domain": str(item.get("domain") or item.get("url") or ""),
            "path": str(item.get("path") or "/"),
            "secure": bool(item.get("secure", False)),
            "httpOnly": bool(item.get("httpOnly", item.get("http_only", False))),
        }
        # domain may be empty if only url provided
        if not cookie["domain"] and item.get("url"):
            # leave domain empty; CDP accepts url instead
            cookie.pop("domain", None)
            cookie["url"] = str(item["url"])
        expires = item.get("expires", item.get("expirationDate", item.get("expiry")))
        if expires is not None:
            try:
                exp_f = float(expires)
                # Playwright uses -1 for session cookies
                if exp_f > 0:
                    cookie["expires"] = exp_f
            except (TypeError, ValueError):
                pass
        same = item.get("sameSite") or item.get("same_site")
        if same:
            # Normalize to CDP enum: Strict | Lax | None
            s = str(same).strip().lower()
            if s in ("strict", "lax", "none"):
                cookie["sameSite"] = s.capitalize() if s != "none" else "None"
            elif s in ("no_restriction", "unspecified"):
                cookie["sameSite"] = "None" if s == "no_restriction" else "Lax"
        out.append(cookie)
    return out


def load_cookies(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    stripped = text.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            die(f"invalid JSON in {path}: {exc}")
        return parse_json_cookies(raw)
    return parse_netscape(text)


def cdp_set_cookies(ws_url: str, cookies: list[dict[str, Any]], dry_run: bool) -> tuple[int, int]:
    try:
        import websocket  # type: ignore[import-untyped]
    except ImportError:
        die("python websocket-client missing (nix package python3Packages.websocket-client)")

    ok = 0
    fail = 0
    if dry_run:
        for c in cookies:
            print(f"dry-run would set: {c.get('domain', c.get('url', '?'))} {c.get('name')}")
            ok += 1
        return ok, fail

    ws = websocket.create_connection(ws_url, timeout=15)
    msg_id = 0

    def call(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        nonlocal msg_id
        msg_id += 1
        payload = {"id": msg_id, "method": method, "params": params or {}}
        ws.send(json.dumps(payload))
        # Read until matching id (skip events)
        deadline = time.time() + 20
        while time.time() < deadline:
            raw = ws.recv()
            data = json.loads(raw)
            if data.get("id") == msg_id:
                return data
        die(f"timeout waiting for CDP response to {method}")

    try:
        # Enable Network domain (harmless if already on)
        call("Network.enable")
        for cookie in cookies:
            params = dict(cookie)
            # CDP wants domain without leading dot for host-only, but accepts .domain
            result = call("Network.setCookie", params)
            if result.get("error"):
                print(
                    f"FAIL {params.get('domain', params.get('url'))} {params.get('name')}: "
                    f"{result['error']}",
                    file=sys.stderr,
                )
                fail += 1
            elif result.get("result", {}).get("success") is False:
                print(
                    f"FAIL {params.get('domain', params.get('url'))} {params.get('name')}: success=false",
                    file=sys.stderr,
                )
                fail += 1
            else:
                print(f"OK   {params.get('domain', params.get('url'))} {params.get('name')}")
                ok += 1
    finally:
        ws.close()
    return ok, fail


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "files",
        nargs="+",
        type=Path,
        help="Netscape .txt or Playwright/JSON cookie files",
    )
    parser.add_argument(
        "--cdp",
        default="http://127.0.0.1:9222",
        help="CDP HTTP base (default: http://127.0.0.1:9222)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and list only; do not call CDP",
    )
    args = parser.parse_args()

    all_cookies: list[dict[str, Any]] = []
    for path in args.files:
        if not path.is_file():
            die(f"not a file: {path}")
        loaded = load_cookies(path)
        print(f"loaded {len(loaded)} cookies from {path}")
        all_cookies.extend(loaded)

    if not all_cookies:
        die("no cookies parsed")

    # Dedupe by (domain|url, name, path) last wins
    dedup: dict[tuple[str, str, str], dict[str, Any]] = {}
    for c in all_cookies:
        key = (
            str(c.get("domain") or c.get("url") or ""),
            str(c.get("name")),
            str(c.get("path") or "/"),
        )
        dedup[key] = c
    cookies = list(dedup.values())
    print(f"unique cookies: {len(cookies)}")

    ws_url = "dry-run" if args.dry_run else fetch_page_ws_url(args.cdp)
    if not args.dry_run:
        print(f"cdp page websocket: {ws_url}")
    ok, fail = cdp_set_cookies(ws_url, cookies, args.dry_run)
    print(f"done: ok={ok} fail={fail}")
    if fail and not ok:
        sys.exit(2)
    if fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
