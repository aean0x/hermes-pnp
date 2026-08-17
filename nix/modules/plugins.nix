# Install first-party + extra plugins. Catalog names in `plugins`;
# extra trees in `extraPlugins`. Comment a line to drop it.
# Dest is $stateDir/plugins/<name> + relative symlink under .hermes/plugins.
{
  config,
  lib,
  pkgs,
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
  extra = pnp.extraPlugins;
  catalog = import ../../catalog.nix;
  install = pnp.pluginInstall;

  gbrainPlugins = [
    "gbrain-retrieval-reflex"
    "gbrain-memory-flush"
  ];

  # User list + extras + gbrain pair (only when gbrain.enable).
  # Do not assign those names back onto `plugins` — that would clobber
  # the composer mkDefault at definition priority 100.
  enabledNames = lib.unique (
    pnp.plugins
    ++ lib.optionals pnp.gbrain.enable gbrainPlugins
    ++ lib.attrNames extra
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
    low = {
      label = "Low";
      role = "fast triage + cheap helper";
    };
    medium = {
      label = "Medium";
      role = "default workhorse";
    };
    high = {
      label = "High";
      role = "high-stakes + final voice";
    };
  };

  modelRouterConfig = {
    models = lib.genAttrs routerOrder (name: {
      inherit (routerMeta.${name}) label role;
      inherit (pnp.models.${name}) model provider;
      short = routerMeta.${name}.label;
    });
    final = "high";
    final_voice = true;
    rest_on_high = true;
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
        printf '%s\n' ${lib.escapeShellArg (
          "window.__MODEL_ROUTER_CONFIG = " + builtins.toJSON modelRouterWebui + ";"
        )} > "$out/webui/config.js"
      '';

  resolvedSources =
    sources
    // lib.optionalAttrs (modelRouterPlugin != null) {
      model-router = modelRouterPlugin;
    };

  hermesHomePlugins = "${install.stateDir}/.hermes/plugins";
  materializeRoot = "${install.stateDir}/plugins";
  enabledPluginsJson = builtins.toJSON enabledNames;
in
{
  options.services.hermesPnP.pluginInstall = {
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

  config = lib.mkMerge [
    (mkIf (pnp.plugins != [ ] || extra != { } || pnp.gbrain.enable) {
      assertions = [
        {
          assertion = unknown == [ ];
          message = "services.hermesPnP.plugins: unknown name(s): ${lib.concatStringsSep ", " unknown}";
        }
      ];

      services.hermesPnP.pluginInstall.webuiExtensionDir = lib.mkIf
        (lib.elem "model-router" enabledNames && resolvedSources ? model-router)
        "${resolvedSources.model-router}/webui";

      services.hermes-agent.settings.plugins.enabled = enabledNames;

      systemd.tmpfiles.rules = [
        "d ${materializeRoot} 0750 ${install.user} ${install.group} -"
        "d ${hermesHomePlugins} 0750 ${install.user} ${install.group} -"
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

          want=${enabledPluginsJson}
          echo "$want" | ${pkgs.jq}/bin/jq -r '.[]' | while read -r name; do
            case "$name" in
              *[!a-zA-Z0-9_-]* | "")
                echo "hermes-pnp: skip unsafe plugin name: $name" >&2
                continue
                ;;
            esac
            rm -rf "$dest/$name"
          done

          ${lib.concatMapStrings (name: ''
            ${pkgs.rsync}/bin/rsync -a --delete \
              --exclude 'webui/' --exclude '__pycache__/' --exclude '*.pyc' \
              ${resolvedSources.${name}}/ "$dest/${name}/"
            ln -sfn "../../plugins/${name}" "$linkroot/${name}"
          '') enabledNames}

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
