# Hermes PnP — agent notes

`docs/DESIGN.md` is the source of truth. Read it before changing Nix
modules or flake outputs.

This is a **library flake**, not a host flake. Site identity, secrets,
hostnames, Telegram IDs, mail policy, browser CDP, RAM caps, and
SOUL.md / USER.md / MEMORY.md stay in the consumer.

## Composer vs library

- `services.hermesPnP.enable = false` (default) keeps the library
  path: plugins + `services.mcpProxy` only. Official services stay off
  unless the consumer enables them.
- `services.hermesPnP.enable = true` turns on pairing opinions
  (WebUI, share env, optional silence wrap, toolbox). One extra
  `enable = true` on a native Hermes config.

## Do

- Import official `hermes-agent` / `hermes-webui` modules. Do not
  re-declare their option trees.
- Set pairing values with `mkDefault` only.
- Forward `services.hermes-agent.extraPythonPackages` and
  `extraDependencyGroups` into the package wrap. Do not default extras
  to `["mcp"]`.
- Materialize first-party plugins to `$stateDir/plugins/<name>` and
  symlink `$stateDir/.hermes/plugins/<name>` → `../../plugins/<name>`.
  Do not install them via official `extraPlugins`.
- Gate the silence-marker wrap on
  `services.hermesPnP.packageFixes.silenceMarkers`.
- Keep `runtime.mode = "s6"` as a hard stub (`throw`) until a complete
  port exists.

## Do not

- Add an HMC module.
- Start `gbrain serve` or manage PGLite / sources from Nix.
- Write SOUL.md / USER.md / MEMORY.md from Nix.
- Put secrets in JSON (`mcpServers` may reference env vars only).
- Rewrite plugin Python unless Nix wiring requires it.
- Force consumers onto container mode or s6.

## Layout

```
nix/modules/default.nix   # composer imports
nix/modules/options.nix   # enable, models, plugins, extraPlugins, quiet toggles
nix/modules/models.nix    # seed official settings from models.*
nix/modules/plugins.nix   # plugins list + extraPlugins + pluginInstall
nix/catalog.nix           # plugin name → path
services/mcp-proxy/       # services.mcpProxy.*
plugins/                  # first-party plugin trees
```

Adding a plugin: drop `plugins/<name>/` and add one catalog line.

Three models, named: `models.low` / `models.medium` / `models.high`.
No T1/T2/T3 in Nix options, README, plugin.yaml, WebUI, or slash
commands.

## Checks

`nix flake check` must stay eval-cheap for the composer (dummy
packages; do not realize the official agent/webui builds). Keep
plugin pytest and mcp-proxy tests.

**Please consider leaving a star if this repo saved you time or tokens :)**
