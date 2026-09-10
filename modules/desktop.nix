# Native Desktop pairing. Official backend.mode is blocked in the
# jail, so this mode refuses container. The GUI launcher never reads
# the shell profile: wrap HERMES_HOME + remote URL into the binary,
# read the session token at start (never --set; that lands in the
# store). Same contract as official Home Manager desktop.
#
# NixOS identity stays the hermes service user. desktop.users are
# GUI logins added to that group. Do not flip services.hermes-agent.user
# to the login. Personal/laptop identity is homeManagerModules.default.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  inherit (lib)
    mkDefault
    mkForce
    mkIf
    mkOption
    types
    ;

  pnp = config.services.hermesPnP;
  agent = config.services.hermes-agent;
  cfg = pnp.desktop;

  hermesHome = "${agent.stateDir}/.hermes";
  remoteUrl = "http://${cfg.host}:${toString cfg.port}";

  tokenFile = cfg.sessionTokenFile;

  desktopEnvironment = {
    HERMES_HOME = hermesHome;
    HERMES_MANAGED = "true";
    HERMES_DESKTOP_REMOTE_URL = remoteUrl;
    HERMES_DESKTOP_HERMES = "${agent.package}/bin/hermes";
  };

  desktopRun = [
    ''
      if [ -r ${lib.escapeShellArg tokenFile} ]; then
        HERMES_DESKTOP_REMOTE_TOKEN="$(tr -d '\r\n' < ${lib.escapeShellArg tokenFile})"
        export HERMES_DESKTOP_REMOTE_TOKEN
      else
        echo "hermes-desktop: cannot read the session token at ${tokenFile}." >&2
        echo "hermes-desktop: the application starts its own backend instead of hermes-backend." >&2
      fi
    ''
  ];

  # Official hermes-desktop is callPackage'd with extraEnv / extraRun.
  # A dummy or foreign package must expose the same override.
  desktopPackage = cfg.package.override {
    extraEnv = desktopEnvironment;
    extraRun = desktopRun;
  };
in
{
  options.services.hermesPnP.desktop = {
    enable = lib.mkEnableOption ''
      Hermes Desktop on this host. Turns off the composer container
      knob, starts official `hermes serve` (`hermes-backend`), and
      installs a wrapped launcher that attaches to that backend.
      Incompatible with `services.hermes-agent.container.enable`.
    '';

    users = mkOption {
      type = types.listOf types.str;
      default = [ ];
      example = [ "alice" ];
      description = ''
        GUI logins that may launch Desktop. Each is added to the
        hermes group so they can read `$HERMES_HOME` and the session
        token. Empty is rejected: a system-wide shortcut with no
        reader is how Desktop falls back to `~/.hermes`.
      '';
    };

    sessionTokenFile = mkOption {
      type = types.nullOr types.str;
      default = null;
      example = lib.literalExpression ''config.sops.secrets."hermes/desktop-token".path'';
      description = ''
        Path to the raw session token (one line). Forwarded to
        official `services.hermes-agent.backend.sessionTokenFile`.
        The backend and the launcher both read it. Owner hermes,
        group hermes, mode 0440. Do not use a Nix path literal.
      '';
    };

    package = mkOption {
      type = types.package;
      default = pnp.internal.officialDesktopPackageFor pkgs.stdenv.hostPlatform.system;
      defaultText = lib.literalExpression "hermes-agent.packages.\${system}.desktop";
      description = ''
        Official `hermes-desktop` package (must accept extraEnv /
        extraRun override). Default comes from the composer flake.
        Pass a dummy with that override in eval checks.
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

  options.services.hermesPnP.internal.officialDesktopPackageFor = mkOption {
    type = types.functionTo types.package;
    internal = true;
    default =
      system:
      throw "hermesPnP.desktop requires nixosModules.default (official desktop package not wired for ${system})";
    defaultText = lib.literalExpression "system: throw \"…\"";
    description = "system → official hermes-desktop package. Set by the composer flake.";
  };

  config = mkIf (pnp.enable && cfg.enable) {
    assertions = [
      {
        assertion = cfg.users != [ ];
        message = ''
          services.hermesPnP.desktop.users must list the GUI login.
          Without it Desktop cannot read the managed HERMES_HOME.
        '';
      }
      {
        assertion = cfg.sessionTokenFile != null && cfg.sessionTokenFile != "";
        message = ''
          services.hermesPnP.desktop.sessionTokenFile is required.
          Official backend mints a new token each start otherwise, and
          Desktop cannot attach.
        '';
      }
      {
        assertion = !(agent.container.enable or false);
        message = ''
          services.hermesPnP.desktop.enable is native-only.
          Official backend.mode is blocked when
          services.hermes-agent.container.enable is true.
          Drop the jail or drop desktop.
        '';
      }
      {
        assertion = cfg.host == "127.0.0.1" || cfg.host == "localhost" || cfg.host == "::1";
        message = ''
          services.hermesPnP.desktop.host must be loopback.
          A non-loopback bind is a consumer reverse-proxy problem,
          not composer pairing.
        '';
      }
    ];

    # Composer jail knob off. Official container.enable stays an
    # assertion (above) so a direct official jail still fails loud.
    services.hermesPnP.container.enable = mkForce false;

    services.hermes-agent = {
      addToSystemPackages = mkDefault true;
      backend.mode = mkDefault cfg.mode;
      backend.host = mkDefault cfg.host;
      backend.port = mkDefault cfg.port;
      backend.sessionTokenFile = tokenFile;
    };

    users.groups.${agent.group}.members = cfg.users;

    environment.systemPackages = [ desktopPackage ];
  };
}
