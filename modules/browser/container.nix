# OCI mode: one container, one unit. Gate runs inside next to Brave.
# No /etc binds — workspace/profile/cookies/logs/gate only.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  inherit (lib) mkDefault mkIf;

  oci = import ../_lib.nix { inherit pkgs lib; };
  shared = import ./shared.nix { inherit config lib pkgs; };
  inherit (shared)
    pnp
    agent
    cfg
    bctr
    profileDir
    cookiesDir
    logDir
    workspaceDir
    gateHome
    display
    displayNum
    cdpAddr
    cdpPort
    browserBin
    hermesBrowserGate
    ;

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
    extraEnv = { };
    envFiles = [ ];
    command = [ "${supervisor}/bin/hermes-browser-supervisor" ];
    identityFile = "${profileDir}/.oci-identity";
    identity = {
      package = "${supervisor}";
    };
    after = [ "hermes-browser-env.service" ];
    wants = [ "hermes-browser-env.service" ];
    requiresDocker = false;
  };
in
{
  config = mkIf (pnp.enable && cfg.enable && bctr.enable) {
    virtualisation.docker.enable = mkIf jail.dockerEnable (mkDefault true);

    systemd.services.hermes-browser = jail.unit;
  };
}
