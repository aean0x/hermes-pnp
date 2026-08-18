# Host-native: Brave independently supervised; gate is a CDP client.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  oci = import ../../lib { inherit pkgs lib; };
  shared = import ./shared.nix { inherit config lib pkgs; };
  inherit (shared)
    pnp
    agent
    cfg
    bctr
    profileDir
    cookiesDir
    logDir
    home
    display
    displayNum
    cdpAddr
    cdpPort
    browserBin
    hermesBrowserGate
    ;

  hostHarden = oci.hardenHost // {
    ProtectSystem = "full";
    ProtectHome = "read-only";
  };
in
{
  config = lib.mkIf (pnp.enable && cfg.enable && !bctr.enable) {
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
      } // hostHarden;

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

    systemd.services.hermes-browser-gate = lib.mkIf cfg.gate.enable {
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
      } // hostHarden;
    };
  };
}
