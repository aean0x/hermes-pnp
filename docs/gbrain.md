# GBrain (operator)

Off by default. `services.hermesPnP.gbrain.enable = true` starts loopback
`gbrain serve` (`gbrain-mcp-http`), sets `mcpServers.gbrain.url`, and
installs `gbrain-retrieval-reflex` + `gbrain-memory-flush`.

The CLI is a consumer bootstrap (`bun install -g`). Nix does not ship
PGLite, sources, or a memory registry.

**Setup:** `scripts/gbrain-setup.sh` (root on the device; consumer
`./deploy gbrain-setup` copies it from this flake).  
**Validate:** `scripts/validate-gbrain.sh`.  
**Agent bootstrap prompt:** `scripts/gbrain-bootstrap-query.txt`.

## Rules

- **MCP via shared HTTP** to `http://127.0.0.1:3131/mcp` (put_page / query / get_page / volunteer_context).
- **Never** spawn a second `gbrain serve` (PGLite single-writer).
- Maintenance is MCP tools on the live serve, not exclusive CLI.

## Day path

| Surface | Role |
|---------|------|
| systemd `gbrain-mcp-http` | Sole PGLite owner: `gbrain serve --http --bind 127.0.0.1 --port 3131` |
| Hermes MCP `gbrain` | url + **literal** Bearer (gateway + WebUI + CLI) |
| Auth token (not sops) | `gbrain auth create hermes-agents` → `~/.gbrain/hermes-mcp.token`, `GBRAIN_REMOTE_TOKEN` in `~/.hermes/.env`, `config.yaml` `Authorization: Bearer <token>`. Never `Bearer ${GBRAIN_REMOTE_TOKEN}`. |
| plugin `gbrain-retrieval-reflex` | resolve IPC if sock present; else nudge `volunteer_context` |

## Ops

```bash
sudo systemctl restart gbrain-mcp-http hermes-agent
systemctl is-active gbrain-mcp-http; ss -ltn | grep 3131; pgrep -a gbrain

# Orphans / crash-loop (PGLite lock or WASM Aborted):
sudo systemctl stop hermes-webui hermes-agent gbrain-mcp-http
sudo pkill -9 -f gbrain || true
sudo rm -rf /var/lib/hermes/home/.gbrain/.locks /var/lib/hermes/home/.gbrain/brain.pglite/.gbrain-lock
sudo systemctl reset-failed gbrain-mcp-http
sudo systemctl start gbrain-mcp-http
```

Maintenance is MCP ops on the live serve, or Hermes cron **via MCP tools only**.
Never `gbrain autopilot --install` alongside serve.
