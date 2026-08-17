# User-facing composer options. Official hermes-agent / hermes-webui
# trees are not re-declared here.
#
# Comment a line to drop a thing. Import a plugin beside the catalog
# with one extraPlugins attr.
{ config
, lib
, pkgs
, ...
}:

let
  inherit (lib)
    literalExpression
    mkEnableOption
    mkOption
    types
    ;

  mkModelFields = defaults: {
    provider = mkOption {
      type = types.str;
      default = defaults.provider;
      description = "Provider id (official settings / plugin).";
    };
    model = mkOption {
      type = types.str;
      default = defaults.model;
      description = "Model id (official settings / plugin).";
    };
  };

  mkNamedModel =
    { provider
    , model
    , description
    ,
    }:
    mkOption {
      type = types.submodule { options = mkModelFields { inherit provider model; }; };
      default = { };
      inherit description;
      example = { inherit provider model; };
    };

  agent = config.services.hermes-agent;
in
{
  options.services.hermesPnP = {
    enable = mkEnableOption ''
      composer opinions (WebUI pairing, share env, toolbox, browser).
      Off (default): library path — plugins + mcp-proxy only.
    '';

    models = {
      low = mkNamedModel {
        provider = "deepseek";
        model = "deepseek-v4-flash";
        description = "Cheap helper. Seeds every auxiliary slot + unpinned cron.";
      };

      medium = mkNamedModel {
        provider = "deepseek";
        model = "deepseek-v4-pro";
        description = "Workhorse. Seeds delegation (delegate_task children).";
      };

      high = mkNamedModel {
        provider = "xai-oauth";
        model = "grok-4.6";
        description = "Session identity + voice. Seeds model.default, fallback, rest.";
      };
    };

    plugins = mkOption {
      type = types.listOf types.str;
      default = [ ];
      description = ''
        Catalog names to materialize. Composer on defaults to
        model-router, tool-call-coherency, secret-handoff (mkDefault).
      '';
      example = [
        "model-router"
        "tool-call-coherency"
        "secret-handoff"
        # "gbrain-retrieval-reflex"
        # "gbrain-memory-flush"
        # "projects-auto-commit"
      ];
    };

    extraPlugins = mkOption {
      type = types.attrsOf types.path;
      default = { };
      description = "Name → source tree beside the catalog.";
      example = literalExpression ''
        {
          # my-plugin = ./plugins/my-plugin;
        }
      '';
    };

    webui.enable = mkOption {
      type = types.bool;
      default = true;
      description = "When the composer is on, pair official WebUI. Set false for gateway-only.";
    };

    toolbox = {
      enable = mkOption {
        type = types.bool;
        default = true;
        description = ''
          Opinionated everyday CLI buildEnv (the "sauce"): a curated
          ~40-package toolkit + python3 + login PATH. Browser-specific
          aliases live in the browser module. Set false for a bare agent.
        '';
      };

      extraPackages = mkOption {
        type = types.listOf types.package;
        default = [ ];
        description = "Append-only packages added to the toolbox set.";
      };

      pythonPackages = mkOption {
        type = types.functionTo (types.listOf types.package);
        default = ps: with ps; [
          requests
          pyyaml
          toml
        ];
        defaultText = literalExpression "ps: with ps; [ requests pyyaml toml ]";
        description = "Python packages baked into the toolbox python3/python.";
      };

      # Read-only paths computed by toolbox.nix; host modules may reference
      # these instead of re-deriving PATH.
      toolboxDir = mkOption { type = types.str; readOnly = true; };
      containerToolboxDir = mkOption { type = types.str; readOnly = true; };
      hostPath = mkOption { type = types.str; readOnly = true; };
      containerPath = mkOption { type = types.str; readOnly = true; };
    };

    browser = {
      enable = mkOption {
        type = types.bool;
        default = true;
        description = ''
          Opinionated host browser: a persistent CDP browser (loopback
          :9222) + optional noVNC for human captcha handoff. The native
          hermes browser_* tools attach to it via browser.cdp_url /
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

      logDir = mkOption {
        type = types.str;
        default = "${agent.stateDir}/browser-logs";
        defaultText = literalExpression ''"''${config.services.hermes-agent.stateDir}/browser-logs"'';
        description = "Browser / x11vnc / noVNC log dir.";
      };

      noVNC = {
        enable = mkOption {
          type = types.bool;
          default = true;
          description = "Phone / LAN human captcha handoff via noVNC.";
        };
        port = mkOption {
          type = types.port;
          default = 6080;
          description = "noVNC web port (opened in the firewall).";
        };
        vncPort = mkOption {
          type = types.port;
          default = 5900;
          description = "Raw VNC port (kept closed unless opened explicitly).";
        };
      };

      extraArgs = mkOption {
        type = types.listOf types.str;
        default = [ ];
        description = "Extra chromium flags appended to the browser ExecStart.";
      };
    };

    gbrain = {
      enable = mkEnableOption ''
        Thin GBrain hook: mkDefault mcpServers.gbrain.url and export
        GBRAIN_MCP_URL / GBRAIN_TOKEN_FILE. Does not start gbrain serve.
        Also installs the two gbrain plugins even if they are not listed.
      '';

      url = mkOption {
        type = types.str;
        default = "http://127.0.0.1:3131/mcp";
        description = "GBrain HTTP MCP URL.";
      };

      tokenFile = mkOption {
        type = types.nullOr types.str;
        default = null;
        description = "Path to a token file. Injected as GBRAIN_TOKEN_FILE; never read into Nix.";
      };
    };

    skills = {
      enable = mkEnableOption "first-party hermes-pnp skills (e.g. browser)";
      extraSkills = mkOption {
        type = types.attrsOf types.path;
        default = { };
        description = "Name → skill dir beside the catalog (consumer skills).";
      };
    };

    environmentFiles = mkOption {
      type = types.listOf types.path;
      default = [ ];
      description = ''
        Secrets drop-in. Declare the rendered env file here; the composer
        forwards it to services.hermes-agent.environmentFiles (WebUI
        inherits that list). Prefer a sops.templates path. Key list:
        docs/hermes.env.example.
      '';
      example = literalExpression ''[ config.sops.templates.hermesEnv.path ]'';
    };

    mcpProxy.enable = mkOption {
      type = types.bool;
      default = false;
      description = "Turn on services.mcpProxy. Backends stay in services.mcpProxy.*.";
    };

    packageFixes.silenceMarkers = mkOption {
      type = types.bool;
      default = true;
      description = ''
        Patch gateway silence-token matching via PYTHONPATH.
        Turn off when upstream ships the plural form.
      '';
    };
  };
}
