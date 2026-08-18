#!/usr/bin/env python3
"""Patch Hermes config.yaml with HTTP GBrain MCP + literal Bearer.

Official hermes-agent-setup merges Nix mcpServers into config.yaml and
Nix keys win, so headers are dropped on every activation. Re-apply a
literal Authorization after that merge. Hermes does not expand
${GBRAIN_REMOTE_TOKEN} in yaml.

Usage: gbrain-wire-config.py CONFIG_YAML MCP_URL [TOKEN]
Exit 0 if config is missing or PyYAML is absent (nothing to do yet).
"""
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("gbrain-wire-config: PyYAML missing — skip")
    sys.exit(0)

if len(sys.argv) < 3:
    print("usage: gbrain-wire-config.py CONFIG_YAML MCP_URL [TOKEN]", file=sys.stderr)
    sys.exit(2)

path = Path(sys.argv[1])
url = sys.argv[2]
token = sys.argv[3] if len(sys.argv) > 3 else ""

if not path.is_file():
    print(f"gbrain-wire-config: no {path} yet — start hermes once")
    sys.exit(0)

data = yaml.safe_load(path.read_text()) or {}
mcp = data.setdefault("mcp_servers", {})
cur = mcp.get("gbrain") or {}
desired = {
    "url": url,
    "connect_timeout": 120,
    "timeout": 120,
    "enabled": True,
}

placeholder = "${"
if token and placeholder not in token:
    desired["headers"] = {"Authorization": "Bearer " + token}
else:
    headers = cur.get("headers") if isinstance(cur.get("headers"), dict) else {}
    auth = str(headers.get("Authorization") or headers.get("authorization") or "")
    if auth.startswith("Bearer ") and placeholder not in auth:
        desired["headers"] = {"Authorization": auth}
    else:
        print("gbrain-wire-config: no usable literal Bearer — MCP will 401 until mint")

if mcp.get("gbrain") != desired:
    mcp["gbrain"] = desired
    path.write_text(yaml.safe_dump(data, sort_keys=False, default_flow_style=False))
    print("gbrain-wire-config: updated HTTP + literal Bearer")
else:
    print("gbrain-wire-config: already correct")
