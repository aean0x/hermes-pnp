# Hermes PnP — agent notes

`docs/DESIGN.md` is the source of truth. Read it before changing Nix
modules or flake outputs.

This is a **library flake**, not a host flake. Site identity, secrets,
hostnames, Telegram IDs, mail policy, RAM caps, and SOUL.md / USER.md /
MEMORY.md stay in the consumer. Browser CDP/dashboard provisioning *is* a
composer opinion (`services.hermesPnP.browser`); the engine stays in the
consumer.

## Composer vs library

- `services.hermesPnP.enable = false` (default) keeps the library
  path: plugins + `services.hermesPnP.mcpProxy` only. Official services
  stay off unless the consumer enables them.
- `services.hermesPnP.enable = true` turns on pairing opinions
  (WebUI, share env, optional silence wrap, toolbox, browser). One extra
  `enable = true` on a native Hermes config.

## Git identity (non-negotiable)

- Commit ONLY as GitHub user `aean0x` (that account's
  `users.noreply.github.com` identity). Set `user.name` + `user.email`
  global AND repo-local. Never a local agent identity.
- Check `git config user.name` before the first commit of a session.
- After any history rewrite, re-pin downstream `flake.lock` inputs.

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

## Do not

- Manage PGLite / sources / a memory registry from Nix. `gbrain.enable`
  may start loopback `gbrain serve`; it must stay default-off.
- Write SOUL.md / USER.md / MEMORY.md from Nix.
- Put secrets in JSON (`mcpServers` may reference env vars only).
- Rewrite plugin Python unless Nix wiring requires it.
- Force consumers onto container mode or s6.
- Re-add `runtime.mode` / a `runtime.*` wrapper (deleted — use official
  `container.extraVolumes` directly).

## Layout

```
lib/                      # flake.lib — mkOciJail, mkDockerEnv
modules/                  # composer + pairing; options live next to config
modules/webui/            # official WebUI pairing + host harden + OCI jail
modules/browser/          # CDP browser + dashboard + cookie import
pkgs/mcp-proxy/           # proxy package + src/tests/examples
pkgs/agent-browser.nix    # pinned musl-static release
checks/                   # eval + plugin/proxy tests (CI)
examples/                 # composer + library-path (evalled by checks)
plugins/catalog.nix       # plugin name → path
skills/catalog.nix        # first-party skill name → path
scripts/                  # gbrain-setup / validate-gbrain (operator; not Nix)
plugins/                  # first-party plugin trees
skills/                   # first-party skill trees
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
