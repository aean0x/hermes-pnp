# Official hermes-webui + pairing defaults. mkDefault only.
# Gated on hermesPnP.enable && hermesPnP.webui.enable.
{
  config,
  lib,
  ...
}:

let
  inherit (lib) mkDefault mkIf optionalAttrs;

  pnp = config.services.hermesPnP;
  agent = config.services.hermes-agent;
  pairing = pnp.enable && pnp.webui.enable;
  extensionDir = pnp.pluginInstall.webuiExtensionDir;
in
{
  config = mkIf pairing {
    assertions = [
      {
        assertion = !(lib.elem "model-router" pnp.plugins) || extensionDir != null;
        message = "hermes-webui needs hermesPnP model-router plugin for HERMES_WEBUI_EXTENSION_DIR.";
      }
    ];

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
          # Loopback bind is meant to sit behind a reverse proxy.
          HERMES_WEBUI_TRUST_FORWARDED_PROTO = mkDefault "true";
          HERMES_WEBUI_SECURE = mkDefault "true";
        }
        // optionalAttrs (extensionDir != null) {
          HERMES_WEBUI_EXTENSION_DIR = toString extensionDir;
          HERMES_WEBUI_EXTENSION_MANIFEST = "extensions.json";
        }
        // optionalAttrs pnp.toolbox.enable {
          PATH = pnp.toolbox.hostPath;
        };
    };

    systemd.services.hermes-webui = {
      after = [ "hermes-agent.service" ];
      wants = [ "hermes-agent.service" ];
    };
  };
}
