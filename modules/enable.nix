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
      example = lib.literalExpression ''[ config.sops.templates.hermesEnv.path ]'';
    };

    container = {
      enable = mkOption {
        type = types.bool;
        default = false;
        description = ''
          Turn on official services.hermes-agent.container (Ubuntu image,
          host network). Toolbox /data remaps assume this when the
          composer is on. Extra volumes and RAM flags stay official
          container.extraVolumes / extraOptions.
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
  };
}
