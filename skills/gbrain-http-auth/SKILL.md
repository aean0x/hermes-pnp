---
name: gbrain-http-auth
description: "Wire GBrain HTTP MCP bearer — GBRAIN_TOKEN in .env + Bearer ${GBRAIN_TOKEN} env-ref in config.yaml (no sops)."
version: 1.1.0
author: Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [gbrain, auth, hermes-pnp]
---

# GBrain HTTP MCP auth

Use when MCP gbrain returns **401**, tools missing after restart, or fresh bootstrap after HTTP serve is up.

The composer (`services.hermesPnP.gbrain.enable`) starts `gbrain serve
--http`, sets the MCP URL + `headers.Authorization: Bearer
${GBRAIN_TOKEN}` (env-ref), and installs the two plugins. This skill is
the mint / repair procedure when `gbrain-setup` has not run or MCP 401s.

## Architecture

- Sole serve: consumer unit (commonly `gbrain-mcp-http`) → `http://127.0.0.1:3131/mcp`
- Auth: **Bearer access token** from `gbrain auth create` (not sops)
- Hermes expands `${GBRAIN_TOKEN}` in `mcpServers.gbrain.headers.Authorization`
  from `$HERMES_HOME/.env` at runtime (same mechanism as `${MCP_PROXY_TOKEN}`).

## Iron rules

1. `config.yaml` keeps the env-ref, never a literal token:
   `headers.Authorization: "Bearer ${GBRAIN_TOKEN}"`.
   The token value lives in `$HERMES_HOME/.env` as `GBRAIN_TOKEN=…`.
2. Keep the same secret in **two Hermes-owned places** (operator state, not Nix):
   - `~/.gbrain/hermes-mcp.token` (mode 600) — durable source + plugin ambient HTTP fallback
   - `~/.hermes/.env` → `GBRAIN_TOKEN=…` — what Hermes expands
3. Do **not** print the token in chat/logs.
4. Do **not** install `gbrain autopilot` or a second `gbrain serve`.

## Mint / repair (as hermes, HTTP unit running)

```bash
# 1) Serve must be up
systemctl is-active gbrain-mcp-http   # or ask operator

# 2) Mint (skip if token file already works)
gbrain auth create hermes
# copy the shown token once (looks like gbrain_…)

# 3) Persist (do not echo token to transcripts)
install -m 600 /dev/null ~/.gbrain/hermes-mcp.token
# write token into that file and into .env:
#   GBRAIN_TOKEN=<token>
```

Prefer `scripts/gbrain-setup.sh` (consumer `./deploy gbrain-setup`) after
`gbrain.enable` is switched. That mints, writes the token file +
`GBRAIN_TOKEN`, and probes HTTP.

## Verify (no secret dump)

```bash
curl -sS http://127.0.0.1:3131/health
# config must contain the env-ref, not a literal token
grep -A6 'gbrain:' ~/.hermes/config.yaml | grep Authorization
hermes mcp test gbrain   # or tool_search gbrain
```

Expect: tools list includes `get_page` / `put_page` / `query` / `volunteer_context`.

## If 401 again

1. Token file empty/missing or `GBRAIN_TOKEN` missing from `.env` → re-run mint (this skill).
2. New session after restart (old WebUI chat may lag).
3. Exactly one `gbrain serve --http` on :3131.

## Ambient reflex

Plugin `gbrain-retrieval-reflex` reads `GBRAIN_TOKEN` from the process env
(populated from `.env`) and falls back to `~/.gbrain/hermes-mcp.token`.
Keep both in sync when you rotate.
