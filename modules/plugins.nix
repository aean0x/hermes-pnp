# Install catalog + extraPluginDirs.
# NixOS: $stateDir/plugins/<name> and symlink
# $stateDir/.hermes/plugins/<name> → ../../plugins/<name>.
# Home Manager: $hermesHome/plugins/<name> (same dir as official
# extraPlugins; only remove PnP-owned trees).
{
  config,
  lib,
  pkgs,
  options,
  ...
}:

let
  inherit (lib)
    mkDefault
    mkIf
    mkOption
    types
    ;

  pnp = config.services.hermesPnP;
  agent = config.services.hermes-agent;
  extra = pnp.extraPluginDirs;
  catalog = import ../plugins/catalog.nix;
  gbrainOn = (options.services.hermesPnP ? gbrain) && pnp.gbrain.enable;

  gbrainPlugins = [
    "gbrain-retrieval-reflex"
    "gbrain-memory-flush"
  ];

  officialExtraPluginNames = map lib.getName (agent.extraPlugins or [ ]);

  routerOn = pnp.modelRouter.enable;

  # Materialize only PnP trees. Official extraPlugins already land as
  # nix-managed-* under $HERMES_HOME/plugins.
  # model-router is gated by modelRouter.enable (same pattern as gbrain):
  # enable=true injects it; enable=false strips it even if listed.
  pnpNames = lib.unique (
    (lib.filter (n: n != "model-router") pnp.plugins)
    ++ lib.optional routerOn "model-router"
    ++ lib.optionals gbrainOn gbrainPlugins
    ++ lib.attrNames extra
  );

  # plugins.enabled is an opt-in allow-list. Union PnP names with
  # official extraPlugins (path key + getName) so we do not hide them.
  enabledNames = lib.unique (
    pnpNames ++ officialExtraPluginNames ++ map (n: "nix-managed-${n}") officialExtraPluginNames
  );

  unknown =
    let
      known = (lib.attrNames catalog) ++ (lib.attrNames extra);
    in
    lib.filter (n: !(lib.elem n known)) pnp.plugins;

  sources = catalog // extra;

  routerOrder = [
    "low"
    "default"
    "high"
  ];

  # Plugin-only keys (escalate_*, tails) live in the JSON catalog.
  # Slot identity (model, provider, label, short, best_for) is Nix.
  pluginDefaults = builtins.fromJSON (
    builtins.readFile ../plugins/model-router/config.default.json
  );

  modelRouterConfig = pluginDefaults // {
    models = lib.genAttrs routerOrder (
      name:
      pluginDefaults.models.${name}
      // {
        inherit (pnp.models.${name})
          model
          provider
          best_for
          label
          short
          ;
      }
    );
  };

  modelRouterWebui = {
    models =
      (map (name: {
        cmd = "/${name}";
        label = pnp.models.${name}.label;
        short = pnp.models.${name}.short;
        model = pnp.models.${name}.model;
        title = "Pin ${pnp.models.${name}.label}";
      }) routerOrder)
      ++ [
        {
          cmd = "/auto";
          label = "Auto";
          short = "Auto";
          model = "";
          title = "Resume per-turn routing";
        }
      ];
  };

  modelRouterSrc = sources.model-router or null;

  modelRouterPlugin =
    if modelRouterSrc == null then
      null
    else
      pkgs.runCommand "model-router-plugin" { } ''
        cp -a ${modelRouterSrc}/. "$out/"
        chmod -R u+w "$out"
        printf '%s\n' ${lib.escapeShellArg (builtins.toJSON modelRouterConfig)} \
          > "$out/config.json"
        printf '%s\n' ${
          lib.escapeShellArg ("window.__MODEL_ROUTER_CONFIG = " + builtins.toJSON modelRouterWebui + ";")
        } > "$out/webui/config.js"
      '';

  resolvedSources =
    sources
    // lib.optionalAttrs (modelRouterPlugin != null) {
      model-router = modelRouterPlugin;
    };

in
{
  imports = [
    ./enable.nix
    ./models.nix
    (lib.mkRenamedOptionModule
      [ "services" "hermesPnP" "extraPlugins" ]
      [ "services" "hermesPnP" "extraPluginDirs" ]
    )
  ];

  options.services.hermesPnP = {
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
        # "git-hook"
      ];
    };

    extraPluginDirs = mkOption {
      type = types.attrsOf types.path;
      default = { };
      description = ''
        Name → source tree beside the catalog. Distinct from official
        services.hermes-agent.extraPlugins (listOf package).
      '';
      example = lib.literalExpression ''
        {
          # my-plugin = ./plugins/my-plugin;
        }
      '';
    };

    pluginInstall = {
      stateDir = mkOption {
        type = types.str;
        default = "/var/lib/hermes";
        internal = true;
        description = "Plugin dest root. Composer sets this from the official agent.";
      };
      user = mkOption {
        type = types.str;
        default = "hermes";
        internal = true;
      };
      group = mkOption {
        type = types.str;
        default = "hermes";
        internal = true;
      };
      webuiExtensionDir = mkOption {
        type = types.nullOr types.path;
        default = null;
        internal = true;
        description = "Bundled model-router WebUI dir. Set when that plugin is enabled.";
      };
    };

    internal.pnpPluginNames = mkOption {
      type = types.listOf types.str;
      internal = true;
      default = [ ];
      description = "Catalog + extraPluginDirs names to materialize.";
    };

    internal.pnpPluginSources = mkOption {
      type = types.attrsOf types.path;
      internal = true;
      default = { };
      description = "Name → store path for pnpPluginNames.";
    };
  };

  config = lib.mkMerge [
    (mkIf (pnp.plugins != [ ] || extra != { } || gbrainOn) {
      assertions = [
        {
          assertion = unknown == [ ];
          message = "services.hermesPnP.plugins: unknown name(s): ${lib.concatStringsSep ", " unknown}";
        }
      ];

      services.hermesPnP.pluginInstall.webuiExtensionDir = lib.mkIf (
        routerOn && lib.elem "model-router" enabledNames && resolvedSources ? model-router
      ) "${resolvedSources.model-router}/webui";

      services.hermes-agent.settings.plugins.enabled = enabledNames;
      services.hermesPnP.internal.pnpPluginNames = pnpNames;
      services.hermesPnP.internal.pnpPluginSources = resolvedSources;
    })

    (mkIf pnp.enable {
      services.hermesPnP.plugins = mkDefault (
        lib.optional routerOn "model-router"
        ++ [
          "tool-call-coherency"
          "secret-handoff"
        ]
      );
    })
  ];
}
