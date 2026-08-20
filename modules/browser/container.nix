# One jail: Xvfb + engine + gate. Workspace/profile/cookies/logs/gate only.
{
  config,
  lib,
  options,
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
    fontconfigFile
    ;

  # Host /etc/static is a symlink; Docker bind-mounts that as an empty
  # dir. The etc entry is the resolved store path of the system bundle
  # (nss-cacert plus security.pki extras). /nix/store is already bound.
  caBundle = config.environment.etc."ssl/certs/ca-certificates.crt".source;
  caBinds = [
    "${caBundle}:/etc/ssl/certs/ca-certificates.crt:ro"
    "${caBundle}:/etc/ssl/cert.pem:ro"
  ];

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
      export FONTCONFIG_FILE=${fontconfigFile}
      mkdir -p "$HOME" "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME" /tmp/.X11-unix ${profileDir} ${cookiesDir} ${logDir} ${gateHome}
      touch "$XAUTHORITY"
      chmod 600 "$XAUTHORITY"

      ${launchXvfb}
      ${waitForDisplay}

      ${
        if cfg.gate.enable then
          ''
            hermes-browser-gate &
          ''
        else
          ""
      }

      # Restart the engine in-process. Xvfb and the gate stay up.
      # Keep Chromium session-restore; extraArgs hide the crash bubble.
      while true; do
        echo "$(date -Iseconds) start" >> ${logDir}/supervisor.log
        # chromiumExec is a multi-line command; wrap so the log redirects bind.
        {
          ${chromiumExec}
        } >>${logDir}/browser.stdout 2>>${logDir}/browser.stderr \
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
    network = oci.agentContainerNetwork options config;
    publish = bctr.publish;
    volumes = [
      oci.nixStoreBind
      "${workspaceDir}:${workspaceDir}"
      "${profileDir}:${profileDir}"
      "${cookiesDir}:${cookiesDir}"
      "${logDir}:${logDir}"
      "${gateHome}:${gateHome}"
    ]
    ++ caBinds;
    extraEnv = {
      FONTCONFIG_FILE = fontconfigFile;
      SSL_CERT_FILE = toString caBundle;
      NIX_SSL_CERT_FILE = toString caBundle;
      CURL_CA_BUNDLE = toString caBundle;
    };
    command = [ "${supervisor}/bin/hermes-browser-supervisor" ];
    identity = {
      package = "${supervisor}";
      inherit fontconfigFile caBundle;
    };
  };
in
{
  config = mkIf (pnp.enable && cfg.enable && bctr.enable) {
    virtualisation.docker.enable = mkIf jail.dockerEnable (mkDefault true);

    systemd.services.hermes-browser = jail.unit;
  };
}
