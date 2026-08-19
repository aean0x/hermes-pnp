# One jail: Xvfb + engine + gate. Workspace/profile/cookies/logs/gate only.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  inherit (lib) mkDefault mkIf;

  oci = import ../../lib { inherit pkgs lib; };
  pnp = config.services.hermesPnP;
  agent = config.services.hermes-agent;
  cfg = pnp.browser;
  bctr = cfg.container;

  shared = import ./shared.nix { inherit config lib pkgs; };
  inherit (shared)
    profileDir
    cookiesDir
    logDir
    workspaceDir
    gateHome
    display
    hermesBrowserGate
    launchXvfb
    waitForDisplay
    chromiumExec
    cdpPort
    ;

  pruneTabs = pkgs.writeScript "hermes-browser-prune-tabs" ''
    #!${pkgs.python3}/bin/python3
    import json, sys, urllib.request
    port, cap = sys.argv[1], int(sys.argv[2])
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2) as r:
            targets = json.load(r)
    except Exception:
        raise SystemExit(0)
    pages = [t for t in targets if t.get("type") == "page" and t.get("id")]
    for t in pages[:-cap]:
        try:
            urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/close/{t['id']}", timeout=2
            ).read()
        except Exception:
            pass
  '';

  supervisor = pkgs.writeShellApplication {
    name = "hermes-browser-supervisor";
    runtimeInputs = [
      pkgs.coreutils
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

      ${launchXvfb}
      ${waitForDisplay}

      ${
        if cfg.gate.enable then ''
          hermes-browser-gate &
        '' else ""
      }

      ${
        if cfg.maxTabs != null then ''
          while true; do
            ${pruneTabs} ${toString cdpPort} ${toString cfg.maxTabs} || true
            sleep 20
          done &
        '' else ""
      }

      # Restart the engine in-process. Xvfb and the gate stay up.
      while true; do
        echo "$(date -Iseconds) start" >> ${logDir}/supervisor.log
        rm -f ${profileDir}/Default/"Current Session" \
              ${profileDir}/Default/"Last Session" \
              ${profileDir}/Default/"Current Tabs" \
              ${profileDir}/Default/"Last Tabs"
        rm -rf ${profileDir}/Default/Sessions
        ${chromiumExec} \
          >> ${logDir}/browser.stdout \
          2>> ${logDir}/browser.stderr \
          || echo "$(date -Iseconds) exit $?" >> ${logDir}/supervisor.log
        sleep 2
      done
    '';
  };

  jail = oci.mkOciJail {
    name = "hermes-browser";
    description = "Hermes persistent browser (OCI, official-container-shaped)";
    user = agent.user;
    cfg = bctr;
    volumes = [
      oci.nixStoreBind
      "${workspaceDir}:${workspaceDir}"
      "${profileDir}:${profileDir}"
      "${cookiesDir}:${cookiesDir}"
      "${logDir}:${logDir}"
      "${gateHome}:${gateHome}"
    ];
    command = [ "${supervisor}/bin/hermes-browser-supervisor" ];
    identity = {
      package = "${supervisor}";
    };
  };
in
{
  config = mkIf (pnp.enable && cfg.enable && bctr.enable) {
    virtualisation.docker.enable = mkIf jail.dockerEnable (mkDefault true);

    systemd.services.hermes-browser = jail.unit;
  };
}
