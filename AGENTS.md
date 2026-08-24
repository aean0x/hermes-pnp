# Hermes PnP — agent notes

`docs/DESIGN.md` is the source of truth. Read it before changing Nix
modules or flake outputs.

This is a **library flake**, not a host flake. Site identity, secrets,
hostnames, Telegram IDs, mail policy, RAM caps, and SOUL.md / USER.md /
MEMORY.md stay in the consumer. Browser CDP/dashboard provisioning is a
composer opinion (`services.hermesPnP.browser`); the engine stays in the
consumer.

## Composer vs library

- `services.hermesPnP.enable = false` (default): plugins +
  `services.hermesPnP.mcpProxy`. Official services stay off unless the
  consumer enables them. Opt-in `gbrain` / `hmc` still work.
- `services.hermesPnP.enable = true`: WebUI pairing, share env, optional
  silence wrap, toolbox, browser.

## Git identity

- Commit ONLY as GitHub user `aean0x` (`users.noreply.github.com`).
  Set `user.name` + `user.email` global AND repo-local.
- Check `git config user.name` before the first commit of a session.
- After any history rewrite, re-pin downstream `flake.lock` inputs.

## Do

- Import official `hermes-agent` / `hermes-webui` modules. Do not
  re-declare their option trees.
- Set pairing values with `mkDefault` only.
- Forward `services.hermes-agent.extraPythonPackages` and
  `extraDependencyGroups` into the package wrap. Do not default extras.
- Materialize first-party plugins to `$stateDir/plugins/<name>` and
  symlink `$stateDir/.hermes/plugins/<name>` → `../../plugins/<name>`.
  Do not install them via official `extraPlugins`. Consumer trees
  go on `extraPluginDirs`. Union official extraPlugins names into
  `settings.plugins.enabled`.
- Fold official `extraPackages` into the toolbox buildEnv. Do not put
  the env back on `extraPackages`.
- WebUI/browser jails follow official `container.enable` / network,
  not `hermesPnP.container.enable`.
- Gate the silence-marker wrap on
  `services.hermesPnP.packageFixes.silenceMarkers`.
- Extra host mounts go on official `container.extraVolumes`.

## Do not

- Manage PGLite / sources / a memory registry from Nix.
  `gbrain.enable` stays default-off.
- Write SOUL.md / USER.md / MEMORY.md from Nix.
- Put secrets in JSON (`mcpServers` may reference env vars only).
- Rewrite plugin Python unless Nix wiring requires it.
- Force consumers onto container mode.

## Layout

```
lib/                      # mkOciJail, mkDockerEnv, remapStatePath
modules/                  # composer + pairing; options next to config
modules/webui/            # WebUI pairing + host harden + OCI jail
modules/browser/          # CDP browser + dashboard + cookie import
pkgs/mcp-proxy/           # proxy package + src/tests/examples
pkgs/agent-browser.nix    # pinned musl-static release
checks/                   # eval + plugin/proxy tests
examples/                 # consumer snippets (evalled by checks)
plugins/catalog.nix       # plugin name → path
skills/catalog.nix        # first-party skill name → path
scripts/                  # gbrain-setup / validate-gbrain / wire-config (not Nix)
plugins/                  # first-party plugin trees
skills/                   # first-party skill trees
```

Adding a plugin: drop `plugins/<name>/` and add one catalog line.

Router tiers: `models.low` / `models.medium` / `models.high`. Nix also
has `models.auxiliary` (official aux slots; not a slash command).
`reasoning_effort` is unset except auxiliary (`"none"`). Router tiers
have `best_for` (classifier matrix; plugin JSON defaults). Model-router
never writes reasoning. No T1/T2/T3 in plugin.yaml, WebUI, or slash
commands.

## Checks

`nix flake check` stays eval-cheap for the composer (dummy packages).
Keep plugin pytest and mcp-proxy tests.
