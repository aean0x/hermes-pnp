# Official-container-shaped WebUI jail. mkForce over official ExecStart.
#
# ubuntu + /nix/store:ro + slim entrypoint. Not ghcr.io/nesquena/hermes-webui.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  inherit (lib) mkDefault mkForce mkIf optional optionalAttrs;

  pnp = config.services.hermesPnP;
  agent = config.services.hermes-agent;
  webui = config.services.hermes-webui;
  wctr = pnp.webui.container;
  pairing = pnp.enable && pnp.webui.enable;

  oci = import ../../lib { inherit pkgs lib; };

  remappedHome = oci.remapStatePath {
    inherit (agent) stateDir;
    path = webui.hermesHome;
  };

  # NixOS /etc/ssl/certs is a symlink farm into /etc/static. Binding
  # only /etc/ssl leaves ca-certificates.crt dangling in Ubuntu.
  nixosCaBinds = [
    "/etc/ssl:/etc/ssl:ro"
    "/etc/static:/etc/static:ro"
  ];
  gitconfigBind = "/etc/gitconfig:/etc/gitconfig:ro";

  inferredPython =
    if webui.agent.python != null then
      webui.agent.python
    else if webui.agent.package != null && webui.agent.package.passthru ? hermesVenv then
      "${webui.agent.package.passthru.hermesVenv}/bin/python3"
    else
      null;

  inferredAgentDir =
    if webui.agent.dir != null then
      webui.agent.dir
    else if webui.agent.package != null && webui.agent.package.passthru ? hermesAgentDir then
      webui.agent.package.passthru.hermesAgentDir
    else
      null;

  extraEnv =
    {
      HERMES_WEBUI_HOST = webui.host;
      HERMES_WEBUI_PORT = toString webui.port;
      HERMES_WEBUI_STATE_DIR = webui.stateDir;
      HOME = oci.containerHome;
    }
    // optionalAttrs (webui.hermesHome != null) {
      HERMES_HOME = remappedHome;
    }
    // optionalAttrs (inferredAgentDir != null) {
      HERMES_WEBUI_AGENT_DIR = inferredAgentDir;
    }
    // optionalAttrs (inferredPython != null) {
      HERMES_WEBUI_PYTHON = inferredPython;
    }
    // webui.extraEnvironment
    // optionalAttrs (webui.agent.package != null) {
      PATH = "${webui.agent.package}/bin:${
        webui.extraEnvironment.PATH or "${oci.containerData}/toolbox/bin:/usr/bin:/bin"
      }";
    };

  jail = oci.mkOciJail {
    name = "hermes-webui";
    description = "Hermes Web UI (OCI, official-container-shaped)";
    user = webui.user;
    cfg = wctr;
    volumes = [
      oci.nixStoreBind
      (oci.stateDirBind agent.stateDir)
      (oci.homeBind agent.stateDir)
      "${webui.stateDir}:${webui.stateDir}"
    ] ++ nixosCaBinds ++ [ gitconfigBind ];
    inherit extraEnv;
    envFiles = webui.environmentFiles;
    command = [ "${webui.package}/bin/hermes-webui" ];
    identity = {
      package = "${webui.package}";
    };
    after = [ "hermes-agent.service" ]
      ++ optional pnp.gbrain.enable "gbrain-mcp-http.service"
      ++ optional pnp.mcpProxy.enable "mcp-proxy.service";
    wants = [ "hermes-agent.service" ]
      ++ optional pnp.gbrain.enable "gbrain-mcp-http.service"
      ++ optional pnp.mcpProxy.enable "mcp-proxy.service";
  };
in
{
  config = mkIf (pairing && wctr.enable) {
    virtualisation.docker.enable = mkIf jail.dockerEnable (mkDefault true);

    systemd.tmpfiles.rules = [
      "d ${webui.stateDir} 0700 ${webui.user} ${webui.group} - -"
      "d ${agent.stateDir}/home 0750 ${webui.user} ${webui.group} - -"
    ];

    systemd.services.hermes-webui = mkForce jail.unit;
  };
}
