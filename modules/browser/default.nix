# Persistent CDP browser + agent-browser dashboard.
# Host-native: separate engine and gate units as the hermes user.
# Container: one jail (workspace, profile, cookies, logs, gate).
# Agent attaches at 127.0.0.1:9222. Humans use the dashboard
# (listenAddress, default 127.0.0.1) via Caddy.
# Gate waits for /json/version before `agent-browser connect`.
# Chromium --remote-allow-origins takes HTTP origins, not CIDR.
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
  profileImport = cfg.profileImport;

  # Whitelist filter for the auth-state copy: only Local State plus
  # <profile>/{Cookies,Network/Cookies,Login Data,Preferences}. Cache,
  # GPUCache, Singleton* locks and sqlite -wal/-shm sidecars are never
  # traversed, so the store path stays a few hundred KB and a running
  # source browser cannot smuggle a stale WAL into the seed.
  authStateFilter = src: profileName: path: type:
    let
      rel =
        let r = lib.removePrefix (toString src) (toString path);
        in if r == "" then "" else lib.removePrefix "/" r;
      dirs = [ profileName "${profileName}/Network" ];
      files = [
        "Local State"
        "${profileName}/Cookies"
        "${profileName}/Network/Cookies"
        "${profileName}/Login Data"
        "${profileName}/Preferences"
      ];
    in
    rel == ""
    || (type == "directory" && lib.elem rel dirs)
    || (type == "regular" && lib.elem rel files);

  # The filtered store copy. `source` is read from the local filesystem
  # at eval time, which is impure — `nixos-rebuild switch --impure`
  # (or pass a flake-input store path as source for a pure build).
  importedAuth =
    if profileImport.enable && profileImport.source != null then
      builtins.path {
        path = profileImport.source;
        name = "hermes-browser-auth";
        filter = authStateFilter profileImport.source profileImport.profileName;
      }
    else
      null;

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
        Persistent CDP browser on loopback :9222 plus an optional
        agent-browser dashboard. Hermes attaches via browser.cdp_url /
        BROWSER_CDP_URL.
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

    cdpAllowOrigins = mkOption {
      type = types.nullOr (types.listOf types.str);
      default = null;
      description = ''
        Chromium --remote-allow-origins. Each entry is an HTTP origin
        (scheme://host[:port]), not a CIDR — Chromium has no LAN-range
        form. null (default) is loopback CDP + dashboard, plus
        gate.publicUrl and a non-loopback listenAddress when set.
        Set [ "*" ] to allow any Origin (weaker).
      '';
      example = [
        "http://127.0.0.1:9222"
        "https://browser.example.com"
      ];
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

    profileImport = {
      enable = mkOption {
        type = types.bool;
        default = false;
        description = ''
          Seed the browser profile with auth state (cookies, saved
          logins, preferences) copied from a Chromium-family user-data
          dir on the build machine. One-shot: the seed is applied only
          when the profile is empty (or overwrite=true), so gate logins
          you do later stay sticky.
        '';
      };

      source = mkOption {
        type = types.nullOr types.path;
        default = null;
        description = ''
          Absolute path to the browser user-data dir to import, e.g.
          ~/.config/BraveSoftware/Brave-Browser (the dir that contains
          Local State and Default/). Must exist on the machine that
          evaluates the configuration (usually the build machine).

          Reading an absolute path is an impure operation: build with
          `nixos-rebuild switch --impure`, or pass a store path (e.g.
          from a flake input) for a pure build. Only the auth files are
          copied — Cache, GPUCache, Singleton* locks and sqlite WAL/SHM
          sidecars are filtered out — so the store copy is a few
          hundred KB, not the whole profile.

          The copied cookies/logins are Nix store content: readable by
          local users on the build and target machines.
        '';
        example = "/home/alice/.config/BraveSoftware/Brave-Browser";
      };

      profileName = mkOption {
        type = types.str;
        default = "Default";
        description = "Profile dir inside source (Default, Profile 1, ...).";
      };

      overwrite = mkOption {
        type = types.bool;
        default = false;
        description = ''
          Replace an existing profile with the imported auth state.
          Default keeps the sticky profile (gate logins, session state)
          and only seeds when the profile is empty.
        '';
      };
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
          Dashboard bind. Passed as --host when the binary supports it.
          Default loopback; expose LAN through Caddy (same auth as the
          WebUI). 0.0.0.0 is a raw LAN bind with no dashboard auth.
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
      extraOptionsDescription = "Extra docker create args. Privilege-regain locks and --init are always injected.";
      description = ''
        Run Xvfb + browser + gate in one OCI jail (workspace, profile,
        cookies, logs, gate). Defaults on when the official agent
        container is on. Network follows official container.network.
        Override memory / cpus / shmSize here — do not mkForce
        extraOptions just to set RAM.
      '';
    };

    extraArgs = mkOption {
      type = types.listOf types.str;
      default = [
        "--disable-session-crashed-bubble"
      ];
      description = "Extra chromium flags appended to the browser ExecStart.";
    };

    maxTabs = mkOption {
      type = types.nullOr types.ints.positive;
      default = 5;
      description = ''
        Cap live page targets via --renderer-process-limit. Null
        disables. Agent browsers do not need a human tab pile.
      '';
    };
  };

  options.services.hermesPnP = {
    internal.browser.importedAuth = mkOption {
      type = types.nullOr types.path;
      internal = true;
      default = null;
      description = "Filtered store copy of the imported auth state (profileImport).";
    };
  };

  config = mkMerge [
    (mkIf pnp.enable {
      services.hermesPnP.browser.container = oci.followAgentContainer agent;
    })

    (mkIf (pnp.enable && cfg.enable && cfg.maxTabs != null) {
      services.hermesPnP.browser.extraArgs = [
        "--renderer-process-limit=${toString cfg.maxTabs}"
      ];
    })

    (mkIf (pnp.enable && cfg.enable) {
      assertions = [
        {
          assertion = cfg.engine != "";
          message = "services.hermesPnP.browser.engine is empty; set browser.package (engine follows mainProgram) or browser.engine.";
        }
        {
          assertion = !profileImport.enable || profileImport.source != null;
          message = "services.hermesPnP.browser.profileImport.enable = true requires profileImport.source (a Chromium user-data dir on the build machine).";
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
          ${
            if importedAuth != null then
              ''
                echo "import:   ${toString profileImport.source} (${if profileImport.overwrite then "overwrite" else "seed-only"})"
                if [ -e "${profileDir}/.hermes-profile-imported" ]; then
                  echo "import:   applied -> $(cat "${profileDir}/.hermes-profile-imported")"
                else
                  echo "import:   not applied yet"
                fi
              ''
            else
              ""
          }
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
          ${
            if bctr.enable then
              ''
                ${pkgs.systemd}/bin/systemctl is-active hermes-browser.service || true
              ''
            else
              ''
                ${pkgs.systemd}/bin/systemctl is-active hermes-browser.service hermes-browser-gate.service || true
              ''
          }
        '')
      ];

      # Loopback dashboard. Open the firewall only for a non-loopback bind.
      networking.firewall.allowedTCPPorts = optionals (
        cfg.gate.enable && listenAddr != "127.0.0.1" && listenAddr != "localhost"
      ) [ gatePort ];

      systemd.tmpfiles.rules = [
        "d ${profileDir} 0750 ${agent.user} ${agent.group} - -"
        "d ${cookiesDir} 0750 ${agent.user} ${agent.group} - -"
        "d ${logDir} 0750 ${agent.user} ${agent.group} - -"
        "d ${workspaceDir} 2770 ${agent.user} ${agent.group} - -"
        "d ${gateHome} 0750 ${agent.user} ${agent.group} - -"
        "d ${home} 0750 ${agent.user} ${agent.group} - -"
      ];

      # Seed the sticky profile from the filtered store copy, once.
      # Marker + empty-profile checks keep gate-added logins sticky;
      # overwrite=true replaces the whole profile dir.
      services.hermesPnP.internal.browser.importedAuth = importedAuth;
      systemd.services.hermes-browser-profile-import = mkIf (importedAuth != null) {
        description = "Seed Hermes browser profile with imported auth state";
        wantedBy = [ "multi-user.target" ];
        before = [ "hermes-browser.service" ];
        serviceConfig = {
          Type = "oneshot";
          RemainAfterExit = true;
        };
        script = ''
          set -euo pipefail
          src="${importedAuth}"
          marker="${profileDir}/.hermes-profile-imported"
          overwrite=${if profileImport.overwrite then "1" else "0"}
          if [ -e "$marker" ]; then
            echo "profile import: already applied ($(cat "$marker"))"
            exit 0
          fi
          existing="$(${pkgs.findutils}/bin/find "${profileDir}" -mindepth 1 -maxdepth 1 ! -name '.hermes-profile-imported' -print -quit 2>/dev/null || true)"
          if [ -n "$existing" ] && [ "$overwrite" != "1" ]; then
            echo "profile import: ${profileDir} already has state; skipping (overwrite=false)"
            exit 0
          fi
          if [ "$overwrite" = "1" ]; then
            rm -rf "${profileDir}"
          fi
          mkdir -p "${profileDir}"
          ${pkgs.coreutils}/bin/cp -a --no-preserve=ownership,mode "''${src}/." "${profileDir}/"
          chown -R ${agent.user}:${agent.group} "${profileDir}"
          chmod -R u+rwX,go-rwx "${profileDir}"
          printf '%s\n' "${importedAuth}" > "$marker"
          chown ${agent.user}:${agent.group} "$marker"
          echo "profile import: seeded ${profileDir} from ${importedAuth}"
        '';
      };

      services.hermes-agent = {
        environment = {
          BROWSER_CDP_URL = cdpUrl;
          BU_CDP_URL = cdpUrl;
          HERMES_BROWSER_CDP_URL = cdpUrl;
          HERMES_BROWSER_GATE_URL = gateUrl;
          HERMES_BROWSER_GATE_PORT = toString gatePort;
          HERMES_BROWSER_ENGINE = cfg.engine;
          AGENT_BROWSER_ENGINE = cfg.engine;
        }
        // optionalAttrs (!agent.container.enable) {
          HERMES_BROWSER_PROFILE = profileDir;
        };
        settings.browser = {
          cdp_url = cdpUrl;
          engine = cfg.engine;
        };
        container.extraOptions = mkIf agent.container.enable (
          oci.mkDockerEnv { HERMES_BROWSER_PROFILE = containerProfile; }
        );
      };

      # Official activation writes environment{} into .env. In jail mode
      # drop a host HERMES_BROWSER_PROFILE so the remapped --env wins.
      system.activationScripts.hermes-browser-dotenv = mkIf agent.container.enable (
        lib.stringAfter [ "hermes-agent-setup" ] ''
          env_file=${agent.stateDir}/.hermes/.env
          if [ -f "$env_file" ]; then
            ${pkgs.gnused}/bin/sed -i -e '/^HERMES_BROWSER_PROFILE=/d' "$env_file" || true
          fi
        ''
      );
    })
  ];
}
