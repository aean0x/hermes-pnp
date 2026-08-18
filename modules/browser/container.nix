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
    ;

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

      # --no-sandbox: the container is the jail.
      ${chromiumExec}
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
