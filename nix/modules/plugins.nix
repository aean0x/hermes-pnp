# Install Hermes PnP plugins into $HERMES_HOME/plugins via materialize + symlink.
#
# Hermes ≥0.19 scans ONLY $HERMES_HOME/plugins. There is no plugins.external_dirs.
#
#   services.hermesPnP.plugins = [ "model-router" "tool-call-coherency" ];
#   services.hermesPnP.extraPlugins.my-plugin = ./local;
#
# Host-only plugins belong in extraPlugins. Do not use official
# services.hermes-agent.extraPlugins for first-party plugins: it copies
# into $HERMES_HOME and fights the container /data remap.
{ config
, lib
, pkgs
, ...
}:

let
  inherit (lib)
    mkDefault
    mkIf
    mkMerge
    mkOption
    types
    ;

  pnp = config.services.hermesPnP;
  install = pnp.pluginInstall;
  catalog = import ../catalog.nix;

  enabledNames = lib.unique (pnp.plugins ++ lib.attrNames pnp.extraPlugins);
  sources = catalog // pnp.extraPlugins;

  missing = lib.filter (name: !(sources ? ${name})) pnp.plugins;

  modelRouterSrc = sources.model-router or null;

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
      inherit (pnp.models.${name}) provider model;
      inherit (routerMeta.${name}) label role;
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
      (map
        (name: {
          cmd = "/${name}";
          label = routerMeta.${name}.label;
          short = routerMeta.${name}.label;
          model = pnp.models.${name}.model;
          title = "Pin ${routerMeta.${name}.label}";
        })
        routerOrder)
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
      description = "Hermes state directory (installer internal).";
    };

    user = mkOption {
      type = types.str;
      default = "hermes";
    };

    group = mkOption {
      type = types.str;
      default = "hermes";
    };

    webuiExtensionDir = mkOption {
      type = types.nullOr types.path;
      default = null;
      description = "Read-only: model-router WebUI extension dir when that plugin is enabled.";
    };
  };

  config = mkMerge [
    (mkIf pnp.enable {
      services.hermesPnP.plugins = mkDefault [
        "model-router"
        "tool-call-coherency"
        "secret-handoff"
      ];
    })

    (mkIf pnp.gbrain.enable {
      services.hermesPnP.plugins = [
        "gbrain-retrieval-reflex"
        "gbrain-memory-flush"
      ];
    })

    (mkIf (enabledNames != [ ]) {
      assertions = [
        {
          assertion = missing == [ ];
          message = "hermesPnP.plugins names not in catalog or extraPlugins: ${lib.concatStringsSep ", " missing}";
        }
      ];

      services.hermesPnP.pluginInstall.webuiExtensionDir = lib.mkIf
        (
          resolvedSources ? model-router
        ) "${resolvedSources.model-router}/webui";

      # Requires the official hermes-agent module. Installer is a no-op without enable.
      services.hermes-agent.settings.plugins.enabled = enabledNames;

      systemd.services.hermes-agent.serviceConfig.ReadWritePaths = lib.mkIf config.services.hermes-agent.enable [
        materializeRoot
        hermesHomePlugins
      ];

      system.activationScripts.hermes-pnp-plugins = lib.stringAfter [
        "users"
        "groups"
      ] ''
                install_plugin_tree() {
                  local name="$1" src="$2"
                  local dest="${materializeRoot}/$name"
                  local link="${hermesHomePlugins}/$name"
                  mkdir -p "$dest" "${hermesHomePlugins}"
                  if [ -e "$link" ] && [ ! -L "$link" ]; then
                    rm -rf "$link"
                  fi
                  find "$dest" -mindepth 1 -maxdepth 1 ! -name webui ! -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
                  ${pkgs.rsync}/bin/rsync -a --delete \
                    --exclude 'webui/' --exclude '__pycache__/' --exclude '*.pyc' \
                    "$src"/ "$dest"/
                  find "$dest" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
                  find "$dest" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete 2>/dev/null || true
                  find "$dest" -type f -name '*.py' -exec touch -c {} + 2>/dev/null || true
                  chown -R ${install.user}:${install.group} "$dest" 2>/dev/null || true
                  find "$dest" -type d -exec chmod 2770 {} \; 2>/dev/null || true
                  find "$dest" -type f -exec chmod 0640 {} \; 2>/dev/null || true
                  ln -sfn ../../plugins/"$name" "$link"
                  chown -h ${install.user}:${install.group} "$link" 2>/dev/null || true
                }

                ${lib.concatMapStrings (name: ''
                  install_plugin_tree ${lib.escapeShellArg name} ${resolvedSources.${name}}
                '') enabledNames}

                cfg=${install.stateDir}/.hermes/config.yaml
                if [ -f "$cfg" ]; then
                  ${pkgs.python3.withPackages (ps: [ ps.pyyaml ])}/bin/python3 - "$cfg" <<'PY'
        import sys
        from pathlib import Path
        import yaml

        path = Path(sys.argv[1])
        data = yaml.safe_load(path.read_text()) or {}
        if not isinstance(data, dict):
            sys.exit(0)

        changed = False
        plugins = data.setdefault("plugins", {})
        if not isinstance(plugins, dict):
            plugins = {}
            data["plugins"] = plugins
            changed = True

        if "external_dirs" in plugins:
            del plugins["external_dirs"]
            changed = True

        enabled = list(plugins.get("enabled") or [])
        want = ${enabledPluginsJson}
        for name in want:
            if name not in enabled:
                enabled.append(name)
                changed = True
        if plugins.get("enabled") != enabled:
            plugins["enabled"] = enabled
            changed = True

        if changed:
            path.write_text(yaml.safe_dump(data, sort_keys=False, default_flow_style=False))
        PY
                  chown ${install.user}:${install.group} "$cfg" 2>/dev/null || true
                fi
      '';
    })
  ];
}
