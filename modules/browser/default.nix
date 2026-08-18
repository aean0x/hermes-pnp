# Persistent CDP browser + agent-browser dashboard gate.
#
# Two modes:
# - host-native (default unless hermesPnP.container.enable): systemd
#   units as the hermes user, hardened. Brave is independently
#   supervised so a gate crash does not kill the warm profile.
# - container: one Ubuntu OCI container (same docker create --network=host
#   + /nix/store pattern as official hermes-agent.container). Mounts
#   workspace + profile + cookies + logs + gate state. Not hermes home,
#   not .hermes, not /etc. Xvfb + engine + gate share the container.
#
# Takeover: same local browser, two control planes.
#   Agent  = CDP 127.0.0.1:9222  (browser_* tools, unchanged)
#   Human  = agent-browser dashboard on listenAddress (default 127.0.0.1)
#            via Caddy. CDP screencast + input injection. No VNC, no
#            password, no framebuffer.
#
# agent-browser connect ATTACHES to the existing engine. It will launch
# its own Chrome if CDP is down — the gate refuses to connect until
# /json/version answers.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  inherit (lib)
    literalExpression
    mkIf
    mkMerge
    mkOption
    optionalAttrs
    optionals
    types
    ;

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
    gatePort
    listenAddr
    cdpUrl
    home
    gateHome
    agentBrowser
    gateUrl
    chromiumAliases
    hermesBrowserImportCookies
    hermesBrowserGate
    ;

  containerProfile = oci.remapStatePath {
    inherit (agent) stateDir;
    path = profileDir;
  };
in
{
  imports = [
    ../enable.nix
    ./host.nix
    ./container.nix
  ];

  options.services.hermesPnP.browser = {
    enable = mkOption {
      type = types.bool;
      default = true;
      description = ''
        Opinionated host browser: a persistent CDP browser (loopback
        :9222) + optional agent-browser dashboard for human captcha
        handoff. The native hermes browser_* tools attach to it via
        browser.cdp_url / BROWSER_CDP_URL. The dashboard is the phone
        gate (CDP screencast); there is no VNC.
      '';
    };

    package = mkOption {
      type = types.package;
      default = pkgs.chromium;
      defaultText = literalExpression "pkgs.chromium";
      description = ''
        Browser derivation. Swap for pkgs.brave (or any Chromium fork).
        engine follows package.meta.mainProgram unless you override it.
      '';
    };

    engine = mkOption {
      type = types.str;
      default = config.services.hermesPnP.browser.package.meta.mainProgram or "chromium";
      defaultText = literalExpression ''package.meta.mainProgram or "chromium"'';
      description = ''
        Binary name under package/bin and HERMES_BROWSER_ENGINE.
        Defaults to package.meta.mainProgram, so `package = pkgs.brave`
        is enough. Override only when the binary name differs.
      '';
    };

    cdpPort = mkOption {
      type = types.port;
      default = 9222;
      description = "Loopback CDP port.";
    };

    profileDir = mkOption {
      type = types.str;
      default = "${agent.stateDir}/browser-profile";
      defaultText = literalExpression ''"''${config.services.hermes-agent.stateDir}/browser-profile"'';
      description = "Sticky profile directory.";
    };

    cookiesDir = mkOption {
      type = types.str;
      default = "${agent.stateDir}/browser-cookies";
      defaultText = literalExpression ''"''${config.services.hermes-agent.stateDir}/browser-cookies"'';
      description = "Drop dir for Netscape / Playwright cookie files.";
    };

    workspaceDir = mkOption {
      type = types.str;
      default = "${agent.stateDir}/workspace";
      defaultText = literalExpression ''"''${config.services.hermes-agent.stateDir}/workspace"'';
      description = ''
        Host workspace bind-mounted into the browser container.
        Container mode mounts this plus profile/cookies/logs — not
        hermes home, not .hermes, not /etc.
      '';
    };

    logDir = mkOption {
      type = types.str;
      default = "${agent.stateDir}/browser-logs";
      defaultText = literalExpression ''"''${config.services.hermes-agent.stateDir}/browser-logs"'';
      description = "Browser / gate log dir.";
    };

    gate = {
      enable = mkOption {
        type = types.bool;
        default = true;
        description = "Phone / LAN human captcha handoff via the agent-browser dashboard.";
      };
      port = mkOption {
        type = types.port;
        default = 4848;
        description = "Dashboard port. Bound to listenAddress; not firewalled when loopback.";
      };
      listenAddress = mkOption {
        type = types.str;
        default = "127.0.0.1";
        description = ''
          Advertised dashboard bind. The binary itself listens on
          loopback. Expose via Caddy (same auth as the WebUI), not a
          LAN-open 4848.
        '';
      };
      publicUrl = mkOption {
        type = types.nullOr types.str;
        default = null;
        description = ''
          URL the agent relays for captcha handoff, e.g.
          https://browser.example.com/. When null, relays
          http://127.0.0.1:<port>/.
        '';
      };
    };

    container = oci.mkOciServiceOptions {
      extraOptions = [
        "--shm-size=2g"
        "--init"
      ];
      extraOptionsDescription = "Extra docker create args. Privilege-regain locks are always injected.";
      description = ''
        Run Xvfb + browser + agent-browser gate in one OCI container.
        Mounts workspace, profile, cookies, logs, gate state. Not hermes home.
        Defaults on when hermesPnP.container.enable is set.
      '';
    };

    extraArgs = mkOption {
      type = types.listOf types.str;
      default = [ ];
      description = "Extra chromium flags appended to the browser ExecStart.";
    };
  };

  config = mkMerge [
    (mkIf pnp.enable {
      services.hermesPnP.browser.container = oci.followComposerContainer pnp;
    })

    (mkIf (pnp.enable && cfg.enable) {
      assertions = [
        {
          assertion = cfg.engine != "";
          message = "services.hermesPnP.browser.engine is empty; set browser.package (engine follows mainProgram) or browser.engine.";
        }
      ];

      environment.systemPackages = [
        cfg.package
        chromiumAliases
        pkgs.xvfb
        agentBrowser
        hermesBrowserImportCookies
        hermesBrowserGate
        (pkgs.writeShellScriptBin "hermes-browser-status" ''
          set -euo pipefail
          echo "engine:   ${cfg.engine}"
          echo "mode:     ${if bctr.enable then "container" else "host-native"}"
          echo "profile:  ${profileDir}"
          echo "workspace:${workspaceDir}"
          echo "cookies:  ${cookiesDir}  (drop Netscape/JSON; import with hermes-browser-import-cookies)"
          echo "cdp:      ${cdpUrl}"
          echo "gate:     ${gateUrl}"
          if ${pkgs.curl}/bin/curl -fsS --max-time 2 "${cdpUrl}/json/version"; then
            echo
            echo "cdp:      up"
          else
            echo "cdp:      down"
          fi
          if ${pkgs.curl}/bin/curl -fsS --max-time 2 -o /dev/null "http://127.0.0.1:${toString gatePort}/"; then
            echo "gate:     up"
          else
            echo "gate:     down"
          fi
          ${pkgs.systemd}/bin/systemctl is-active hermes-browser.service hermes-browser-gate.service || true
        '')
      ];

      # Dashboard binds loopback. Only punch the firewall if someone
      # explicitly binds off-loopback (not recommended; Caddy instead).
      networking.firewall.allowedTCPPorts =
        optionals (cfg.gate.enable && listenAddr != "127.0.0.1" && listenAddr != "localhost") [ gatePort ];

      systemd.tmpfiles.rules = [
        "d ${profileDir} 0750 ${agent.user} ${agent.group} - -"
        "d ${cookiesDir} 0750 ${agent.user} ${agent.group} - -"
        "d ${logDir} 0750 ${agent.user} ${agent.group} - -"
        "d ${workspaceDir} 2770 ${agent.user} ${agent.group} - -"
        "d ${gateHome} 0750 ${agent.user} ${agent.group} - -"
        "d ${home} 0750 ${agent.user} ${agent.group} - -"
      ];

      services.hermes-agent = {
        environment = {
          BROWSER_CDP_URL = cdpUrl;
          BU_CDP_URL = cdpUrl;
          HERMES_BROWSER_CDP_URL = cdpUrl;
          HERMES_BROWSER_GATE_URL = gateUrl;
          HERMES_BROWSER_GATE_PORT = toString gatePort;
          HERMES_BROWSER_ENGINE = cfg.engine;
        }
        // optionalAttrs (!agent.container.enable) {
          HERMES_BROWSER_PROFILE = profileDir;
        };
        settings.browser = {
          cdp_url = cdpUrl;
        };
        container.extraOptions = mkIf agent.container.enable (
          oci.mkDockerEnv { HERMES_BROWSER_PROFILE = containerProfile; }
        );
      };

      # Official setup already persists environment{} into .env.
      # Delete-only: dead VNC keys, and host profile if the jail remap
      # must not leak back into the file the container also reads.
      system.activationScripts.hermes-browser-dotenv = lib.stringAfter [ "hermes-agent-setup" ] ''
        env_file=${agent.stateDir}/.hermes/.env
        if [ -f "$env_file" ]; then
          ${pkgs.gnused}/bin/sed -i \
            -e '/^HERMES_BROWSER_NOVNC_URL=/d' \
            -e '/^HERMES_BROWSER_NOVNC_PORT=/d' \
            -e '/^HERMES_BROWSER_NOVNC_PASSWORD=/d' \
            -e '/^HERMES_BROWSER_VNC_PASSWORD=/d' \
            ${lib.optionalString agent.container.enable "-e '/^HERMES_BROWSER_PROFILE=/d'"} \
            "$env_file" || true
        fi
      '';
    })
  ];
}
