# Composer master switch, secrets drop-in, and the shared container knob.
{
  config,
  lib,
  ...
}:

let
  inherit (lib) mkEnableOption mkOption types;
in
{
  options.services.hermesPnP = {
    enable = mkEnableOption ''
      composer opinions (WebUI pairing, share env, toolbox, browser).
      Off (default): library path — plugins + mcp-proxy only.
    '';

    environmentFiles = mkOption {
      type = types.listOf types.path;
      default = [ ];
      description = ''
        Secrets drop-in. Declare the rendered env file here; the composer
        forwards it to services.hermes-agent.environmentFiles (WebUI
        inherits that list). Prefer a sops.templates path. Key list:
        docs/hermes.env.example.
      '';
      example = lib.literalExpression "[ config.sops.templates.hermesEnv.path ]";
    };

    container = {
      enable = mkOption {
        type = types.bool;
        default = false;
        description = ''
          Convenience alias: mkDefault official
          services.hermes-agent.container.enable (and backend/image).
          WebUI/browser jails follow the official agent container, not
          this knob. Extra volumes and RAM flags stay official
          container.extraVolumes / extraOptions. Network follows
          official container.network when that option exists (else host).
        '';
      };

      backend = mkOption {
        type = types.str;
        default = "docker";
        description = "Official container.backend.";
      };

      image = mkOption {
        type = types.str;
        default = "ubuntu:24.04";
        description = "Official container.image.";
      };
    };

    workspace = mkOption {
      type = types.nullOr types.str;
      default = null;
      defaultText = lib.literalExpression "null";
      description = ''
        Default workspace for both the gateway
        (services.hermes-agent.settings.terminal.cwd) and the WebUI
        (HERMES_WEBUI_DEFAULT_WORKSPACE). Host path. Remapped to the
        jail (/data, /home/hermes) only when that runtime is
        containerised; native keeps the host path. Unset (null): leave
        both runtimes on their official defaults.
      '';
    };
  };
}
