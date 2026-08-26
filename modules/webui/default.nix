# Pair official hermes-webui. Jail and host harden live in
# container.nix / host.nix.
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
    mkOption
    types
    optionalAttrs
    ;

  pnp = config.services.hermesPnP;
  agent = config.services.hermes-agent;
  pairing = pnp.enable && pnp.webui.enable;
  extensionDir = pnp.pluginInstall.webuiExtensionDir;
  wctr = pnp.webui.container;

  oci = import ../../lib { inherit pkgs lib; };
in
{
  imports = [
    ./host.nix
    ./container.nix
  ];

  options.services.hermesPnP.webui = {
    enable = mkOption {
      type = types.bool;
      default = true;
      description = "When the composer is on, pair official WebUI. Set false for gateway-only.";
    };

    container = oci.mkOciServiceOptions {
      description = ''
        Run hermes-webui in an OCI jail (/nix/store:ro). Network
        follows official services.hermes-agent.container. Defaults on
        when the official agent container is on. The process and any
        terminal it spawns cannot see /etc/nixos.
      '';
    };
  };

  config = mkIf pairing {
    assertions = [
      {
        assertion = !(lib.elem "model-router" pnp.plugins) || extensionDir != null;
        message = "hermes-webui needs hermesPnP model-router plugin for HERMES_WEBUI_EXTENSION_DIR.";
      }
    ];

    services.hermesPnP.webui.container = oci.followAgentContainer agent;

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
      extraEnvironment = {
        HERMES_WEBUI_TRUST_FORWARDED_PROTO = mkDefault "true";
        HERMES_WEBUI_SECURE = mkDefault "true";
        # Caddy on this host. Override for a proxy whose peer is not loopback.
        HERMES_WEBUI_TRUSTED_PROXY_CIDRS = mkDefault "127.0.0.1/32,::1/128";
      }
      // optionalAttrs (extensionDir != null) {
        HERMES_WEBUI_EXTENSION_DIR = toString extensionDir;
        HERMES_WEBUI_EXTENSION_MANIFEST = "extensions.json";
      }
      // optionalAttrs (pnp.workspace != null) {
        HERMES_WEBUI_DEFAULT_WORKSPACE = mkDefault (
          if wctr.enable then
            oci.remapStatePath {
              inherit (agent) stateDir;
              path = pnp.workspace;
            }
          else
            pnp.workspace
        );
      }
      // optionalAttrs pnp.toolbox.enable {
        PATH = if wctr.enable then pnp.toolbox.containerPath else pnp.toolbox.hostPath;
        # Writable ~/.venv (activation). ubuntu:24.04 has no distro
        # python; the jail is read-only so apt cannot add one.
        HERMES_PYTHON =
          if wctr.enable then
            "${oci.containerHome}/.venv/bin/python3"
          else
            "${agent.stateDir}/home/.venv/bin/python3";
      };
    };
  };
}
