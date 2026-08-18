# Persistent CDP browser + agent-browser dashboard gate.
#
# Two modes:
# - host-native (default unless hermesPnP.container.enable): systemd
#   units as the hermes user, hardened. Brave is independently
#   supervised so a gate crash does not kill the warm profile.
# - container: one Ubuntu OCI container (same docker create --network=host
#   + /nix/store pattern as official hermes-agent.container). Mounts
#   workspace + profile + cookies + logs + gate state. Not hermes home,
#   not .hermes, not /etc. Xvfb + engine + gate share the container.
#
# Takeover: same local browser, two control planes.
#   Agent  = CDP 127.0.0.1:9222  (browser_* tools, unchanged)
#   Human  = agent-browser dashboard on listenAddress (default 127.0.0.1)
#            via Caddy. CDP screencast + input injection. No VNC, no
#            password, no framebuffer.
#
# agent-browser connect ATTACHES to the existing engine. It will launch
# its own Chrome if CDP is down — the gate refuses to connect until
# /json/version answers.
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

  agentBrowser = pkgs.callPackage ./package.nix { };

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
      pkgs.coreutils
      pkgs.curl
      cfg.package
      hermesBrowserGate
    ];
    text = ''
      set -euo pipefail
      export DISPLAY=${display}
      export XAUTHORITY=/tmp/browser.xauth
      export HOME=/tmp/browser-home
      export XDG_CONFIG_HOME=/tmp/browser-home/.config
      export XDG_CACHE_HOME=/tmp/browser-home/.cache
      mkdir -p "$HOME" "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME" /tmp/.X11-unix ${profileDir} ${cookiesDir} ${logDir} ${gateHome}
      touch "$XAUTHORITY"
      chmod 600 "$XAUTHORITY"

      rm -f /tmp/.X${displayNum}-lock /tmp/.X11-unix/X${displayNum} || true
      Xvfb ${display} -screen 0 1400x900x24 -ac +extension GLX +render -noreset &
      for _ in $(seq 1 50); do
        if xdpyinfo -display ${display} >/dev/null 2>&1; then break; fi
        sleep 0.1
      done

      ${
        if cfg.gate.enable then ''
          hermes-browser-gate &
        '' else ""
      }

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
    "${gateHome}:${gateHome}"
  ] ++ bctr.extraVolumes;

  identity = {
    image = bctr.image;
    inherit volumes entrypoint;
    extraOptions = bctr.extraOptions;
    extraEnv = { };
    package = "${supervisor}";
  };

  unit = oci.mkUnitScripts {
    backend = bctr.backend;
    containerName = "hermes-browser";
    image = bctr.image;
    user = agent.user;
    inherit volumes entrypoint identity;
    extraEnv = { };
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
        agentBrowser
        hermesBrowserImportCookies
        hermesBrowserGate
        (pkgs.writeShellScriptBin "hermes-browser-status" ''
          set -euo pipefail
          echo "engine:   ${cfg.engine}"
          echo "mode:     ${if bctr.enable then "container" else "host-native"}"
          echo "profile:  ${profileDir}"
          echo "workspace:${workspaceDir}"
          echo "cookies:  ${cookiesDir}  (drop Netscape/JSON; import with hermes-browser-import-cookies)"
          echo "cdp:      http://${cdpAddr}:${toString cdpPort}"
          echo "gate:     ${gateUrl}"
          if ${pkgs.curl}/bin/curl -fsS --max-time 2 "http://${cdpAddr}:${toString cdpPort}/json/version"; then
            echo
            echo "cdp:      up"
          else
            echo "cdp:      down"
          fi
          if ${pkgs.curl}/bin/curl -fsS --max-time 2 -o /dev/null "http://127.0.0.1:${toString gatePort}/"; then
            echo "gate:     up"
          else
            echo "gate:     down"
          fi
          ${pkgs.systemd}/bin/systemctl is-active hermes-browser.service hermes-browser-gate.service || true
        '')
      ];

      # Dashboard binds loopback. Only punch the firewall if someone
      # explicitly binds off-loopback (not recommended; Caddy instead).
      networking.firewall.allowedTCPPorts =
        optionals (cfg.gate.enable && listenAddr != "127.0.0.1" && listenAddr != "localhost") [ gatePort ];

      systemd.tmpfiles.rules = [
        "d ${profileDir} 0750 ${agent.user} ${agent.group} - -"
        "d ${cookiesDir} 0750 ${agent.user} ${agent.group} - -"
        "d ${logDir} 0750 ${agent.user} ${agent.group} - -"
        "d ${workspaceDir} 0755 ${agent.user} ${agent.group} - -"
        "d ${gateHome} 0750 ${agent.user} ${agent.group} - -"
        "d ${home} 0755 ${agent.user} ${agent.group} - -"
        "f ${cdpEnvFile} 0640 ${agent.user} ${agent.group} - "
      ];

      services.hermes-agent = {
        environment = {
          BROWSER_CDP_URL = "http://${cdpAddr}:${toString cdpPort}";
          BU_CDP_URL = "http://${cdpAddr}:${toString cdpPort}";
          HERMES_BROWSER_CDP_URL = "http://${cdpAddr}:${toString cdpPort}";
          HERMES_BROWSER_PROFILE = profileDir;
          HERMES_BROWSER_GATE_URL = gateUrl;
          HERMES_BROWSER_GATE_PORT = toString gatePort;
          HERMES_BROWSER_ENGINE = cfg.engine;
        };
        settings.browser = {
          cdp_url = "http://${cdpAddr}:${toString cdpPort}";
        };
      };

      systemd.services.hermes-browser-env = {
        description = "Seed Hermes browser CDP + gate env";
        wantedBy = [ "multi-user.target" ];
        before = [ "hermes-browser.service" ];

        serviceConfig = {
          Type = "oneshot";
          RemainAfterExit = true;
          User = "root";
        };

        script = ''
          set -euo pipefail
          umask 027
          cat > ${cdpEnvFile} <<EOF
          BROWSER_CDP_URL=http://${cdpAddr}:${toString cdpPort}
          BU_CDP_URL=http://${cdpAddr}:${toString cdpPort}
          HERMES_BROWSER_CDP_URL=http://${cdpAddr}:${toString cdpPort}
          HERMES_BROWSER_PROFILE=${profileDir}
          HERMES_BROWSER_GATE_URL=${gateUrl}
          HERMES_BROWSER_GATE_PORT=${toString gatePort}
          HERMES_BROWSER_ENGINE=${cfg.engine}
          EOF
          chown ${agent.user}:${agent.group} ${cdpEnvFile}

          if [[ -f ${hermesEnv} ]]; then
            ${pkgs.gnused}/bin/sed -i \
              -e '/^BROWSER_CDP_URL=/d' \
              -e '/^BU_CDP_URL=/d' \
              -e '/^HERMES_BROWSER_CDP_URL=/d' \
              -e '/^HERMES_BROWSER_PROFILE=/d' \
              -e '/^HERMES_BROWSER_GATE_URL=/d' \
              -e '/^HERMES_BROWSER_GATE_PORT=/d' \
              -e '/^HERMES_BROWSER_ENGINE=/d' \
              -e '/^HERMES_BROWSER_NOVNC_URL=/d' \
              -e '/^HERMES_BROWSER_NOVNC_PORT=/d' \
              -e '/^HERMES_BROWSER_NOVNC_PASSWORD=/d' \
              -e '/^HERMES_BROWSER_VNC_PASSWORD=/d' \
              ${hermesEnv}
            cat >> ${hermesEnv} <<EOF
          BROWSER_CDP_URL=http://${cdpAddr}:${toString cdpPort}
          BU_CDP_URL=http://${cdpAddr}:${toString cdpPort}
          HERMES_BROWSER_CDP_URL=http://${cdpAddr}:${toString cdpPort}
          HERMES_BROWSER_PROFILE=${profileDir}
          HERMES_BROWSER_GATE_URL=${gateUrl}
          HERMES_BROWSER_GATE_PORT=${toString gatePort}
          HERMES_BROWSER_ENGINE=${cfg.engine}
          EOF
            chown ${agent.user}:${agent.group} ${hermesEnv}
          fi
        '';
      };
    })

    # Host-native: Brave independently supervised; gate is a CDP client.
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
          # Xvfb + engine share this unit, so PrivateTmp is safe now
          # that x11vnc no longer needs the host /tmp/.X11-unix.
          PrivateTmp = true;
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

      systemd.services.hermes-browser-gate = mkIf cfg.gate.enable {
        description = "Hermes browser gate (agent-browser dashboard, loopback)";
        wantedBy = [ "multi-user.target" ];
        after = [
          "hermes-browser.service"
          "hermes-browser-env.service"
        ];
        wants = [ "hermes-browser.service" ];

        serviceConfig = {
          Type = "simple";
          User = agent.user;
          Group = agent.group;
          Restart = "always";
          RestartSec = 3;
          TimeoutStartSec = 120;
          StandardOutput = "append:${logDir}/gate.stdout";
          StandardError = "append:${logDir}/gate.stderr";
          PrivateTmp = true;
          ExecStart = "${hermesBrowserGate}/bin/hermes-browser-gate";
        } // hardenHost;
      };
    })

    # OCI mode: one container, one unit. Gate runs inside next to Brave.
    (mkIf (pnp.enable && cfg.enable && bctr.enable) {
      virtualisation.docker.enable = mkIf (bctr.backend == "docker") (mkDefault true);

      systemd.services.hermes-browser = {
        description = "Hermes persistent browser (OCI, official-container-shaped)";
        wantedBy = [ "multi-user.target" ];
        after = [
          "network-online.target"
          "hermes-browser-env.service"
          "docker.service"
        ];
        wants = [
          "network-online.target"
          "hermes-browser-env.service"
        ];
        path = [
          pkgs.docker
          pkgs.coreutils
        ];

        preStart = unit.preStart;
        script = unit.script;
        preStop = unit.preStop;
        serviceConfig = {
          Type = "simple";
          Restart = "on-failure";
          RestartSec = 5;
          TimeoutStartSec = 180;
          TimeoutStopSec = 30;
        };
      };
    })
  ];
}
