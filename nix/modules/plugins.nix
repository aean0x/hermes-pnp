# Install Hermes PnP plugins into $HERMES_HOME/plugins via materialize + symlink.
#
# Hermes ≥0.19 scans ONLY $HERMES_HOME/plugins. There is no plugins.external_dirs.
#
#   services.hermesPnP.plugins.enable = [ "model-router" "tool-call-coherency" ];
#   services.hermesPnP.plugins.extraPlugins.my-plugin = ./local;
#
# Host-only plugins (HMC pins, one-off trees) belong in extraPlugins.
# Do not use official services.hermes-agent.extraPlugins for first-party
# plugins: it copies into $HERMES_HOME and fights the container /data remap.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  inherit (lib)
    mkIf
    mkOption
    types
    ;

  cfg = config.services.hermesPnP.plugins;
  catalog = import ../catalog.nix;

  enabledNames = lib.unique (cfg.enable ++ lib.attrNames cfg.extraPlugins);
  sources = catalog // cfg.extraPlugins;

  missing = lib.filter (name: !(sources ? ${name})) cfg.enable;

  modelRouterSrc = sources.model-router or null;

  modelRouterWebuiTiers =
    let
      raw = cfg.modelRouter.settings.tiers or { };
      names = lib.attrNames raw;
    in
    map (name: rec {
      cmd = "/t${name}";
      label = raw.${name}.label or "T${name}";
      short = raw.${name}.short or "T${name}";
      model = raw.${name}.model or "";
      title = "Pin ${label}";
    }) (lib.sort (a: b: a < b) names);

  modelRouterPlugin =
    if modelRouterSrc == null || cfg.modelRouter.settings == { } then
      modelRouterSrc
    else
      pkgs.runCommand "model-router-plugin" { } ''
        cp -a ${modelRouterSrc}/. "$out/"
        chmod -R u+w "$out"
        printf '%s\n' ${lib.escapeShellArg (builtins.toJSON cfg.modelRouter.settings)} \
          > "$out/config.json"
        printf '%s\n' ${lib.escapeShellArg (
          "window.__MODEL_ROUTER_CONFIG = " + builtins.toJSON { tiers = modelRouterWebuiTiers; } + ";"
        )} > "$out/webui/config.js"
      '';

  resolvedSources = sources // lib.optionalAttrs (modelRouterPlugin != null) {
    model-router = modelRouterPlugin;
  };

  hermesHomePlugins = "${cfg.stateDir}/.hermes/plugins";
  materializeRoot = "${cfg.stateDir}/plugins";
  enabledPluginsJson = builtins.toJSON enabledNames;
in
{
  options.services.hermesPnP.plugins = {
    enable = mkOption {
      type = types.listOf types.str;
      default = [ ];
      description = "Catalog plugin names to materialize and enable.";
      example = [
        "model-router"
        "tool-call-coherency"
      ];
    };

    extraPlugins = mkOption {
      type = types.attrsOf types.path;
      default = { };
      description = "Additional name → source trees (host pins such as HMC).";
    };

    stateDir = mkOption {
      type = types.str;
      default = "/var/lib/hermes";
      description = "Hermes state directory (official module default).";
    };

    user = mkOption {
      type = types.str;
      default = "hermes";
    };

    group = mkOption {
      type = types.str;
      default = "hermes";
    };

    modelRouter.settings = mkOption {
      type = types.attrs;
      default = { };
      description = ''
        Optional overlay written as plugins/model-router/config.json.
        Empty keeps plugin defaults. Typical keys: tiers, final_tier,
        final_voice, rest_on_final_tier, escalation_errors.
      '';
    };

    webuiExtensionDir = mkOption {
      type = types.nullOr types.path;
      default = null;
      description = "Read-only: model-router WebUI extension dir when that plugin is enabled.";
    };
  };

  config = mkIf (enabledNames != [ ]) {
    assertions = [
      {
        assertion = missing == [ ];
        message = "hermesPnP.plugins.enable names not in catalog or extraPlugins: ${lib.concatStringsSep ", " missing}";
      }
    ];

    services.hermesPnP.plugins.webuiExtensionDir = lib.mkIf (
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
        chown -R ${cfg.user}:${cfg.group} "$dest" 2>/dev/null || true
        find "$dest" -type d -exec chmod 2770 {} \; 2>/dev/null || true
        find "$dest" -type f -exec chmod 0640 {} \; 2>/dev/null || true
        ln -sfn ../../plugins/"$name" "$link"
        chown -h ${cfg.user}:${cfg.group} "$link" 2>/dev/null || true
      }

      ${lib.concatMapStrings (name: ''
        install_plugin_tree ${lib.escapeShellArg name} ${resolvedSources.${name}}
      '') enabledNames}

      cfg=${cfg.stateDir}/.hermes/config.yaml
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
        chown ${cfg.user}:${cfg.group} "$cfg" 2>/dev/null || true
      fi
    '';
  };
}
