# User-facing composer options. Official hermes-agent / hermes-webui
# trees are not re-declared here.
#
# Comment a line to drop a thing. Import a plugin beside the catalog
# with one extraPlugins attr.
{ lib
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
in
{
  options.services.hermesPnP = {
    enable = mkEnableOption ''
      composer opinions (WebUI pairing, share env, toolbox).
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
        description = "When the composer is on, install a small extraPackages set.";
      };

      extraPackages = mkOption {
        type = types.listOf types.package;
        default = [ ];
        description = "Append-only packages added to the toolbox set.";
      };
    };

    gbrain = {
      enable = mkEnableOption ''
        Thin GBrain hook: mkDefault mcpServers.gbrain.url and export
        GBRAIN_MCP_URL / GBRAIN_TOKEN_FILE. Does not start gbrain serve.
        Also appends the two gbrain plugins if they are not already listed.
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

    mcpProxy.enable = mkOption {
      type = types.bool;
      default = false;
      description = "Turn on services.mcpProxy. Backends stay in services.mcpProxy.*.";
    };

    runtime = {
      mode = mkOption {
        type = types.enum [
          "upstream"
          "s6"
        ];
        default = "upstream";
        description = ''
          upstream: official native/container path. s6: not implemented.
        '';
      };

      extraBindMounts = mkOption {
        type = types.listOf types.str;
        default = [ ];
        description = ''
          Host paths appended to official container.extraVolumes.
          A bare path becomes host:host:rw.
        '';
      };
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
