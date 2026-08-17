# Opinionated host browser: persistent CDP browser + optional phone noVNC.
#
# Primary automation path is the loopback CDP endpoint (native hermes
# browser_* tools attach via browser.cdp_url / BROWSER_CDP_URL). noVNC is
# the human captcha handoff — same session, same cookies.
#
# The browser package is a user option (default chromium); swap for a
# Chromium fork that is less commonly fingerprinted as automation:
#   services.hermesPnP.browser = { package = pkgs.brave; engine = "brave"; };
#
# Surfaces:
# - CDP   http://127.0.0.1:9222        (loopback only — agent attach)
# - noVNC http://<host>:6080/vnc.html  (LAN/Tailscale — human captcha)
# - VNC   <host>:5900                  (optional raw; password-gated)
#
# Warm profile cookies:
#   sudo -u hermes hermes-browser-import-cookies /path/to/cookies.txt|.json
#   (Netscape or Playwright JSON; imports via CDP Network.setCookie)
{
  config,
  lib,
  pkgs,
  ...
}:

let
  inherit (lib)
    mkIf
    optionals
    ;

  pnp = config.services.hermesPnP;
  agent = config.services.hermes-agent;
  cfg = pnp.browser;

  profileDir = cfg.profileDir;
  cookiesDir = cfg.cookiesDir;
  logDir = cfg.logDir;
  cdpPort = cfg.cdpPort;
  vncPort = cfg.noVNC.vncPort;
  novncPort = cfg.noVNC.port;
  displayNum = "99";
  display = ":${displayNum}";
  cdpAddr = "127.0.0.1";

  home = "${agent.stateDir}/home";
  hermesEnv = "${agent.stateDir}/.hermes/.env";

  # Password file for x11vnc (auto-created if missing).
  vncPassFile = "${agent.stateDir}/browser-vnc.pass";
  # Plain password + URLs for Hermes to relay (mode 0640 hermes).
  vncEnvFile = "/run/hermes-browser-vnc.env";
  cdpEnvFile = "/run/hermes-browser.env";

  browserBin = "${cfg.package}/bin/${cfg.engine}";

  # Browser tools look for chromium / chrome / google-chrome on PATH; point
  # them at the configured engine so the fallback browser-cli uses the same
  # browser as the CDP service.
  chromiumAliases = pkgs.runCommand "chromium-alias" { } ''
    mkdir -p "$out/bin"
    ln -s ${browserBin} "$out/bin/chromium"
    ln -s ${browserBin} "$out/bin/chrome"
    ln -s ${browserBin} "$out/bin/google-chrome"
  '';

  importCookiesPy = ../browser/import-browser-cookies.py;
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
in
{
  config = mkIf (pnp.enable && cfg.enable) {
    assertions = [
      {
        assertion = cfg.engine != "";
        message = "services.hermesPnP.browser.engine is empty; set browser.package (engine follows mainProgram) or browser.engine.";
      }
    ];
    environment.systemPackages = [
      cfg.package
      chromiumAliases
      pkgs.xvfb
      pkgs.x11vnc
      pkgs.novnc
      pkgs.python3Packages.websockify
      hermesBrowserImportCookies
      (pkgs.writeShellScriptBin "hermes-browser-status" ''
        set -euo pipefail
        echo "engine:   ${cfg.engine}"
        echo "profile:  ${profileDir}"
        echo "cookies:  ${cookiesDir}  (drop Netscape/JSON; import with hermes-browser-import-cookies)"
        echo "cdp:      http://${cdpAddr}:${toString cdpPort}"
        echo "novnc:    http://0.0.0.0:${toString novncPort}/vnc.html"
        if ${pkgs.curl}/bin/curl -fsS --max-time 2 "http://${cdpAddr}:${toString cdpPort}/json/version"; then
          echo
          echo "cdp:      up"
        else
          echo "cdp:      down"
        fi
        if ${pkgs.curl}/bin/curl -fsS --max-time 2 -o /dev/null "http://127.0.0.1:${toString novncPort}/vnc.html"; then
          echo "novnc:    up"
        else
          echo "novnc:    down"
        fi
        if [[ -f ${vncEnvFile} ]]; then
          echo "--- relay env (password redacted) ---"
          ${pkgs.gnused}/bin/sed -E 's/(PASSWORD|VNC_PASSWORD)=.*/\1=***/' ${vncEnvFile}
        fi
        ${pkgs.systemd}/bin/systemctl is-active hermes-browser.service hermes-browser-vnc.service hermes-browser-novnc.service || true
      '')
    ];

    # Phone / LAN access to noVNC (password still required). Prefer Tailscale on cellular.
    networking.firewall.allowedTCPPorts = optionals cfg.noVNC.enable [ novncPort ];

    systemd.tmpfiles.rules = [
      "d ${profileDir} 0750 ${agent.user} ${agent.group} - -"
      "d ${cookiesDir} 0750 ${agent.user} ${agent.group} - -"
      "d ${logDir} 0750 ${agent.user} ${agent.group} - -"
      "d ${home} 0755 ${agent.user} ${agent.group} - -"
      "f ${cdpEnvFile} 0640 ${agent.user} ${agent.group} - "
      "f ${vncEnvFile} 0640 ${agent.user} ${agent.group} - "
    ];

    # Static CDP targets (always known at eval time). environmentFiles alone is
    # not enough: module merges those only at *activation*, while
    # hermes-browser-env fills /run/hermes-browser.env at *service start*.
    # browser_tool.py: BROWSER_CDP_URL env or browser.cdp_url in config.yaml.
    services.hermes-agent = {
      environment = {
        BROWSER_CDP_URL = "http://${cdpAddr}:${toString cdpPort}";
        BU_CDP_URL = "http://${cdpAddr}:${toString cdpPort}";
        HERMES_BROWSER_CDP_URL = "http://${cdpAddr}:${toString cdpPort}";
        HERMES_BROWSER_PROFILE = profileDir;
        HERMES_BROWSER_NOVNC_PORT = toString novncPort;
        HERMES_BROWSER_ENGINE = cfg.engine;
      };
      # Static vars above go straight into .env; the runtime noVNC URL (host IP
      # is only known at boot) is filled by hermes-browser-env into this file.
      environmentFiles = [ cdpEnvFile ];
      # CDP URLs are host-loopback and host-safe — environment{} → .env is enough.
      # Do not also --env them; extraOptions is for container-only PATH/HERMES_PY.
      settings.browser = {
        cdp_url = "http://${cdpAddr}:${toString cdpPort}";
        # attach to the host browser on loopback
        allow_private_urls = true;
      };
    };

    # CDP + path env for Hermes container (host network → loopback works).
    systemd.services.hermes-browser-env = {
      description = "Write Hermes browser CDP/noVNC env";
      wantedBy = [ "multi-user.target" ];
      before = [ "hermes-agent.service" ];
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
      };
      script = ''
        set -euo pipefail
        umask 027

        # Stable VNC password (create once).
        if [[ ! -s ${vncPassFile} ]]; then
          # 12 chars alnum — phone-typable
          pw="$(${pkgs.openssl}/bin/openssl rand -base64 18 | ${pkgs.coreutils}/bin/tr -dc 'A-Za-z0-9' | ${pkgs.coreutils}/bin/head -c 12)"
          echo -n "$pw" > ${vncPassFile}
          chown ${agent.user}:${agent.group} ${vncPassFile}
          chmod 0600 ${vncPassFile}
          # x11vnc store
          ${pkgs.x11vnc}/bin/x11vnc -storepasswd "$pw" ${profileDir}/.vncpass
          chown ${agent.user}:${agent.group} ${profileDir}/.vncpass
          chmod 0600 ${profileDir}/.vncpass
        fi
        pw="$(${pkgs.coreutils}/bin/cat ${vncPassFile})"

        host_ip="$(${pkgs.iproute2}/bin/ip -4 route get 1.1.1.1 2>/dev/null | ${pkgs.gawk}/bin/awk '{print $7; exit}' || true)"
        if [[ -z "''${host_ip:-}" ]]; then
          host_ip="${config.networking.hostName}"
        fi

        cat > ${cdpEnvFile} <<EOF
      # Auto-generated by hermes-browser.nix — do not edit
      BROWSER_CDP_URL=http://${cdpAddr}:${toString cdpPort}
      BU_CDP_URL=http://${cdpAddr}:${toString cdpPort}
      HERMES_BROWSER_CDP_URL=http://${cdpAddr}:${toString cdpPort}
      HERMES_BROWSER_PROFILE=${profileDir}
      HERMES_BROWSER_NOVNC_URL=http://''${host_ip}:${toString novncPort}/vnc.html
      HERMES_BROWSER_NOVNC_PORT=${toString novncPort}
      HERMES_BROWSER_ENGINE=${cfg.engine}
      EOF
        chown ${agent.user}:${agent.group} ${cdpEnvFile}
        chmod 0640 ${cdpEnvFile}

        cat > ${vncEnvFile} <<EOF
      # Auto-generated — agent may relay to user on captcha handoff
      HERMES_BROWSER_NOVNC_URL=http://''${host_ip}:${toString novncPort}/vnc.html
      HERMES_BROWSER_NOVNC_PASSWORD=$pw
      HERMES_BROWSER_VNC_PASSWORD=$pw
      EOF
        chown ${agent.user}:${agent.group} ${vncEnvFile}
        chmod 0640 ${vncEnvFile}

        # Upsert into Hermes dotenv (activation may merge empty env files first).
        if [[ -f "${hermesEnv}" ]]; then
          for key in BROWSER_CDP_URL BU_CDP_URL HERMES_BROWSER_CDP_URL HERMES_BROWSER_PROFILE HERMES_BROWSER_NOVNC_URL HERMES_BROWSER_NOVNC_PORT HERMES_BROWSER_ENGINE; do
            val="$(${pkgs.gnugrep}/bin/grep -E "^''${key}=" ${cdpEnvFile} | ${pkgs.coreutils}/bin/head -1 || true)"
            if [[ -n "$val" ]]; then
              ${pkgs.gnused}/bin/sed -i "/^''${key}=/d" "${hermesEnv}" 2>/dev/null || true
              echo "$val" >> "${hermesEnv}"
            fi
          done
          chown ${agent.user}:${agent.group} "${hermesEnv}"
          chmod 0640 "${hermesEnv}"
        fi
      '';
    };

    systemd.services.hermes-agent = {
      after = [
        "hermes-browser-env.service"
        "hermes-browser.service"
      ];
      wants = [ "hermes-browser-env.service" ];
    };

    # Browser on Xvfb (the automation surface). CDP protocol is identical
    # across Chromium forks.
    systemd.services.hermes-browser = {
      description = "Hermes persistent browser on Xvfb (CDP loopback)";
      wantedBy = [ "multi-user.target" ];
      after = [
        "network-online.target"
        "hermes-browser-env.service"
      ];
      wants = [
        "network-online.target"
        "hermes-browser-env.service"
      ];

      serviceConfig = {
        Type = "simple";
        User = agent.user;
        Group = agent.group;
        Restart = "on-failure";
        RestartSec = 5;
        # Tertiary: cap + prefer kill over HA/AdGuard under host memory pressure.
        MemoryMax = "1G";
        OOMScoreAdjust = 500;
        TimeoutStartSec = 90;
        StandardOutput = "append:${logDir}/browser.stdout";
        StandardError = "append:${logDir}/browser.stderr";
      };

      environment = {
        HOME = home;
        XDG_CONFIG_HOME = "${home}/.config";
        XDG_CACHE_HOME = "${home}/.cache";
        DISPLAY = display;
      };

      script = ''
        set -euo pipefail
        mkdir -p ${profileDir} ${cookiesDir} ${home} ${logDir}

        rm -f /tmp/.X${displayNum}-lock /tmp/.X11-unix/X${displayNum} || true

        ${pkgs.xvfb}/bin/Xvfb ${display} -screen 0 1400x900x24 -ac +extension GLX +render -noreset &
        xvfb_pid=$!
        trap 'kill $xvfb_pid 2>/dev/null || true' EXIT
        sleep 1

        # --no-sandbox: unprivileged service user without chrome-sandbox SUID.
        # password-store=basic: avoid keyring prompts under a headless service user.
        exec ${browserBin} \
          --user-data-dir=${profileDir} \
          --remote-debugging-address=${cdpAddr} \
          --remote-debugging-port=${toString cdpPort} \
          --remote-allow-origins=* \
          --no-first-run \
          --no-default-browser-check \
          --no-sandbox \
          --disable-setuid-sandbox \
          --disable-dev-shm-usage \
          --disable-gpu \
          --password-store=basic \
          --window-size=1400,900 \
          --disable-features=TranslateUI \
          ${lib.concatStringsSep " " cfg.extraArgs} \
          about:blank
      '';
    };

    # x11vnc mirrors Xvfb so a human can click captchas on the same session.
    systemd.services.hermes-browser-vnc = mkIf cfg.noVNC.enable {
      description = "Hermes browser x11vnc (password-gated)";
      wantedBy = [ "multi-user.target" ];
      after = [
        "hermes-browser.service"
        "hermes-browser-env.service"
      ];
      requires = [ "hermes-browser.service" ];

      serviceConfig = {
        Type = "simple";
        User = agent.user;
        Group = agent.group;
        Restart = "on-failure";
        RestartSec = 3;
        StandardOutput = "append:${logDir}/x11vnc.stdout";
        StandardError = "append:${logDir}/x11vnc.stderr";
      };

      environment.DISPLAY = display;

      # -localhost no: phone via Tailscale/LAN must reach it; password required.
      # CDP stays loopback-only; only the framebuffer is shared.
      script = ''
        set -euo pipefail
        # Wait for Xvfb
        for i in $(seq 1 30); do
          if [[ -e /tmp/.X11-unix/X${displayNum} ]]; then break; fi
          sleep 0.5
        done
        exec ${pkgs.x11vnc}/bin/x11vnc \
          -display ${display} \
          -rfbport ${toString vncPort} \
          -rfbauth ${profileDir}/.vncpass \
          -shared \
          -forever \
          -noxdamage \
          -wait 10 \
          -defer 10 \
          -o ${logDir}/x11vnc.log
      '';
    };

    # noVNC web UI → websockify → x11vnc (phone browser, no VNC app required).
    systemd.services.hermes-browser-novnc = mkIf cfg.noVNC.enable {
      description = "Hermes browser noVNC (phone captcha handoff)";
      wantedBy = [ "multi-user.target" ];
      after = [ "hermes-browser-vnc.service" ];
      requires = [ "hermes-browser-vnc.service" ];

      serviceConfig = {
        Type = "simple";
        User = agent.user;
        Group = agent.group;
        Restart = "on-failure";
        RestartSec = 3;
        StandardOutput = "append:${logDir}/novnc.stdout";
        StandardError = "append:${logDir}/novnc.stderr";
      };

      # novnc web root: prefer share/novnc (nixpkgs), fall back to share/webapps/novnc.
      script = ''
        set -euo pipefail
        webroot=""
        for d in \
          ${pkgs.novnc}/share/novnc \
          ${pkgs.novnc}/share/webapps/novnc \
          ${pkgs.novnc}/share/novnc/www
        do
          if [[ -d "$d" ]]; then webroot="$d"; break; fi
        done
        if [[ -z "$webroot" ]]; then
          echo "novnc web root not found under ${pkgs.novnc}" >&2
          ls -la ${pkgs.novnc}/share || true
          exit 1
        fi
        exec ${pkgs.python3Packages.websockify}/bin/websockify \
          --web "$webroot" \
          0.0.0.0:${toString novncPort} \
          127.0.0.1:${toString vncPort}
      '';
    };
  };
}
