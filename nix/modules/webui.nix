# Official hermes-webui pairing + optional OCI sandbox.
#
# Official services.hermes-webui has no container.* (unlike
# services.hermes-agent.container). When webui.container.enable is on we
# replace the host-native systemd ExecStart with the official agent
# container pattern (docker create --network=host, /nix/store bind,
# slim entrypoint that drops to HERMES_UID). Upstream-shaped: this
# block can be lifted into nesquena/hermes-webui later.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  inherit (lib) mkDefault mkForce mkIf mkMerge optionalAttrs;

  pnp = config.services.hermesPnP;
  agent = config.services.hermes-agent;
  webui = config.services.hermes-webui;
  wctr = pnp.webui.container;
  pairing = pnp.enable && pnp.webui.enable;
  extensionDir = pnp.pluginInstall.webuiExtensionDir;
  oci = import ../lib/oci-container.nix { inherit pkgs lib; };

  remappedHome =
    if webui.hermesHome != null && lib.hasPrefix agent.stateDir webui.hermesHome
    then "/data" + lib.removePrefix agent.stateDir webui.hermesHome
    else webui.hermesHome;

  entrypoint = oci.mkSlimEntrypoint "hermes-webui";

  extraEnv =
    {
      HERMES_WEBUI_HOST = webui.host;
      HERMES_WEBUI_PORT = toString webui.port;
      HERMES_WEBUI_STATE_DIR = webui.stateDir;
    }
    // optionalAttrs (webui.hermesHome != null) {
      HERMES_HOME = if wctr.enable then remappedHome else webui.hermesHome;
    }
    // optionalAttrs (webui.agent.dir != null) {
      HERMES_WEBUI_AGENT_DIR = webui.agent.dir;
    }
    // optionalAttrs (webui.agent.python != null) {
      HERMES_WEBUI_PYTHON = webui.agent.python;
    }
    // webui.extraEnvironment;

  unit = oci.mkUnitScripts {
    backend = wctr.backend;
    containerName = "hermes-webui";
    image = wctr.image;
    user = webui.user;
    volumes = [
      "/nix/store:/nix/store:ro"
      "${agent.stateDir}:/data"
      "${agent.stateDir}/home:/home/hermes"
      "${webui.stateDir}:${webui.stateDir}"
      "/etc/ssl:/etc/ssl:ro"
      "/etc/gitconfig:/etc/gitconfig:ro"
    ] ++ wctr.extraVolumes;
    extraEnv = extraEnv;
    extraOptions = wctr.extraOptions;
    envFiles = webui.environmentFiles;
    inherit entrypoint;
    command = [ "${webui.package}/bin/hermes-webui" ];
    identityFile = "${webui.stateDir}/.oci-identity";
    identity = {
      inherit (wctr) image extraVolumes extraOptions;
      inherit extraEnv entrypoint;
      envFiles = webui.environmentFiles;
      package = "${webui.package}";
    };
  };
in
{
  config = mkMerge [
    (mkIf pairing {
      assertions = [
        {
          assertion = !(lib.elem "model-router" pnp.plugins) || extensionDir != null;
          message = "hermes-webui needs hermesPnP model-router plugin for HERMES_WEBUI_EXTENSION_DIR.";
        }
      ];

      services.hermesPnP.webui.container.enable = mkDefault pnp.container.enable;
      services.hermesPnP.webui.container.backend = mkDefault pnp.container.backend;
      services.hermesPnP.webui.container.image = mkDefault pnp.container.image;

      services.hermes-webui = {
        enable = mkDefault true;
        user = mkDefault agent.user;
        group = mkDefault agent.group;
        agent.package = mkDefault agent.package;
        hermesHome = mkDefault "${agent.stateDir}/.hermes";
        host = mkDefault "127.0.0.1";
        port = mkDefault 8787;
        openFirewall = mkDefault false;
        environmentFiles = mkDefault agent.environmentFiles;
        extraEnvironment =
          {
            HERMES_WEBUI_TRUST_FORWARDED_PROTO = mkDefault "true";
            HERMES_WEBUI_SECURE = mkDefault "true";
          }
          // optionalAttrs (extensionDir != null) {
            HERMES_WEBUI_EXTENSION_DIR = toString extensionDir;
            HERMES_WEBUI_EXTENSION_MANIFEST = "extensions.json";
          }
          // optionalAttrs pnp.toolbox.enable {
            PATH = if wctr.enable then pnp.toolbox.containerPath else pnp.toolbox.hostPath;
          };
      };
    })

    (mkIf (pairing && !wctr.enable) {
      systemd.services.hermes-webui = {
        after = [ "hermes-agent.service" ];
        wants = [ "hermes-agent.service" ];
        serviceConfig = {
          NoNewPrivileges = true;
          CapabilityBoundingSet = [ ];
          ProtectKernelTunables = true;
          ProtectKernelModules = true;
          ProtectKernelLogs = true;
          ProtectControlGroups = true;
          RestrictSUIDSGID = true;
        };
      };
    })

    (mkIf (pairing && wctr.enable) {
      virtualisation.docker.enable = mkIf (wctr.backend == "docker") (mkDefault true);

      systemd.tmpfiles.rules = [
        "d ${webui.stateDir} 0700 ${webui.user} ${webui.group} - -"
        "d ${agent.stateDir}/home 0755 ${webui.user} ${webui.group} - -"
      ];

      systemd.services.hermes-webui = mkForce {
        description = "Hermes Web UI (OCI, official-container-shaped)";
        after = [
          "network-online.target"
          "docker.service"
          "hermes-agent.service"
        ];
        wants = [
          "network-online.target"
          "hermes-agent.service"
        ];
        requires = [ "docker.service" ];
        wantedBy = [ "multi-user.target" ];
        preStart = unit.preStart;
        script = unit.script;
        preStop = unit.preStop;
        path = [ pkgs.docker pkgs.coreutils ];
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
