# Official hermes-webui pairing opinions.
#
# Official services.hermes-webui has no container.* (unlike
# services.hermes-agent.container). Host harden and OCI jail live in
# host.nix / container.nix.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  inherit (lib) mkDefault mkIf mkOption types optionalAttrs;

  pnp = config.services.hermesPnP;
  agent = config.services.hermes-agent;
  pairing = pnp.enable && pnp.webui.enable;
  extensionDir = pnp.pluginInstall.webuiExtensionDir;
  wctr = pnp.webui.container;

  oci = import ../_lib.nix { inherit pkgs lib; };
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
        Run official hermes-webui inside an OCI container (same
        docker create --network=host + /nix/store pattern as
        services.hermes-agent.container). The WebUI process and any
        terminal it spawns cannot see /etc/nixos. Defaults on when
        hermesPnP.container.enable is set. extraVolumes is independent
        of services.hermes-agent.container.extraVolumes.
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

    services.hermesPnP.webui.container = oci.followComposerContainer pnp;

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
  };
}
