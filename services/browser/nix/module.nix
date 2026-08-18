# Persistent CDP browser + optional phone noVNC.
#
# Two modes:
# - host-native (default unless hermesPnP.container.enable): systemd
#   units as the hermes user, hardened.
# - container: one Ubuntu OCI container (same docker create --network=host
#   + /nix/store pattern as official hermes-agent.container). Mounts
#   workspace + profile + cookies + logs. Not hermes home, not .hermes,
#   not /etc. Xvfb/x11vnc/noVNC live in the same container so they share
#   the X socket.
#
# Takeover: same local browser, two control planes.
#   Agent  = CDP 127.0.0.1:9222
#   Human  = noVNC on listenAddress (default 127.0.0.1) via Caddy
# VNC password stays as a second factor. Do not bind 0.0.0.0.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  inherit (lib)
    mkDefault
    mkIf
    mkMerge
    optionals
    ;

  pnp = config.services.hermesPnP;
  agent = config.services.hermes-agent;
  cfg = pnp.browser;
  bctr = cfg.container;

  profileDir = cfg.profileDir;
  cookiesDir = cfg.cookiesDir;
  logDir = cfg.logDir;
  workspaceDir = cfg.workspaceDir;
  cdpPort = cfg.cdpPort;
  vncPort = cfg.noVNC.vncPort;
  novncPort = cfg.noVNC.port;
  listenAddr = cfg.noVNC.listenAddress;
  displayNum = "99";
  display = ":${displayNum}";
  cdpAddr = "127.0.0.1";

  home = "${agent.stateDir}/home";
  hermesEnv = "${agent.stateDir}/.hermes/.env";

  vncPassFile = "${agent.stateDir}/browser-vnc.pass";
  vncEnvFile = "/run/hermes-browser-vnc.env";
  cdpEnvFile = "/run/hermes-browser.env";

  browserBin = "${cfg.package}/bin/${cfg.engine}";

  novncUrl =
    if cfg.noVNC.publicUrl != null
    then cfg.noVNC.publicUrl
    else "http://127.0.0.1:${toString novncPort}/vnc.html";

  chromiumAliases = pkgs.runCommand "chromium-alias" { } ''
    mkdir -p "$out/bin"
    ln -s ${browserBin} "$out/bin/chromium"
    ln -s ${browserBin} "$out/bin/chrome"
    ln -s ${browserBin} "$out/bin/google-chrome"
  '';

  importCookiesPy = ../src/import-browser-cookies.py;
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

  # Host-native hardening. Unused in container mode (the container is the jail).
  hardenHost = {
    NoNewPrivileges = true;
    CapabilityBoundingSet = [ ];
    ProtectSystem = "full";
    ProtectHome = "read-only";
    ProtectKernelTunables = true;
    ProtectKernelModules = true;
    ProtectKernelLogs = true;
    ProtectControlGroups = true;
    RestrictSUIDSGID = true;
  };

  oci = import ../../../nix/lib/oci-container.nix { inherit pkgs lib; };
  entrypoint = oci.mkSlimEntrypoint "hermes-browser";

  supervisor = pkgs.writeShellApplication {
    name = "hermes-browser-supervisor";
    runtimeInputs = [
      pkgs.xvfb
      pkgs.xorg.xdpyinfo
      pkgs.x11vnc
      pkgs.novnc
      pkgs.python3Packages.websockify
      pkgs.coreutils
      cfg.package
    ];
    text = ''
      set -euo pipefail
      export DISPLAY=${display}
      export XAUTHORITY=/tmp/browser.xauth
      export HOME=/tmp/browser-home
      export XDG_CONFIG_HOME=/tmp/browser-home/.config
      export XDG_CACHE_HOME=/tmp/browser-home/.cache
      mkdir -p "$HOME" "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME" /tmp/.X11-unix ${profileDir} ${cookiesDir} ${logDir}
      touch "$XAUTHORITY"
      chmod 600 "$XAUTHORITY"

      rm -f /tmp/.X${displayNum}-lock /tmp/.X11-unix/X${displayNum} || true
      Xvfb ${display} -screen 0 1400x900x24 -ac +extension GLX +render -noreset &
      for _ in $(seq 1 50); do
        if xdpyinfo -display ${display} >/dev/null 2>&1; then break; fi
        sleep 0.1
      done

      if [[ "''${VNC_ENABLE:-}" == "1" ]]; then
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
          exit 1
        fi
        x11vnc \
          -display ${display} \
          -rfbport ${toString vncPort} \
          -localhost \
          -rfbauth ${profileDir}/.vncpass \
          -shared -forever -noxdamage -wait 10 -defer 10 \
          -o ${logDir}/x11vnc.log &
        websockify \
          --web "$webroot" \
          ${listenAddr}:${toString novncPort} \
          127.0.0.1:${toString vncPort} &
      fi

      # --no-sandbox: container is the jail (no chrome-sandbox SUID).
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

  volumes = [
    "/nix/store:/nix/store:ro"
    "${workspaceDir}:${workspaceDir}"
    "${profileDir}:${profileDir}"
    "${cookiesDir}:${cookiesDir}"
    "${logDir}:${logDir}"
    "${vncPassFile}:${vncPassFile}:ro"
  ] ++ bctr.extraVolumes;

  extraEnv = lib.optionalAttrs cfg.noVNC.enable { VNC_ENABLE = "1"; };

  identity = {
    image = bctr.image;
    inherit volumes;
    extraOptions = bctr.extraOptions;
    inherit extraEnv;
    package = "${supervisor}";
    inherit entrypoint;
  };

  unit = oci.mkUnitScripts {
    backend = bctr.backend;
    containerName = "hermes-browser";
    image = bctr.image;
    user = agent.user;
    inherit volumes extraEnv entrypoint identity;
    extraOptions = bctr.extraOptions;
    envFiles = [ ];
    command = [ "${supervisor}/bin/hermes-browser-supervisor" ];
    identityFile = "${profileDir}/.oci-identity";
  };
in
{
  config = mkMerge [
    (mkIf pnp.enable {
      services.hermesPnP.browser.container.enable = mkDefault pnp.container.enable;
      services.hermesPnP.browser.container.backend = mkDefault pnp.container.backend;
      services.hermesPnP.browser.container.image = mkDefault pnp.container.image;
    })

    (mkIf (pnp.enable && cfg.enable) {
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
          echo "mode:     ${if bctr.enable then "container" else "host-native"}"
          echo "profile:  ${profileDir}"
          echo "workspace:${workspaceDir}"
          echo "cookies:  ${cookiesDir}  (drop Netscape/JSON; import with hermes-browser-import-cookies)"
          echo "cdp:      http://${cdpAddr}:${toString cdpPort}"
          echo "novnc:    ${novncUrl}"
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

      # Loopback is the default. Only punch the firewall if someone
      # explicitly binds off-loopback (not recommended).
      networking.firewall.allowedTCPPorts =
        optionals (cfg.noVNC.enable && listenAddr != "127.0.0.1" && listenAddr != "localhost") [ novncPort ];

      systemd.tmpfiles.rules = [
        "d ${profileDir} 0750 ${agent.user} ${agent.group} - -"
        "d ${cookiesDir} 0750 ${agent.user} ${agent.group} - -"
        "d ${logDir} 0750 ${agent.user} ${agent.group} - -"
        "d ${workspaceDir} 0755 ${agent.user} ${agent.group} - -"
        "d ${home} 0755 ${agent.user} ${agent.group} - -"
        "f ${cdpEnvFile} 0640 ${agent.user} ${agent.group} - "
        "f ${vncEnvFile} 0640 ${agent.user} ${agent.group} - "
        "f ${vncPassFile} 0600 ${agent.user} ${agent.group} - "
      ];

      services.hermes-agent = {
        environment = {
          BROWSER_CDP_URL = "http://${cdpAddr}:${toString cdpPort}";
          BU_CDP_URL = "http://${cdpAddr}:${toString cdpPort}";
          HERMES_BROWSER_CDP_URL = "http://${cdpAddr}:${toString cdpPort}";
          HERMES_BROWSER_PROFILE = profileDir;
          HERMES_BROWSER_NOVNC_PORT = toString novncPort;
          HERMES_BROWSER_ENGINE = cfg.engine;
        };
        environmentFiles = [ cdpEnvFile ];
        settings.browser = {
          cdp_url = "http://${cdpAddr}:${toString cdpPort}";
          allow_private_urls = true;
        };
      };

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

          if [[ ! -s ${vncPassFile} ]]; then
            pw="$(${pkgs.openssl}/bin/openssl rand -base64 18 | ${pkgs.coreutils}/bin/tr -dc 'A-Za-z0-9' | ${pkgs.coreutils}/bin/head -c 12)"
            echo -n "$pw" > ${vncPassFile}
            chown ${agent.user}:${agent.group} ${vncPassFile}
            chmod 0600 ${vncPassFile}
          fi
          pw="$(${pkgs.coreutils}/bin/cat ${vncPassFile})"
          ${pkgs.x11vnc}/bin/x11vnc -storepasswd "$pw" ${profileDir}/.vncpass
          chown ${agent.user}:${agent.group} ${profileDir}/.vncpass
          chmod 0600 ${profileDir}/.vncpass

          cat > ${cdpEnvFile} <<EOF
          # Auto-generated by services/browser/nix/module.nix — do not edit
          BROWSER_CDP_URL=http://${cdpAddr}:${toString cdpPort}
          BU_CDP_URL=http://${cdpAddr}:${toString cdpPort}
          HERMES_BROWSER_CDP_URL=http://${cdpAddr}:${toString cdpPort}
          HERMES_BROWSER_PROFILE=${profileDir}
          HERMES_BROWSER_NOVNC_URL=${novncUrl}
          HERMES_BROWSER_NOVNC_PORT=${toString novncPort}
          HERMES_BROWSER_ENGINE=${cfg.engine}
          EOF
          chown ${agent.user}:${agent.group} ${cdpEnvFile}
          chmod 0640 ${cdpEnvFile}

          cat > ${vncEnvFile} <<EOF
          # Auto-generated — agent may relay to user on captcha handoff
          HERMES_BROWSER_NOVNC_URL=${novncUrl}
          HERMES_BROWSER_NOVNC_PASSWORD=$pw
          HERMES_BROWSER_VNC_PASSWORD=$pw
          EOF
          chown ${agent.user}:${agent.group} ${vncEnvFile}
          chmod 0640 ${vncEnvFile}

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
    })

    # Host-native units (Xvfb/browser/vnc/novnc as separate systemd services).
    (mkIf (pnp.enable && cfg.enable && !bctr.enable) {
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
          MemoryMax = "1G";
          OOMScoreAdjust = 500;
          TimeoutStartSec = 90;
          StandardOutput = "append:${logDir}/browser.stdout";
          StandardError = "append:${logDir}/browser.stderr";
        } // hardenHost;

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

      systemd.services.hermes-browser-vnc = mkIf cfg.noVNC.enable {
        description = "Hermes browser x11vnc (password-gated, loopback)";
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
        } // hardenHost;

        environment.DISPLAY = display;

        script = ''
          set -euo pipefail
          for i in $(seq 1 30); do
            if [[ -e /tmp/.X11-unix/X${displayNum} ]]; then break; fi
            sleep 0.5
          done
          exec ${pkgs.x11vnc}/bin/x11vnc \
            -display ${display} \
            -rfbport ${toString vncPort} \
            -localhost \
            -rfbauth ${profileDir}/.vncpass \
            -shared \
            -forever \
            -noxdamage \
            -wait 10 \
            -defer 10 \
            -o ${logDir}/x11vnc.log
        '';
      };

      systemd.services.hermes-browser-novnc = mkIf cfg.noVNC.enable {
        description = "Hermes browser noVNC (loopback; proxy via Caddy)";
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
        } // hardenHost;

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
            ${listenAddr}:${toString novncPort} \
            127.0.0.1:${toString vncPort}
        '';
      };
    })

    # OCI mode: one container, one unit. vnc/novnc are inside.
    (mkIf (pnp.enable && cfg.enable && bctr.enable) {
      virtualisation.docker.enable = mkIf (bctr.backend == "docker") (mkDefault true);

      systemd.services.hermes-browser = {
        description = "Hermes persistent browser (OCI, official-container-shaped)";
        wantedBy = [ "multi-user.target" ];
        after = [
          "network-online.target"
          "docker.service"
          "hermes-browser-env.service"
        ];
        wants = [
          "network-online.target"
          "hermes-browser-env.service"
        ];
        requires = [ "docker.service" ];
        preStart = unit.preStart;
        script = unit.script;
        preStop = unit.preStop;
        serviceConfig = {
          Type = "simple";
          Restart = "on-failure";
          RestartSec = 5;
          MemoryMax = "1G";
          OOMScoreAdjust = 500;
          TimeoutStartSec = 180;
          TimeoutStopSec = 30;
        };
        path = [ pkgs.docker pkgs.coreutils ];
      };
    })
  ];
}
