# Shared paths and Xvfb + Chromium launch for host and container.
{ config, lib, pkgs }:

let
  pnp = config.services.hermesPnP;
  agent = config.services.hermes-agent;
  cfg = pnp.browser;

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
  cdpUrl = "http://${cdpAddr}:${toString cdpPort}";

  home = "${agent.stateDir}/home";
  gateHome = "${agent.stateDir}/browser-gate";

  browserBin = "${cfg.package}/bin/${cfg.engine}";

  # Always the in-tree pin. nixpkgs agent-browser is 0.27.0; `or` would
  # pick it and keep the old daemon (keyboard stream broken until 0.33.2).
  agentBrowser = pkgs.callPackage ../../pkgs/agent-browser.nix { };

  gateUrl =
    if cfg.gate.publicUrl != null
    then cfg.gate.publicUrl
    else "http://127.0.0.1:${toString gatePort}";

  originFromUrl =
    url:
    let
      m = builtins.match "([a-zA-Z][a-zA-Z0-9+.-]*://[^/?#]+).*" url;
    in
    if m == null then null else builtins.head m;

  loopbackOrigins = [
    "http://127.0.0.1:${toString cdpPort}"
    "http://localhost:${toString cdpPort}"
    "http://[::1]:${toString cdpPort}"
  ]
  ++ lib.optionals cfg.gate.enable [
    "http://127.0.0.1:${toString gatePort}"
    "http://localhost:${toString gatePort}"
    "http://[::1]:${toString gatePort}"
  ];

  listenOrigin =
    if
      cfg.gate.enable
      && listenAddr != "127.0.0.1"
      && listenAddr != "localhost"
      && listenAddr != "::1"
      && listenAddr != "0.0.0.0"
      && listenAddr != "::"
    then
      "http://${listenAddr}:${toString gatePort}"
    else
      null;

  publicOrigin = if cfg.gate.publicUrl != null then originFromUrl cfg.gate.publicUrl else null;

  computedOrigins = lib.unique (
    loopbackOrigins
    ++ lib.optional (listenOrigin != null) listenOrigin
    ++ lib.optional (publicOrigin != null) publicOrigin
  );

  allowOrigins =
    if cfg.cdpAllowOrigins == null || cfg.cdpAllowOrigins == [ ] then
      computedOrigins
    else
      cfg.cdpAllowOrigins;

  allowOriginsFlag = lib.concatStringsSep "," allowOrigins;

  # Ubuntu OCI image ships no fonts. Empty fontconfig then Skia FATALS
  # FontConfigInterface (clicks / text layout). Pin a store fonts.conf.
  fontconfigFile = pkgs.makeFontsConf {
    fontDirectories = [
      "${pkgs.dejavu_fonts}/share/fonts"
      "${pkgs.liberation_ttf}/share/fonts"
      "${pkgs.noto-fonts-color-emoji}/share/fonts"
    ];
  };

  chromiumAliases = pkgs.runCommand "chromium-alias" { } ''
    mkdir -p "$out/bin"
    ln -s ${browserBin} "$out/bin/chromium"
    ln -s ${browserBin} "$out/bin/chrome"
    ln -s ${browserBin} "$out/bin/google-chrome"
  '';

  importCookiesPy = ./import-browser-cookies.py;
  hermesBrowserImportCookies = pkgs.writeShellApplication {
    name = "hermes-browser-import-cookies";
    runtimeInputs = [
      (pkgs.python3.withPackages (ps: [ ps.websocket-client ]))
      pkgs.curl
      pkgs.coreutils
    ];
    text = ''
      set -euo pipefail
      exec python3 ${importCookiesPy} --cdp "${cdpUrl}" "$@"
    '';
  };

  # Foreground supervisor: dashboard start returns immediately.
  # Wait for CDP before connect or agent-browser starts a second browser.
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

      cdp="${cdpUrl}"
      dash="http://${
        if listenAddr == "0.0.0.0" || listenAddr == "::" then "127.0.0.1" else listenAddr
      }:${toString gatePort}"

      cleanup() {
        agent-browser dashboard stop >/dev/null 2>&1 || true
        exit 0
      }
      trap cleanup TERM INT

      # gateHome is bind-mounted. A leftover default.version (0.27.0)
      # plus the full /nix/store bind lets connect exec an old daemon.
      rm -f "$HOME/.agent-browser/"*.pid \
            "$HOME/.agent-browser/"*.sock \
            "$HOME/.agent-browser/"*.stream \
            "$HOME/.agent-browser/"*.version
      rm -rf "$HOME/.agent-browser/namespaces"
      agent-browser dashboard stop >/dev/null 2>&1 || true

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

      echo "attaching to existing browser via CDP $cdp"
      # Port-only connect uses localhost, which is ::1 in the jail.
      # Brave binds 127.0.0.1 only, so that misses CDP and the daemon dies.
      agent-browser connect ${cdpUrl}

      start_dash() {
        if agent-browser dashboard start --help 2>&1 | grep -q -- '--host'; then
          agent-browser dashboard start --port ${toString gatePort} --host '${listenAddr}'
        else
          agent-browser dashboard start --port ${toString gatePort}
        fi
      }

      # Dashboard GET / blocks while /api/exec is in flight. Treating
      # that as "down" stops the dashboard (502) and a second connect
      # steals Chrome from the supervisor (exit 0, tabs wiped).
      run="$HOME/.agent-browser/namespaces/$AGENT_BROWSER_NAMESPACE/run"
      pidfile="$run/default.pid"
      dashpid="$run/dashboard.pid"
      pid_alive() {
        local f="$1"
        [[ -f "$f" ]] || return 1
        kill -0 "$(cat "$f")" 2>/dev/null
      }
      cdp_ok() { curl -sf --max-time 1 "$cdp/json/version" >/dev/null; }

      echo "starting dashboard on $dash (host ${listenAddr})"
      start_dash
      for _ in $(seq 1 25); do
        pid_alive "$dashpid" && break
        sleep 0.2
      done

      while true; do
        if cdp_ok && pid_alive "$pidfile" && pid_alive "$dashpid"; then
          sleep 5
          continue
        fi
        echo "session/dashboard down; reconnecting"
        if ! cdp_ok; then
          echo "CDP down; waiting for engine supervisor"
          sleep 5
          continue
        fi
        if ! pid_alive "$pidfile"; then
          agent-browser connect ${cdpUrl} || true
        fi
        if ! pid_alive "$dashpid"; then
          start_dash || true
        fi
        sleep 5
      done
    '';
  };

  launchXvfb = ''
    rm -f /tmp/.X${displayNum}-lock /tmp/.X11-unix/X${displayNum} || true
    ${pkgs.xvfb}/bin/Xvfb ${display} -screen 0 1400x900x24 -ac +extension GLX +render -noreset &
  '';

  waitForDisplay = ''
    for _ in $(seq 1 50); do
      if ${pkgs.xdpyinfo}/bin/xdpyinfo -display ${display} >/dev/null 2>&1; then break; fi
      sleep 0.1
    done
  '';

  chromiumExec = ''
    export FONTCONFIG_FILE=${fontconfigFile}
    ${browserBin} \
      --user-data-dir=${profileDir} \
      --remote-debugging-address=${cdpAddr} \
      --remote-debugging-port=${toString cdpPort} \
      --remote-allow-origins='${allowOriginsFlag}' \
      --no-first-run \
      --no-default-browser-check \
      --no-sandbox \
      --disable-setuid-sandbox \
      --disable-dev-shm-usage \
      --disable-gpu \
      --password-store=basic \
      --window-size=1400,900 \
      --disable-features=TranslateUI \
      ${lib.concatStringsSep " " cfg.extraArgs}
  '';
in
{
  inherit
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
    cdpUrl
    home
    gateHome
    browserBin
    agentBrowser
    gateUrl
    chromiumAliases
    hermesBrowserImportCookies
    hermesBrowserGate
    launchXvfb
    waitForDisplay
    chromiumExec
    fontconfigFile
    ;
}
