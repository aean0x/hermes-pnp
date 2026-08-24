# Install catalog + extraPluginDirs to $stateDir/plugins/<name>
# and symlink $stateDir/.hermes/plugins/<name> → ../../plugins/<name>.
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
  install = pnp.pluginInstall;

  gbrainOn = (options.services.hermesPnP ? gbrain) && pnp.gbrain.enable;

  gbrainPlugins = [
    "gbrain-retrieval-reflex"
    "gbrain-memory-flush"
  ];

  officialExtraPluginNames = map lib.getName (agent.extraPlugins or [ ]);

  # Materialize only PnP trees. Official extraPlugins already land as
  # nix-managed-* under $HERMES_HOME/plugins.
  pnpNames = lib.unique (pnp.plugins ++ lib.optionals gbrainOn gbrainPlugins ++ lib.attrNames extra);

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
    "medium"
    "high"
  ];

  routerMeta = {
    low.label = "Low";
    medium.label = "Medium";
    high.label = "High";
  };

  modelRouterConfig = {
    models = lib.genAttrs routerOrder (name: {
      inherit (routerMeta.${name}) label;
      inherit (pnp.models.${name}) model provider best_for;
      short = routerMeta.${name}.label;
    });
    escalate_max = "high";
    escalation_errors = {
      low = 4;
      medium = 3;
    };
  };

  modelRouterWebui = {
    models =
      (map (name: {
        cmd = "/${name}";
        label = routerMeta.${name}.label;
        short = routerMeta.${name}.label;
        model = pnp.models.${name}.model;
        title = "Pin ${routerMeta.${name}.label}";
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

  hermesHomePlugins = "${install.stateDir}/.hermes/plugins";
  materializeRoot = "${install.stateDir}/plugins";
  enabledPluginsJson = builtins.toJSON pnpNames;
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
        lib.elem "model-router" enabledNames && resolvedSources ? model-router
      ) "${resolvedSources.model-router}/webui";

      services.hermes-agent.settings.plugins.enabled = enabledNames;

      systemd.tmpfiles.rules = [
        "d ${materializeRoot} 2770 ${install.user} ${install.group} -"
        "d ${hermesHomePlugins} 2770 ${install.user} ${install.group} -"
      ];

      systemd.services.hermes-agent-plugins = {
        description = "Materialize hermes-pnp plugins";
        wantedBy = [ "multi-user.target" ];
        before = [ "hermes-agent.service" ];
        requiredBy = [ "hermes-agent.service" ];

        serviceConfig = {
          Type = "oneshot";
          RemainAfterExit = true;
          User = install.user;
          Group = install.group;
        };

        script = ''
          set -euo pipefail
          dest='${materializeRoot}'
          linkroot='${hermesHomePlugins}'
          mkdir -p "$dest" "$linkroot"

          want='${enabledPluginsJson}'
          echo "$want" | ${pkgs.jq}/bin/jq -r '.[]' | while read -r name; do
            case "$name" in
              *[!a-zA-Z0-9_-]* | "")
                echo "hermes-pnp: skip unsafe plugin name: $name" >&2
                continue
                ;;
            esac
            chmod -R u+w "$dest/$name" 2>/dev/null || true
            rm -rf "$dest/$name"
          done

          ${lib.concatMapStrings (name: ''
            ${pkgs.rsync}/bin/rsync -a --delete --chmod=D2770,F0640 \
              --exclude 'webui/' --exclude '__pycache__/' --exclude '*.pyc' \
              ${resolvedSources.${name}}/ "$dest/${name}/"
            ln -sfn "../../plugins/${name}" "$linkroot/${name}"
          '') pnpNames}

          echo "$want" | ${pkgs.jq}/bin/jq -r '.[]' > "$dest/.enabled"
        '';
      };
    })

    (mkIf pnp.enable {
      services.hermesPnP.plugins = mkDefault [
        "model-router"
        "tool-call-coherency"
        "secret-handoff"
      ];
    })
  ];
}
