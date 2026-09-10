# Home Manager pairing. Gateway/backend stay official user units.
# Desktop is official programs.hermes-agent.desktop — wrap and
# HERMES_HOME stay upstream. Do not rewrite hermesHome.
{
  config,
  lib,
  options,
  ...
}:

let
  inherit (lib)
    mkDefault
    mkIf
    mkMerge
    mkOption
    types
    ;

  pnp = config.services.hermesPnP;
  cfg = pnp.desktop;
  isHomeManager = options ? home && options.home ? homeDirectory;
in
{
  options.services.hermesPnP.desktop = {
    enable = lib.mkEnableOption ''
      Hermes Desktop on this Home Manager user. Starts official
      hermes-backend (serve) and official programs.hermes-agent.desktop.
      Directories stay upstream (~/.hermes). Incompatible with
      services.hermesPnP.container.enable.
    '';

    sessionTokenFile = mkOption {
      type = types.nullOr types.str;
      default = null;
      example = lib.literalExpression ''config.sops.secrets."hermes/desktop-token".path'';
      description = ''
        Path to the raw session token (one line). Forwarded to
        official services.hermes-agent.backend.sessionTokenFile.
        Official Desktop reads it at start. Mode 0400, owner the
        login user. Do not use a Nix path literal.
      '';
    };

    host = mkOption {
      type = types.str;
      default = "127.0.0.1";
      description = "Official backend.host. Loopback only.";
    };

    port = mkOption {
      type = types.port;
      default = 9119;
      description = "Official backend.port.";
    };

    mode = mkOption {
      type = types.enum [
        "serve"
        "dashboard"
      ];
      default = "serve";
      description = ''
        Official backend.mode. serve is headless (Desktop). dashboard
        adds the browser admin UI on the same port.
      '';
    };
  };

  config = mkIf (isHomeManager && pnp.enable) (mkMerge [
    {
      assertions = [
        {
          assertion = !pnp.container.enable;
          message = ''
            services.hermesPnP.container.enable is NixOS-only.
            Home Manager has no container runtime.
          '';
        }
      ];

      programs.hermes-agent.enable = mkDefault true;
      services.hermes-agent.enable = mkDefault true;
      # Official HM splits the messaging gateway from enable.
      services.hermes-agent.gateway.enable = mkDefault true;
      services.hermes-agent.environmentFiles = pnp.environmentFiles;

      # Leave hermesHome and workingDirectory unset so upstream
      # defaults apply (~/.hermes, $HOME).
    }
    (mkIf (pnp.workspace != null) {
      services.hermes-agent.settings.terminal.cwd = pnp.workspace;
    })
    (mkIf cfg.enable {
      assertions = [
        {
          assertion = cfg.sessionTokenFile != null && cfg.sessionTokenFile != "";
          message = ''
            services.hermesPnP.desktop.sessionTokenFile is required.
            Official backend mints a new token each start otherwise, and
            Desktop cannot attach.
          '';
        }
        {
          assertion = cfg.host == "127.0.0.1" || cfg.host == "localhost" || cfg.host == "::1";
          message = ''
            services.hermesPnP.desktop.host must be loopback.
            A non-loopback bind is a reverse-proxy problem, not pairing.
            A hosted client that is not using this shortcut sets
            HERMES_DESKTOP_REMOTE_URL on the official module.
          '';
        }
      ];

      programs.hermes-agent.desktop.enable = mkDefault true;
      services.hermes-agent.backend.mode = mkDefault cfg.mode;
      services.hermes-agent.backend.host = mkDefault cfg.host;
      services.hermes-agent.backend.port = mkDefault cfg.port;
      services.hermes-agent.backend.sessionTokenFile = cfg.sessionTokenFile;
    })
  ]);
}
