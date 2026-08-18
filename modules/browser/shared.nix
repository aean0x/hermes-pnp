# Shared browser derivations and paths. Imported by module/host/container.
{ config, lib, pkgs }:

let
  pnp = config.services.hermesPnP;
  agent = config.services.hermes-agent;
  cfg = pnp.browser;
  bctr = cfg.container;

  profileDir = cfg.profileDir;
  cookiesDir = cfg.cookiesDir;
  logDir = cfg.logDir;
  workspaceDir = cfg.workspaceDir;
  cdpPort = cfg.cdpPort;
  gatePort = cfg.gate.port;
  listenAddr = cfg.gate.listenAddress;
  displayNum = "99";
  display = ":${displayNum}";
  cdpAddr = "127.0.0.1";

  home = "${agent.stateDir}/home";
  hermesEnv = "${agent.stateDir}/.hermes/.env";
  gateHome = "${agent.stateDir}/browser-gate";
  cdpEnvFile = "/run/hermes-browser.env";

  browserBin = "${cfg.package}/bin/${cfg.engine}";

  agentBrowser = pkgs.agent-browser or (pkgs.callPackage ../../pkgs/agent-browser.nix { });

  gateUrl =
    if cfg.gate.publicUrl != null
    then cfg.gate.publicUrl
    else "http://127.0.0.1:${toString gatePort}";

  chromiumAliases = pkgs.runCommand "chromium-alias" { } ''
    mkdir -p "$out/bin"
    ln -s ${browserBin} "$out/bin/chromium"
    ln -s ${browserBin} "$out/bin/chrome"
    ln -s ${browserBin} "$out/bin/google-chrome"
  '';

  importCookiesPy = ../../services/browser/src/import-browser-cookies.py;
  hermesBrowserImportCookies = pkgs.writeShellApplication {
    name = "hermes-browser-import-cookies";
    runtimeInputs = [
      (pkgs.python3.withPackages (ps: [ ps.websocket-client ]))
      pkgs.curl
      pkgs.coreutils
    ];
    text = ''
      set -euo pipefail
      exec python3 ${importCookiesPy} --cdp "http://${cdpAddr}:${toString cdpPort}" "$@"
    '';
  };

  # Foreground supervisor around a daemonizing dashboard.
  # dashboard start returns immediately; we health-check and stay
  # in the foreground so systemd can restart us. Never `connect`
  # unless CDP is up — connect would otherwise spawn a second browser.
  hermesBrowserGate = pkgs.writeShellApplication {
    name = "hermes-browser-gate";
    runtimeInputs = [
      agentBrowser
      pkgs.curl
      pkgs.coreutils
      pkgs.gnugrep
    ];
    text = ''
      set -euo pipefail

      export AGENT_BROWSER_NAMESPACE=hermes
      export AGENT_BROWSER_IDLE_TIMEOUT_MS=0
      export HOME=${gateHome}
      export XDG_CONFIG_HOME=${gateHome}/.config
      export XDG_CACHE_HOME=${gateHome}/.cache
      export XDG_STATE_HOME=${gateHome}/.local/state
      mkdir -p "$HOME" "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME" "$XDG_STATE_HOME" ${logDir}

      cdp="http://${cdpAddr}:${toString cdpPort}"
      dash="http://127.0.0.1:${toString gatePort}"

      cleanup() {
        agent-browser dashboard stop >/dev/null 2>&1 || true
        exit 0
      }
      trap cleanup TERM INT

      echo "waiting for CDP $cdp"
      up=0
      for _ in $(seq 1 90); do
        if curl -sf --max-time 1 "$cdp/json/version" >/dev/null; then
          up=1
          break
        fi
        sleep 1
      done
      if [[ "$up" != "1" ]]; then
        echo "cdp never came up; refusing to connect (would spawn a second browser)" >&2
        exit 1
      fi

      # Stale-daemon quirk: "already running" while the port is dead.
      if ! curl -sf --max-time 1 -o /dev/null "$dash/"; then
        agent-browser dashboard stop >/dev/null 2>&1 || true
      fi

      echo "attaching to existing browser via CDP $cdp"
      agent-browser connect ${toString cdpPort}

      echo "starting dashboard on $dash"
      agent-browser dashboard start --port ${toString gatePort}

      while true; do
        if ! curl -sf --max-time 2 -o /dev/null "$dash/"; then
          echo "dashboard down; restarting"
          agent-browser dashboard stop >/dev/null 2>&1 || true
          agent-browser dashboard start --port ${toString gatePort} || true
        fi
        if ! agent-browser session info --json 2>/dev/null | grep -q '"connectionMethod":"cdp"'; then
          if curl -sf --max-time 1 "$cdp/json/version" >/dev/null; then
            echo "session dropped; reconnecting"
            agent-browser connect ${toString cdpPort} || true
          fi
        fi
        sleep 5
      done
    '';
  };
in
{
  inherit
    pnp
    agent
    cfg
    bctr
    profileDir
    cookiesDir
    logDir
    workspaceDir
    cdpPort
    gatePort
    listenAddr
    displayNum
    display
    cdpAddr
    home
    hermesEnv
    gateHome
    cdpEnvFile
    browserBin
    agentBrowser
    gateUrl
    chromiumAliases
    hermesBrowserImportCookies
    hermesBrowserGate
    ;
}
