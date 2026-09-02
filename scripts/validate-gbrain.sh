#!/usr/bin/env bash
# Live GBrain checks for a hermes-pnp host (run as root on the device).
# MCP + reflex only — no exclusive CLI / host dream timers.
set -euo pipefail

fail=0
ok() { echo "OK  $*"; }
bad() { echo "FAIL $*"; fail=1; }
warn() { echo "WARN $*"; }

STATE="${HERMES_STATE:-/var/lib/hermes}"
HOME_DIR="${HERMES_HOME_DIR:-${STATE}/home}"
HERMES_HOME="${STATE}/.hermes"
HERMES_AGENTS="${HERMES_HOME}/AGENTS.md"
CONTAINER="${HERMES_CONTAINER:-hermes-agent}"

echo "=== 1. live AGENTS.md (must not be a Nix manifesto) ==="
if [ -e "$HERMES_AGENTS" ] && grep -qE "look here first|Memory contract \(operator" "$HERMES_AGENTS"; then
  bad "live $HERMES_AGENTS still has Nix-injected manifesto (should be purged)"
elif [ -e "$HERMES_AGENTS" ]; then
  ok "live AGENTS.md present (Hermes/GBrain owned, not Nix manifesto)"
else
  ok "live HERMES_HOME/AGENTS.md absent (not Nix-injected)"
fi

echo "=== 2. Legacy exclusive surface must be gone ==="
for u in hermes-gbrain-consolidate.timer gbrain-dream.timer gbrain-embed.timer gbrain-nightly.timer; do
  if systemctl is-enabled "$u" >/dev/null 2>&1; then
    bad "$u still enabled (should be purged)"
  else
    ok "$u not enabled"
  fi
done
for bin in hermes-gbrain-exclusive hermes-gbrain-consolidate hermes-gbrain-nightly hermes-gbrain-dream hermes-gbrain-embed; do
  if command -v "$bin" >/dev/null 2>&1 || [ -x "/run/current-system/sw/bin/$bin" ]; then
    bad "$bin still on PATH (exclusive CLI purged)"
  else
    ok "$bin not on PATH"
  fi
done
for f in "${STATE}/bin/gbrain-exclusive-cli" \
  "${STATE}/bin/hermes-gbrain-consolidate" \
  "${STATE}/bin/hermes-gbrain-dream" \
  "${STATE}/bin/hermes-gbrain-embed"; do
  if [ -e "$f" ]; then
    bad "agent-visible $f still present"
  else
    ok "purged $f"
  fi
done

echo "=== 3. gbrain-mcp-http (sole owner) + hermes-agent ==="
if systemctl is-active --quiet gbrain-mcp-http.service 2>/dev/null; then
  ok "gbrain-mcp-http active"
else
  bad "gbrain-mcp-http not active (sole PGLite owner)"
fi
if systemctl is-active --quiet hermes-agent.service 2>/dev/null; then
  ok "hermes-agent active"
else
  bad "hermes-agent not active"
fi
nserve=$(pgrep -fc 'gbrain serve' 2>/dev/null || echo 0)
if [ "${nserve:-0}" -eq 1 ]; then
  ok "exactly one gbrain serve process"
elif [ "${nserve:-0}" -eq 0 ]; then
  bad "no gbrain serve process"
else
  warn "multiple gbrain serve processes ($nserve) — dual-writer risk; pkill orphans"
fi
if ss -ltn 2>/dev/null | grep -q ':3131'; then
  ok "gbrain HTTP listening :3131"
else
  warn "nothing listening on :3131"
fi

if [ -x "${HOME_DIR}/.bun/bin/gbrain" ] \
  || docker exec "$CONTAINER" test -x /home/hermes/.bun/bin/gbrain 2>/dev/null; then
  ok "gbrain CLI present (bun global)"
else
  warn "gbrain not installed (bootstrap: bun install -g github:garrytan/gbrain)"
fi

if [ -e "${STATE}/workspace/gbrain-pointer-index.json" ] \
  || [ -d "${STATE}/plugins/gbrain-reflex" ] \
  || [ -d "${HERMES_HOME}/plugins/gbrain-reflex" ]; then
  bad "static pointer index / gbrain-reflex still present (should be purged)"
else
  ok "static pointer workaround purged on live"
fi

for plug in gbrain-retrieval-reflex gbrain-memory-flush; do
  if [ -f "${HERMES_HOME}/plugins/${plug}/plugin.yaml" ] \
    || docker exec "$CONTAINER" test -f "/data/.hermes/plugins/${plug}/plugin.yaml" 2>/dev/null; then
    ok "plugin $plug installed under \$HERMES_HOME/plugins"
  else
    warn "plugin $plug not found under \$HERMES_HOME/plugins"
  fi
done

sock=""
for cand in \
  "${HOME_DIR}/.gbrain/brain.pglite/.gbrain-resolve.sock" \
  /home/hermes/.gbrain/brain.pglite/.gbrain-resolve.sock; do
  if [ -e "$cand" ]; then sock="$cand"; break; fi
done
if [ -n "$sock" ]; then
  ok "gbrain resolve IPC socket present ($sock)"
else
  warn "resolve IPC socket missing (serve not up, old gbrain, or non-PGLite path)"
fi

echo "=== 4. config.yaml HTTP + Bearer env-ref ==="
CFG="${HERMES_HOME}/config.yaml"
if [ -f "$CFG" ]; then
  block=$(grep -A12 -E '^[[:space:]]*gbrain:' "$CFG" || true)
  if echo "$block" | grep -qF '${GBRAIN_TOKEN}'; then
    ok "config.yaml gbrain Authorization is Bearer \${GBRAIN_TOKEN} (env-ref)"
  elif echo "$block" | grep -qE 'Authorization:[[:space:]]*[Bb]earer[[:space:]]+gbrain_'; then
    bad "config.yaml gbrain Authorization is a literal token (must be \${GBRAIN_TOKEN} env-ref)"
  else
    warn "config.yaml gbrain has no Authorization — run gbrain-setup"
  fi
  if echo "$block" | grep -qF 'url: http://127.0.0.1:3131/mcp'; then
    ok "config.yaml gbrain url is loopback HTTP"
  else
    warn "config.yaml gbrain url is not http://127.0.0.1:3131/mcp"
  fi
else
  warn "no $CFG yet"
fi

echo "=== 5. MCP list (gbrain expected) ==="
if command -v hermes >/dev/null 2>&1 || [ -x /run/current-system/sw/bin/hermes ]; then
  HERMES_BIN=$(command -v hermes 2>/dev/null || echo /run/current-system/sw/bin/hermes)
  if sudo -u hermes "$HERMES_BIN" mcp list 2>/dev/null | tee /tmp/hermes-mcp-list.txt | grep -qi gbrain; then
    ok "hermes mcp list includes gbrain"
  else
    warn "hermes mcp list missing gbrain (agent/OAuth/bootstrap?)"
    tail -20 /tmp/hermes-mcp-list.txt 2>/dev/null || true
  fi
else
  warn "hermes CLI not on host PATH"
fi

echo "=== 6. autopilot + jobs worker absent ==="
for u in gbrain-autopilot gbrain-jobs gbrain-jobs-work; do
  if systemctl is-enabled "$u" >/dev/null 2>&1 || systemctl is-active "$u" >/dev/null 2>&1; then
    bad "$u present (must be absent next to serve on PGLite)"
  else
    ok "$u absent"
  fi
done

echo "=== summary ==="
if [ "$fail" -eq 0 ]; then
  echo "PASS validate-gbrain (MCP + reflex)"
  exit 0
else
  echo "FAIL validate-gbrain ($fail checks)"
  exit 1
fi
