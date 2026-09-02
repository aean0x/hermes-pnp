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

  browserBin = "${cfg.package}/bin/${cfg.engine}";

  # Vendored @agent-infra/browser-ui UMD bundle. The gate serves it and
  # proxies CDP HTTP/WS so a human can drive the persistent engine.
  agentInfraBrowserUi = pkgs.callPackage ../../pkgs/agent-infra-browser-ui.nix { };

  browserUiGateJs = ./browser-ui-gate.js;

  browserUiStaticDir = pkgs.runCommand "hermes-browser-ui" { } ''
    mkdir -p "$out"
    cp ${./ui/index.html} "$out/index.html"
    cp ${./ui/app.js} "$out/app.js"
    cp ${agentInfraBrowserUi}/share/browser-ui/index.js "$out/browser-ui.js"
  '';

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

  # Loopback static server + CDP HTTP/WS proxy. No connect step, so the
  # gate can never spawn a second browser. Caddy fronts it with TLS + the
  # LAN guard; the page discovers the browser WS via /json/version and
  # rewrites it to same-origin.
  hermesBrowserGate = pkgs.writeShellApplication {
    name = "hermes-browser-gate";
    runtimeInputs = [
      pkgs.nodejs
      pkgs.coreutils
    ];
    text = ''
      set -euo pipefail
      mkdir -p ${logDir}
      exec ${pkgs.nodejs}/bin/node ${browserUiGateJs} \
        --listen '${listenAddr}' \
        --port ${toString gatePort} \
        --cdp "${cdpUrl}" \
        --static "${browserUiStaticDir}"
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
    browserBin
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
