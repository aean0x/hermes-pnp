# User-facing composer options. Official hermes-agent / hermes-webui
# trees are not re-declared here.
{
  lib,
  ...
}:

let
  inherit (lib)
    mkEnableOption
    mkOption
    types
    ;
in
{
  options.services.hermesPnP = {
    enable = mkEnableOption ''
      Hermes PnP composer opinions (WebUI pairing, bundled-share env,
      package wrap, toolbox). Default off: library path (plugins +
      mcp-proxy only).
    '';

    webui.enable = mkOption {
      type = types.bool;
      default = true;
      description = ''
        When the composer is on, default-enable official WebUI pairing.
        Set false for gateway-only.
      '';
    };

    toolbox = {
      enable = mkOption {
        type = types.bool;
        default = true;
        description = ''
          When the composer is on, install a small extraPackages set
          onto the official agent path.
        '';
      };

      extraPackages = mkOption {
        type = types.listOf types.package;
        default = [ ];
        description = "Append-only packages added to the toolbox set.";
      };
    };

    runtime = {
      mode = mkOption {
        type = types.enum [
          "upstream"
          "s6"
        ];
        default = "upstream";
        description = ''
          upstream: official native/container path (PnP does not turn
          container.enable on). s6: not implemented (throws).
        '';
      };

      extraBindMounts = mkOption {
        type = types.listOf types.str;
        default = [ ];
        description = ''
          Host paths appended to official container.extraVolumes.
          A bare path becomes host:host:rw. Strings that already
          contain ":" are passed through.
        '';
      };
    };

    packageFixes.silenceMarkers = mkOption {
      type = types.bool;
      default = true;
      description = ''
        Patch gateway silence-token matching via PYTHONPATH.
        Upstream _is_token still uses singular
        _canonical_silence_candidate, so **[SILENT]** / *NO_REPLY*
        fail. Turn off when upstream ships the plural form.
      '';
    };

    gbrain = {
      enable = mkEnableOption ''
        Thin GBrain hook: mkDefault mcpServers.gbrain.url and export
        GBRAIN_MCP_URL / GBRAIN_TOKEN_FILE for first-party plugins.
        Does not start gbrain serve.
      '';

      url = mkOption {
        type = types.str;
        default = "http://127.0.0.1:3131/mcp";
        description = "GBrain HTTP MCP URL.";
      };

      tokenFile = mkOption {
        type = types.nullOr types.str;
        default = null;
        description = ''
          Path to a token file. Injected as GBRAIN_TOKEN_FILE; never
          read into Nix.
        '';
      };
    };
  };
}
