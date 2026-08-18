#!/usr/bin/env bash
# GBrain post-install setup for a hermes-pnp host (operator / consumer deploy).
#
# Run on the device as root (or via: scp + sudo bash). Idempotent.
# Paths default to official stateDir (/var/lib/hermes); override via env.
# Declarative pieces (systemd gbrain-mcp-http, MCP url, plugins) come from
# services.hermesPnP.gbrain; this script is the remaining one-shot /
# Hermes-home state (bun CLI, PGLite init, bearer, import/embed).
#
# Two-step consumer path:
#   1. services.hermesPnP.gbrain.enable = true  (switch — unit + MCP URL + plugins)
#   2. this script (bun CLI, mint token, import/embed)
# Official hermes-agent-setup drops headers on merge; composer activation
# re-applies a literal Bearer. This script wires immediately so the first
# start after mint does not 401.
#
# Catalogue (fresh machine after remote-switch):
#   1. bun + gbrain CLI (bun global under hermes HOME)
#   2. gbrain init --pglite (if no PGLite dir)
#   3. ~/brain git tree + config (search.mode, sources path)
#   4. HTTP sole-owner unit up (gbrain-mcp-http)
#   5. Bearer token for Hermes MCP clients (auth create → token file + .env)
#   6. Wire config.yaml headers (url + Authorization)
#   7. Import ~/brain markdown if present (serve stopped)
#   8. Embed --stale with ZEROENTROPY (serve stopped)
#   9. Start HTTP serve + restart hermes-agent (+ webui if enabled)
#  10. Smoke: health, hermes mcp test / list
#
# NEVER: gbrain autopilot --install alongside serve (second PGLite writer)
# NEVER: exclusive consolidate/dream host timers
# NEVER: per-agent stdio gbrain serve as primary MCP
#
set -euo pipefail

HERMES_HOME_DIR="${HERMES_HOME_DIR:-/var/lib/hermes/home}"
HERMES_STATE="${HERMES_STATE:-/var/lib/hermes/.hermes}"
GBRAIN_HOME="${HERMES_HOME_DIR}/.gbrain"
BRAIN_REPO="${HERMES_HOME_DIR}/brain"
TOKEN_FILE="${GBRAIN_HOME}/hermes-mcp.token"
HERMES_ENV_FILE="${HERMES_STATE}/.env"
HERMES_CFG="${HERMES_STATE}/config.yaml"
BUN_BIN="${HERMES_HOME_DIR}/.bun/bin"
GBRAIN_BIN="${BUN_BIN}/gbrain"
MCP_URL="${GBRAIN_MCP_URL:-http://127.0.0.1:3131/mcp}"
SEARCH_MODE="${GBRAIN_SEARCH_MODE:-balanced}"
TOKEN_NAME="${GBRAIN_TOKEN_NAME:-hermes-agents}"

log() { echo "[gbrain-setup] $*"; }
warn() { echo "[gbrain-setup] WARN: $*" >&2; }
die() { echo "[gbrain-setup] FAIL: $*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die "run as root (sudo)"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WIRE_PY="${HERE}/gbrain-wire-config.py"

if ! systemctl cat gbrain-mcp-http.service >/dev/null 2>&1; then
  die "gbrain-mcp-http unit missing — set services.hermesPnP.gbrain.enable = true and switch first"
fi

as_hermes() {
  # shellcheck disable=SC2086
  sudo -u hermes env HOME="${HERMES_HOME_DIR}" \
    PATH="${BUN_BIN}:/run/current-system/sw/bin:/usr/bin:/bin" \
    ${ZEROENTROPY_API_KEY:+ZEROENTROPY_API_KEY="${ZEROENTROPY_API_KEY}"} \
    "$@"
}

load_ze() {
  if [[ -f /run/hermes.env ]]; then
    # shellcheck disable=SC1091
    set -a
    # shellcheck source=/dev/null
    . /run/hermes.env
    set +a
  fi
  if [[ -z "${ZEROENTROPY_API_KEY:-}" ]]; then
    warn "ZEROENTROPY_API_KEY not in /run/hermes.env — keyword search works; embed may no-op"
  else
    log "ZEROENTROPY_API_KEY present (len=${#ZEROENTROPY_API_KEY})"
  fi
}

stop_brain_consumers() {
  log "stopping hermes-webui, hermes-agent, gbrain-mcp-http"
  systemctl stop hermes-webui 2>/dev/null || true
  systemctl stop hermes-agent 2>/dev/null || true
  systemctl stop gbrain-mcp-http 2>/dev/null || true
  sleep 1
  pkill -9 -f '[g]brain' 2>/dev/null || true
  sleep 1
  pgrep -a gbrain >/dev/null 2>&1 && warn "gbrain still running" || log "no gbrain processes"
}

ensure_dirs() {
  install -d -m 0755 -o hermes -g hermes "${HERMES_HOME_DIR}"
  install -d -m 0755 -o hermes -g hermes "${GBRAIN_HOME}"
  install -d -m 0755 -o hermes -g hermes "${GBRAIN_HOME}/audit"
  install -d -m 0755 -o hermes -g hermes "${BRAIN_REPO}"
  install -d -m 0755 -o hermes -g hermes "${HERMES_STATE}"
  if [[ ! -e /home/hermes ]]; then
    ln -sfn "${HERMES_HOME_DIR}" /home/hermes
  fi
}

ensure_bun_gbrain() {
  if [[ ! -x "${GBRAIN_BIN}" ]]; then
    log "installing bun + gbrain CLI for hermes"
    if [[ ! -x "${BUN_BIN}/bun" ]]; then
      as_hermes bash -c 'curl -fsSL https://bun.sh/install | bash'
    fi
    as_hermes bash -c 'export PATH="$HOME/.bun/bin:$PATH"; bun install -g github:garrytan/gbrain'
  fi
  [[ -x "${GBRAIN_BIN}" ]] || die "gbrain binary missing at ${GBRAIN_BIN}"
  log "gbrain: $(as_hermes "${GBRAIN_BIN}" --version 2>/dev/null | head -1 || echo ok)"
}

ensure_init() {
  if [[ ! -d "${GBRAIN_HOME}/brain.pglite" ]]; then
    log "gbrain init --pglite"
    as_hermes "${GBRAIN_BIN}" init --pglite || as_hermes "${GBRAIN_BIN}" init
  else
    log "PGLite dir exists — skip init"
  fi
  if [[ ! -d "${BRAIN_REPO}/.git" ]]; then
    as_hermes git -C "${BRAIN_REPO}" init
    log "git init ${BRAIN_REPO}"
  fi
  as_hermes "${GBRAIN_BIN}" config set search.mode "${SEARCH_MODE}" 2>/dev/null || true
  # Pin markdown source path (PGLite sources table may need this later)
  as_hermes "${GBRAIN_BIN}" config set brain_repo "${BRAIN_REPO}" 2>/dev/null || true
  as_hermes "${GBRAIN_BIN}" config set repo "${BRAIN_REPO}" 2>/dev/null || true
}

# Write token to hermes-owned env surfaces (no sops). Never use ${VAR} in yaml.
persist_token() {
  local tok="$1"
  [[ -n "${tok}" ]] || return 1
  [[ "${tok}" != *'${'* ]] || die "refusing token that looks like an unexpanded \${placeholder}"
  umask 077
  printf '%s\n' "${tok}" >"${TOKEN_FILE}"
  chown hermes:hermes "${TOKEN_FILE}"
  chmod 600 "${TOKEN_FILE}"
  touch "${HERMES_ENV_FILE}"
  chown hermes:hermes "${HERMES_ENV_FILE}"
  chmod 600 "${HERMES_ENV_FILE}"
  if grep -q '^GBRAIN_REMOTE_TOKEN=' "${HERMES_ENV_FILE}" 2>/dev/null; then
    # Avoid sed & in token; rewrite via python
    python3 - "${HERMES_ENV_FILE}" "${tok}" <<'PY'
from pathlib import Path
import sys
path, tok = Path(sys.argv[1]), sys.argv[2]
lines = path.read_text().splitlines() if path.exists() else []
out, seen = [], False
for line in lines:
    if line.startswith("GBRAIN_REMOTE_TOKEN="):
        out.append(f"GBRAIN_REMOTE_TOKEN={tok}")
        seen = True
    else:
        out.append(line)
if not seen:
    out.append(f"GBRAIN_REMOTE_TOKEN={tok}")
path.write_text("\n".join(out) + "\n")
PY
  else
    printf '\nGBRAIN_REMOTE_TOKEN=%s\n' "${tok}" >>"${HERMES_ENV_FILE}"
  fi
  chown hermes:hermes "${HERMES_ENV_FILE}"
  log "persisted token → ${TOKEN_FILE} + GBRAIN_REMOTE_TOKEN in ${HERMES_ENV_FILE} (do not print)"
}

token_works() {
  local tok="$1"
  [[ -n "${tok}" ]] || return 1
  curl -sS -m 5 -o /dev/null -w "%{http_code}" \
    -X POST "${MCP_URL}" \
    -H "Authorization: Bearer ${tok}" \
    -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"gbrain-setup","version":"0"}}}' \
    | grep -qE '200|201'
}

mint_token() {
  local tok=""
  if [[ -s "${TOKEN_FILE}" ]]; then
    tok=$(tr -d '\r\n' <"${TOKEN_FILE}")
    if token_works "${tok}"; then
      log "existing token works — refresh env + config only"
      persist_token "${tok}"
      return 0
    fi
    warn "existing token file failed HTTP probe — re-mint"
  fi
  log "minting HTTP MCP token (${TOKEN_NAME})"
  systemctl start gbrain-mcp-http 2>/dev/null || true
  sleep 2
  out=$(as_hermes "${GBRAIN_BIN}" auth create "${TOKEN_NAME}" 2>&1 || true)
  echo "${out}" | tail -15
  # Live tokens look like gbrain_… (and older gb_…)
  tok=$(echo "${out}" | grep -Eo 'gbrain_[A-Za-z0-9_-]+|gb_[A-Za-z0-9_-]+' | tail -1 || true)
  if [[ -z "${tok}" ]]; then
    out=$(as_hermes "${GBRAIN_BIN}" auth create "${TOKEN_NAME}" --json 2>&1 || true)
    tok=$(echo "${out}" | grep -Eo '"token"[[:space:]]*:[[:space:]]*"[^"]+"' | head -1 | sed 's/.*"\([^"]*\)"$/\1/' || true)
  fi
  if [[ -z "${tok}" ]]; then
    warn "could not parse token from auth create — Hermes/operator must mint:"
    warn "  systemctl start gbrain-mcp-http"
    warn "  sudo -u hermes env HOME=${HERMES_HOME_DIR} PATH=${BUN_BIN}:\$PATH gbrain auth create ${TOKEN_NAME}"
    warn "  write plaintext token to ${TOKEN_FILE} (600) AND GBRAIN_REMOTE_TOKEN=… in ${HERMES_ENV_FILE}"
    warn "  config.yaml headers.Authorization must be 'Bearer <literal token>' — NEVER 'Bearer \${GBRAIN_REMOTE_TOKEN}'"
    return 1
  fi
  persist_token "${tok}"
  if token_works "${tok}"; then
    log "token HTTP probe OK"
  else
    warn "token saved but HTTP probe failed — check gbrain-mcp-http"
  fi
}

wire_hermes_mcp_config() {
  [[ -f "${HERMES_CFG}" ]] || {
    warn "no ${HERMES_CFG} yet — start hermes once, re-run setup"
    return 0
  }
  local token=""
  if [[ -s "${TOKEN_FILE}" ]]; then
    token=$(tr -d '\r\n' <"${TOKEN_FILE}")
  elif grep -q '^GBRAIN_REMOTE_TOKEN=' "${HERMES_ENV_FILE}" 2>/dev/null; then
    token=$(grep '^GBRAIN_REMOTE_TOKEN=' "${HERMES_ENV_FILE}" | head -1 | cut -d= -f2- | tr -d '\r\n' | tr -d '"' | tr -d "'")
  fi
  # Reject unexpanded placeholders (common footgun)
  if [[ "${token}" == *'${'* ]] || [[ "${token}" == 'GBRAIN_REMOTE_TOKEN' ]]; then
    warn "refusing placeholder token in env — re-run mint"
    token=""
  fi
  local py=python3
  command -v python3 >/dev/null 2>&1 || py=/var/lib/hermes/toolbox/bin/python3
  if [[ -f "${WIRE_PY}" ]]; then
    "${py}" "${WIRE_PY}" "${HERMES_CFG}" "${MCP_URL}" "${token}" || warn "wire-config exited non-zero"
  else
    warn "gbrain-wire-config.py not next to this script — skip yaml patch (composer activation will apply)"
  fi
  [[ -f "${HERMES_CFG}" ]] && chown hermes:hermes "${HERMES_CFG}"
}

import_and_embed() {
  stop_brain_consumers
  local n
  n=$(find "${BRAIN_REPO}" -type f -name '*.md' 2>/dev/null | wc -l | tr -d ' ')
  if [[ "${n}" -gt 0 ]]; then
    log "import ${n} markdown files from ${BRAIN_REPO}"
    as_hermes "${GBRAIN_BIN}" import "${BRAIN_REPO}" --no-embed || warn "import had errors"
  else
    log "no markdown under ${BRAIN_REPO} — skip import"
  fi
  if [[ -n "${ZEROENTROPY_API_KEY:-}" ]]; then
    log "gbrain embed --stale"
    as_hermes "${GBRAIN_BIN}" embed --stale || warn "embed had errors"
  else
    warn "skip embed (no ZEROENTROPY_API_KEY)"
  fi
}

harden_brain_durability() {
  # Official CLI: put_page write-through only commits if sources harden ran.
  # Must run with cwd=$HOME (Nixpkgs bun cannot posix_spawn from a foreign cwd)
  # and with serve down (PGLite single-writer).
  local pat_file="${GBRAIN_HOME}/github.pat"
  [[ -d "${BRAIN_REPO}/.git" ]] || {
    warn "no ${BRAIN_REPO}/.git — skip sources harden"
    return 0
  }
  install -d -m 0700 -o hermes -g hermes "${GBRAIN_HOME}"
  if [[ ! -s "${pat_file}" && -r /run/hermes.env ]]; then
    grep '^GITHUB_PAT=' /run/hermes.env | tail -1 | cut -d= -f2- | tr -d '\r\n"' \
      | sudo tee "${pat_file}" >/dev/null
    chown hermes:hermes "${pat_file}"
    chmod 600 "${pat_file}"
  fi
  [[ -s "${pat_file}" ]] || {
    warn "no PAT at ${pat_file} — skip sources harden"
    return 0
  }
  systemctl stop gbrain-mcp-http 2>/dev/null || true
  log "gbrain sources harden default (cwd=HOME, --no-cron)"
  as_hermes bash -lc "cd \"\$HOME\" && gbrain sources harden default --pat-file '${pat_file}' --no-cron" \
    || warn "sources harden exited non-zero (check hook + credential.helper)"
}

start_stack() {
  log "starting gbrain-mcp-http (sole PGLite owner)"
  systemctl reset-failed gbrain-mcp-http 2>/dev/null || true
  systemctl start gbrain-mcp-http
  sleep 3
  systemctl is-active --quiet gbrain-mcp-http || die "gbrain-mcp-http failed — journalctl -u gbrain-mcp-http"
  if ss -ltn 2>/dev/null | grep -q ':3131'; then
    log "listening on 127.0.0.1:3131"
  else
    warn "nothing on :3131 yet"
  fi
  curl -sS -m 3 http://127.0.0.1:3131/health && echo || warn "health check failed"
  log "starting hermes-agent"
  systemctl start hermes-agent
  systemctl start hermes-webui 2>/dev/null || true
  sleep 2
  systemctl is-active hermes-agent && log "hermes-agent active"
}

smoke() {
  log "smoke"
  systemctl is-active gbrain-mcp-http hermes-agent || true
  if command -v hermes >/dev/null 2>&1 || [[ -x /run/current-system/sw/bin/hermes ]]; then
    sudo -u hermes env HOME="${HERMES_HOME_DIR}" HERMES_HOME="${HERMES_STATE}" \
      PATH="/var/lib/hermes/bin:/run/current-system/sw/bin:${BUN_BIN}" \
      hermes mcp test gbrain 2>&1 | tail -20 || warn "hermes mcp test failed (session/auth?)"
  fi
  log "done — stock gbrain + HTTP sole-owner + two plugins (retrieval-reflex, memory-flush)"
  log "do NOT install gbrain autopilot daemon on this host"
}

main() {
  load_ze
  ensure_dirs
  ensure_bun_gbrain
  stop_brain_consumers
  ensure_init
  # Token mint may need HTTP serve briefly
  systemctl start gbrain-mcp-http 2>/dev/null || true
  sleep 2
  mint_token || true
  wire_hermes_mcp_config
  import_and_embed
  harden_brain_durability
  start_stack
  # Re-wire after start in case setup rewrote config
  wire_hermes_mcp_config
  systemctl restart hermes-agent 2>/dev/null || true
  systemctl restart hermes-webui 2>/dev/null || true
  smoke
}

main "$@"
