# Host-native engine unit; gate is a separate CDP client.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  inherit (import ../../lib { inherit lib; }) hardenHost;
  pnp = config.services.hermesPnP;
  agent = config.services.hermes-agent;
  cfg = pnp.browser;
  bctr = cfg.container;

  shared = import ./shared.nix { inherit config lib pkgs; };
  inherit (shared)
    profileDir
    cookiesDir
    logDir
    home
    display
    hermesBrowserGate
    launchXvfb
    waitForDisplay
    chromiumExec
    ;

  hostHarden = hardenHost // {
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
      ];
      wants = [
        "network-online.target"
      ];

      # This unit runs the engine itself, so systemd resource control binds
      # here (unlike a jail unit, which only runs `docker start -a`). Take
      # the consumer's declared browser.container.memory / oomScoreAdj.
      # Unset means unbounded: RAM policy is consumer policy (AGENTS.md),
      # never a composer default.
      serviceConfig =
        {
          Type = "simple";
          User = agent.user;
          Group = agent.group;
          Restart = "on-failure";
          RestartSec = 5;
          TimeoutStartSec = 90;
          StandardOutput = "append:${logDir}/browser.stdout";
          StandardError = "append:${logDir}/browser.stderr";
          PrivateTmp = true;
        }
        // lib.optionalAttrs (bctr.memory != null) {
          MemoryMax = bctr.memory;
        }
        // lib.optionalAttrs (bctr.oomScoreAdj != null) {
          OOMScoreAdjust = bctr.oomScoreAdj;
        }
        // hostHarden;

      environment = {
        HOME = home;
        XDG_CONFIG_HOME = "${home}/.config";
        XDG_CACHE_HOME = "${home}/.cache";
        DISPLAY = display;
        FONTCONFIG_FILE = shared.fontconfigFile;
      };

      script = ''
        set -euo pipefail
        mkdir -p ${profileDir} ${cookiesDir} ${home} ${logDir}
        ${launchXvfb}
        xvfb_pid=$!
        trap 'kill $xvfb_pid 2>/dev/null || true' EXIT
        ${waitForDisplay}
        ${chromiumExec}
      '';
    };

    systemd.services.hermes-browser-gate = lib.mkIf cfg.gate.enable {
      description = "Hermes browser gate (browser-ui static + CDP proxy, loopback)";
      wantedBy = [ "multi-user.target" ];
      after = [
        "hermes-browser.service"
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
