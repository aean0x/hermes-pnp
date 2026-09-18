# Install catalog + extraPluginDirs.
# NixOS: $stateDir/plugins/<name> and symlink
# $stateDir/.hermes/plugins/<name> → ../../plugins/<name>.
# Home Manager: $hermesHome/plugins/<name> (same dir as official
# extraPlugins; only remove PnP-owned trees).
#
# Sources: this repo's ./plugins catalog, plus internal.pluginSources
# (plugins pinned as flake inputs, e.g. model-picker), plus extraPluginDirs.
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
  docindexOn = (options.services.hermesPnP ? docindex) && pnp.docindex.enable;

  gbrainPlugins = [
    "gbrain-retrieval-reflex"
    "gbrain-memory-flush"
  ];

  officialExtraPluginNames = map lib.getName (agent.extraPlugins or [ ]);

  pickerOn = pnp.modelPicker.enable;

  # Materialize only PnP trees. Official extraPlugins already land as
  # nix-managed-* under $HERMES_HOME/plugins.
  # model-picker is gated by modelPicker.enable (same pattern as gbrain):
  # enable=true injects it; enable=false strips it even if listed.
  pnpNames = lib.unique (
    (lib.filter (n: n != "model-picker") pnp.plugins)
    ++ lib.optional pickerOn "model-picker"
    ++ lib.optionals gbrainOn gbrainPlugins
    ++ lib.optional docindexOn "docindex"
    ++ lib.attrNames extra
  );

  # plugins.enabled is an opt-in allow-list. Union PnP names with
  # official extraPlugins (path key + getName) so we do not hide them.
  enabledNames = lib.unique (
    pnpNames ++ officialExtraPluginNames ++ map (n: "nix-managed-${n}") officialExtraPluginNames
  );

  unknown =
    let
      # A name is known when any source can resolve it: the in-repo catalog,
      # a plugin pinned as a flake input, or extraPluginDirs.
      known =
        (lib.attrNames catalog)
        ++ (lib.attrNames pnp.internal.pluginSources)
        ++ (lib.attrNames extra);
    in
    lib.filter (n: !(lib.elem n known)) pnp.plugins;

  sources = catalog // pnp.internal.pluginSources // extra;

  pickerOrder = [
    "low"
    "default"
    "high"
  ];

  # Plugin-only keys (escalate_*, tails) live in the JSON catalog.
  # Slot identity (model, provider, label, short, best_for) is Nix.
  pickerSrc = sources.model-picker or null;

  pickerDefaults =
    if pickerSrc == null then
      null
    else
      builtins.fromJSON (builtins.readFile (pickerSrc + "/config.default.json"));

  pluginDefaults =
    if pickerDefaults == null then { models = lib.genAttrs pickerOrder (_: { }); } else pickerDefaults;

  modelPickerConfig = pluginDefaults // {
    models = lib.genAttrs pickerOrder (
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

  modelPickerWebui = {
    models =
      (map (name: {
        cmd = "/${name}";
        label = pnp.models.${name}.label;
        short = pnp.models.${name}.short;
        model = pnp.models.${name}.model;
        title = "Pin ${pnp.models.${name}.label}";
      }) pickerOrder)
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

  modelPickerPlugin =
    if pickerSrc == null then
      null
    else
      pkgs.runCommand "model-picker-plugin" { } ''
        cp -a ${pickerSrc}/. "$out/"
        chmod -R u+w "$out"
        printf '%s\n' ${lib.escapeShellArg (builtins.toJSON modelPickerConfig)} \
          > "$out/config.json"
        printf '%s\n' ${
          lib.escapeShellArg ("window.__MODEL_PICKER_CONFIG = " + builtins.toJSON modelPickerWebui + ";")
        } > "$out/webui/config.js"
      '';

  resolvedSources =
    sources
    // lib.optionalAttrs (modelPickerPlugin != null) {
      model-picker = modelPickerPlugin;
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
        model-picker, tool-call-coherency, secret-handoff (mkDefault).

        Names come from three places: this repo's ./plugins catalog, the
        flake's plugin inputs (injected as internal.pluginSources), and
        extraPluginDirs.
      '';
      example = [
        "model-picker"
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
        description = "Bundled model-picker WebUI dir. Set when that plugin is enabled.";
      };
    };

    internal.pluginSources = mkOption {
      type = types.attrsOf types.path;
      default = { };
      internal = true;
      description = ''
        Plugin name → source tree for plugins that live in their own repo.
        The flake pins them as inputs and sets this; the in-repo
        ./plugins/catalog.nix covers the rest and extraPluginDirs adds
        local trees. An empty set is valid — it only means no plugin comes
        from outside this repo.
      '';
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
    (mkIf (pnp.plugins != [ ] || extra != { } || gbrainOn || docindexOn) {
      assertions = [
        {
          assertion = unknown == [ ];
          message = "services.hermesPnP.plugins: unknown name(s): ${lib.concatStringsSep ", " unknown}";
        }
      ];

      services.hermesPnP.pluginInstall.webuiExtensionDir = lib.mkIf (
        pickerOn && lib.elem "model-picker" enabledNames && resolvedSources ? model-picker
      ) "${resolvedSources.model-picker}/webui";

      services.hermes-agent.settings.plugins.enabled = enabledNames;
      services.hermesPnP.internal.pnpPluginNames = pnpNames;
      services.hermesPnP.internal.pnpPluginSources = resolvedSources;
    })

    (mkIf pnp.enable {
      services.hermesPnP.plugins = mkDefault (
        lib.optional pickerOn "model-picker"
        ++ [
          "tool-call-coherency"
          "secret-handoff"
        ]
      );
    })
  ];
}
