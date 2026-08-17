---
name: gbrain-http-auth
description: "Wire GBrain HTTP MCP bearer — token file + GBRAIN_REMOTE_TOKEN + literal Bearer in config.yaml (no sops)."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [gbrain, auth, hermes-pnp]
---

# GBrain HTTP MCP auth

Use when MCP gbrain returns **401**, tools missing after restart, or fresh bootstrap after HTTP serve is up.

The composer (`services.hermesPnP.gbrain`) only sets the MCP URL and
`GBRAIN_TOKEN_FILE`. The consumer starts `gbrain serve --http`. This
skill is the operator/agent procedure for minting and wiring the Bearer.

## Architecture

- Sole serve: consumer unit (commonly `gbrain-mcp-http`) → `http://127.0.0.1:3131/mcp`
- Auth: **Bearer access token** from `gbrain auth create` (not sops)
- Hermes does **not** expand `${GBRAIN_REMOTE_TOKEN}` inside `config.yaml`

## Iron rules

1. **`config.yaml` must contain the literal token:**
   `headers.Authorization: "Bearer gbrain_…"`
   **Never** `Bearer ${GBRAIN_REMOTE_TOKEN}` (that string is sent as-is → 401).
2. Keep the same secret in **two Hermes-owned places** (operator state, not Nix):
   - `~/.gbrain/hermes-mcp.token` (mode 600) — plugin ambient HTTP reads this
   - `~/.hermes/.env` → `GBRAIN_REMOTE_TOKEN=…` — human/ops mirror
3. Do **not** print the token in chat/logs.
4. Do **not** install `gbrain autopilot` or a second `gbrain serve`.

## Mint / repair (as hermes, HTTP unit running)

```bash
# 1) Serve must be up
systemctl is-active gbrain-mcp-http   # or ask operator

# 2) Mint (skip if token file already works)
gbrain auth create hermes-agents
# copy the shown token once (looks like gbrain_…)

# 3) Persist (do not echo token to transcripts)
install -m 600 /dev/null ~/.gbrain/hermes-mcp.token
# write token only into that file and into .env:
#   GBRAIN_REMOTE_TOKEN=<token>

# 4) Wire config.yaml with LITERAL bearer (python/yaml preferred)
# mcp_servers.gbrain:
#   url: http://127.0.0.1:3131/mcp
#   headers:
#     Authorization: Bearer <token>
#   enabled: true
# remove command/args/env/auth on gbrain entry

# 5) Restart agent processes so they reload config+env
#    (operator if no rights): systemctl restart hermes-agent hermes-webui
```

The consumer flake may ship a one-shot that does this (mint, write token
file + `GBRAIN_REMOTE_TOKEN`, patch `config.yaml` with a literal Bearer,
probe HTTP). Prefer that when present.

## Verify (no secret dump)

```bash
curl -sS http://127.0.0.1:3131/health
# config must not contain the characters $ { after Bearer
grep -A6 'gbrain:' ~/.hermes/config.yaml | grep Authorization
hermes mcp test gbrain   # or tool_search gbrain
```

Expect: tools list includes `get_page` / `put_page` / `query` / `volunteer_context`.

## If 401 again

1. Token file empty/missing or only `${…}` in yaml → re-run mint + wire (this skill).
2. New session after restart (old WebUI chat may lag).
3. Exactly one `gbrain serve --http` on :3131.

## Ambient reflex

Plugin `gbrain-retrieval-reflex` calls HTTP `volunteer_context` then `query` using the **token file** (not yaml expansion). Keep `hermes-mcp.token` in sync when you rotate.
