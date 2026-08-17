# Hermes PnP — Design

Opinionated NixOS composer for Hermes Agent. One flake input. Drop-in
on top of the official `services.hermes-agent` surface. WebUI is part of
the product. Site identity stays in the consumer flake.

This is not a host flake. It does not own secrets, hostnames, Telegram
IDs, mail routing, browser CDP, RAM caps, or SOUL.md.

## Goal

A user who already has a native Hermes NixOS setup should be able to:

```nix
# flake.nix
inputs.hermes-pnp.url = "github:aean0x/hermes-pnp";

# configuration
imports = [ inputs.hermes-pnp.nixosModules.default ];

services.hermes-agent = {
  enable = true;
  settings.model.default = "xai/grok-4";
  environmentFiles = [ config.sops.secrets.hermes-env.path ];
};

services.hermesPnP.enable = true;
```

and get a cohesive agent + WebUI + first-party plugins + toolbox + MCP
proxy, with official options still working as documented.

A user who wants more control keeps writing `services.hermes-agent.*`
and `services.hermes-webui.*` exactly as the upstream modules declare
them. PnP only adds pairing, plugins, and a few extra modules.

## Non-goals

- Declarative GBrain (PGLite, sources, embeddings, HTTP serve). Tertiary.
  We expose first-party GBrain *plugins* and a thin optional MCP URL hook.
- HMC / context-manager packaging. Leave it out.
- Honcho, Telegram home-channel, browser CDP, Composio mail-filter policy.
  Those are site policy. The MCP *proxy mechanism* stays; the mail rules
  do not.
- Shipping SOUL.md / USER.md / MEMORY.md from Nix.
- Replacing the official module option tree with a parallel settings DSL.
- Being a full host flake (networking, sops, disks, users beyond hermes).

## Relationship to official modules

PnP **imports** `hermes-agent.nixosModules.default` and
`hermes-webui.nixosModules.default`. It does not re-declare
`services.hermes-agent.enable`, `settings`, `environmentFiles`,
`container.*`, `documents`, `mcpServers`, `extraPythonPackages`, or
`extraDependencyGroups`.

Official options stay the user-facing config language. PnP opinions
land as `mkDefault` (overridable) or implicit pairing (not an option)
on that same tree.

Double-import is fine: a consumer that already imports the official
modules and then adds PnP will merge the same option set.

Flake inputs (followed, not vendored):

- `nixpkgs`
- `hermes-agent` — `github:NousResearch/hermes-agent`
- `hermes-webui` — `github:nesquena/hermes-webui`
- `mcp-proxy` — already in-tree as `services/mcp-proxy` (path input)

Re-export the official modules for consumers who want them unbundled:

- `nixosModules.default` — composer (agent + webui + pnp extras)
- `nixosModules.agent` — official agent only
- `nixosModules.webui` — official webui only
- `nixosModules.plugins` — plugin installer only (today's module)
- `nixosModules.mcp-proxy` — proxy only
- `nixosModules.toolbox`
- `nixosModules.runtime`

## Module map

```
hermes-pnp/
  flake.nix
  docs/DESIGN.md                 # this file
  AGENTS.md                      # for future agents
  README.md                      # user-facing, matches this design
  nix/
    catalog.nix                  # plugin name → path (exists)
    lib.nix                      # shared helpers (docker --env, plugin dest)
    modules/
      default.nix                # composer: imports below
      agent.nix                  # official module + pairing opinions
      webui.nix                  # official webui + pairing defaults
      plugins.nix                # exists; keep API
      toolbox.nix                # extraPackages + PATH
      runtime.nix                # extra binds; optional s6
      package.nix                # shared package + bundled-share env
      gbrain.nix                 # thin optional MCP URL + plugin env
  plugins/                       # first-party plugins (unchanged)
  services/mcp-proxy/            # exists; keep services.mcpProxy
```

`services.hermesPnP.enable` turns on the composer opinions (WebUI
pairing, bundled-share env, plugin dest convention). Plugins and
mcp-proxy stay independently selectable, as they are today.

## Opinion vs option

### Core opinions — do not expose

These make the setup cohesive. No knobs. If a consumer needs something
else, they should not use the composer, or they override the *official*
option PnP set via `mkDefault`.

1. **One identity.** Gateway and WebUI share `user`, `group`, `package`,
   and the same store-safe runtime env map. WebUI never gets a second
   package override.
2. **Plugin dest.** Materialize to `$stateDir/plugins/<name>` and link
   `$stateDir/.hermes/plugins/<name>` → `../../plugins/<name>`. Hermes
   ≥0.19 has no `plugins.external_dirs`. Do not use official
   `extraPlugins` for first-party plugins (it copies into `$HERMES_HOME`
   and fights the container `/data` remap).
3. **WebUI bind.** `127.0.0.1:8787`, `hermesHome = ${stateDir}/.hermes`,
   same user as the agent. Opening the firewall or binding `0.0.0.0` is
   a consumer override of `services.hermes-webui.*`.
4. **Bundled share env.** Container and WebUI do not exec the upstream
   `hermes` wrapper, so they miss `HERMES_BUNDLED_PLUGINS` etc. PnP
   injects that map into `services.hermes-agent.environment` and into
   container `extraOptions` as `docker --env`. This is how plugins and
   skills exist at all in those entrypoints.
5. **No identity-from-Nix.** PnP never writes SOUL / USER / MEMORY.
6. **No HMC.** Not imported, not configured.
7. **No secrets in JSON.** MCP credentials go through environmentFiles
   or the MCP proxy. `mcpServers` may contain URLs and headers that
   reference env vars; never raw tokens.
8. **Site policy stays out.** Telegram allowlists, home channel, RAM
   Percentage, browser CDP, Composio tool/account filters, hostnames,
   sops paths — consumer flake only.

### User-facing options

Keep existing names. Add only what a consumer must say to compose.

**Official (passthrough — do not wrap):**

- `services.hermes-agent.enable`
- `services.hermes-agent.settings` (including `settings.platform`,
  `model`, `mcp_servers`, …)
- `services.hermes-agent.environmentFiles`
- `services.hermes-agent.environment`
- `services.hermes-agent.documents`
- `services.hermes-agent.mcpServers`
- `services.hermes-agent.container.*`
- `services.hermes-agent.extraPythonPackages`
- `services.hermes-agent.extraDependencyGroups`
- `services.hermes-agent.user` / `group` / `stateDir` / `workingDirectory`
- `services.hermes-webui.*`

**PnP extras:**

- `services.hermesPnP.enable` — composer on. Default `false`.
  When true: WebUI enable defaults on, pairing defaults apply,
  package/share env is injected.
- `services.hermesPnP.webui.enable` — default `true` when composer
  is on. Escape hatch to run gateway-only.
- `services.hermesPnP.plugins.enable` — list of catalog names.
  Already exists. Keep.
- `services.hermesPnP.plugins.extraPlugins` — already exists. Keep.
- `services.hermesPnP.plugins.modelRouter.settings` — already exists.
- `services.hermesPnP.toolbox.enable` — default `true` when composer
  is on. Installs a small curated extraPackages set onto the official
  `container.extraPackages` / native `environment.systemPackages` path.
- `services.hermesPnP.toolbox.extraPackages` — append-only.
- `services.hermesPnP.runtime.mode` — `"upstream"` (default) or `"s6"`.
  Upstream = official `container.enable` path, untouched.
  `s6` = optional docker+s6 pairing extracted from rk3588. Not the
  default. Drop-in native users must not be forced onto s6.
- `services.hermesPnP.runtime.extraBindMounts` — list of host paths
  appended to official `container.extraVolumes` (upstream mode) or
  the s6 `-v` list (s6 mode).
- `services.hermesPnP.packageFixes.silenceMarkers` — default `true`.
  Upstream `_is_token` still uses singular `_canonical_silence_candidate`,
  so `**[SILENT]**` / `*NO_REPLY*` fail. Patch via PYTHONPATH. Escape
  hatch for when upstream ships the plural form.
- `services.hermesPnP.gbrain.enable` — default `false`. Thin: set
  `settings.mcp_servers.gbrain.url` (mkDefault) and export
  `GBRAIN_MCP_URL` / `GBRAIN_TOKEN_FILE` for the first-party plugins.
  Does **not** start `gbrain serve`, manage PGLite, or declare sources.
- `services.hermesPnP.gbrain.url` — default `http://127.0.0.1:3131/mcp`.
- `services.hermesPnP.gbrain.tokenFile` — optional path. Injected as
  env, never read into Nix.
- `services.mcpProxy.*` — unchanged.

### Defaults PnP sets with `mkDefault` (overridable)

When `services.hermesPnP.enable = true`:

- `services.hermes-webui.enable = true` (unless `hermesPnP.webui.enable = false`)
- `services.hermes-webui.user/group` = agent user/group
- `services.hermes-webui.agent.package` = agent package
- `services.hermes-webui.hermesHome` = `${agent.stateDir}/.hermes`
- `services.hermes-webui.host = "127.0.0.1"`
- `services.hermes-webui.port = 8787`
- `services.hermes-webui.environmentFiles` = agent environmentFiles
- toolbox extraPackages (git, curl, jq, ripgrep, file, unzip, and a
  similarly small set — not the rk3588 kitchen sink)

When `services.hermesPnP.enable = false`, PnP is inert except for
explicitly enabled submodules (`plugins.enable`, `mcpProxy.enable`).
This preserves today's "library flake" use.

## Drop-in migration

Native today:

```nix
imports = [
  inputs.hermes-agent.nixosModules.default
  inputs.hermes-webui.nixosModules.default
];
services.hermes-agent = {
  enable = true;
  settings.model.default = "…";
  environmentFiles = [ … ];
};
```

PnP:

```nix
imports = [ inputs.hermes-pnp.nixosModules.default ];
services.hermes-agent = {
  enable = true;
  settings.model.default = "…";
  environmentFiles = [ … ];
};
services.hermesPnP.enable = true;
services.hermesPnP.plugins.enable = [
  "model-router"
  "tool-call-coherency"
  "secret-handoff"
];
```

`settings.*` does not move. Secrets do not move. The consumer deletes
their hand-rolled WebUI pairing, plugin symlink scripts, and bundled
share env once they opt into the composer.

rk3588-nixos-nas is a *consumer*, not this repo. Host cutover is a
follow-up PR there. This repo ships the composer and a documented
example.

## Plugins

Catalog stays the SoT (`nix/catalog.nix`). Adding a plugin is: drop a
folder under `plugins/<name>`, add one catalog line.

Current first-party set (keep, do not rewrite Python unless Nix wiring
requires it):

- `model-router`
- `tool-call-coherency`
- `gbrain-retrieval-reflex`
- `gbrain-memory-flush`
- `secret-handoff`
- `projects-auto-commit`

Installer rules (already true, keep):

- Empty `plugins.enable` and no `extraPlugins` → no plugin files.
- Materialize real files to `$stateDir/plugins/<name>`.
- Discover via relative symlink under `$stateDir/.hermes/plugins/`.
- Gateway unit grows `ReadWritePaths` for both dests when systemd
  ProtectSystem is on.

Do not add a parallel `services.hermes-agent.extraPlugins` path for
these. Document why in the module comment.

## Package / share env

Extract rk3588 `overrides/package-fix.nix` into `nix/modules/package.nix`.

Responsibilities:

1. Optional silence-marker wrap (gated by
   `packageFixes.silenceMarkers`).
2. Force `services.hermes-agent.package` to that derivation so gateway
   and WebUI consume the same store path.
3. Export the bundled-share env map (`HERMES_BUNDLED_PLUGINS`,
   `HERMES_BUNDLED_SKILLS`, `HERMES_OPTIONAL_SKILLS`,
   `HERMES_BUNDLED_LOCALES`, `HERMES_OPTIONAL_MCPS`, `HERMES_WEB_DIST`,
   `HERMES_TUI_DIR`) plus optional silence `PYTHONPATH`.
4. Apply the map to `services.hermes-agent.environment` and, when
   `container.enable`, to `container.extraOptions` as `docker --env`.

`extraDependencyGroups` / `extraPythonPackages` stay official options.
The wrap must *forward* whatever the consumer set on the service, not
replace `full` with `[]`.

Do not bake rk3588's `["mcp"]` extra as a PnP default. Native `full`
already has what most users need; extras are a consumer choice.

## Runtime

Default is the official module's native or container path. PnP does not
turn `container.enable` on. The consumer does, same as today.

`runtime.mode = "upstream"` (default):

- `runtime.extraBindMounts` → `container.extraVolumes`
- toolbox packages → `container.extraPackages` when container is on,
  else `environment.systemPackages`

`runtime.mode = "s6"`:

- Extract rk3588 `runtime.nix` + `toolbox.nix` image/s6 tree into
  `nix/modules/runtime.nix` (and whatever small `services/runtime/`
  tree it needs).
- Still same user/package/env map.
- This is for hosts that already outgrew the official container
  (extra binds, docker.sock, PATH). It is an escape hatch, not the
  product default.

Do not copy rk3588's 50% RAM cap, `/data/src` bind, or media mounts
into PnP defaults.

## Toolbox

Small default set, append-only `extraPackages`. The opinion is "the
agent can do basic unix work," not "here is one person's workstation."

Default packages: `git`, `curl`, `jq`, `ripgrep`, `file`, `unzip`,
`gnused`, `coreutils`, `findutils`.

Not in the default: `gh`, `docker`, `sops`, `age`, `nmap`, language
toolchains. Consumers append those.

## GBrain (tertiary)

`gbrain.nix` is ~40 lines:

- `enable` default false
- `url` / `tokenFile`
- `mkDefault` `services.hermes-agent.settings.mcp_servers.gbrain`
- env for `gbrain-retrieval-reflex` / `gbrain-memory-flush`

No systemd unit. No PGLite. No sources. The consumer runs `gbrain
serve --http` however they want (or not at all).

Enabling the GBrain *plugins* does not require `gbrain.enable`. The
plugins no-op if the env is unset.

## MCP proxy

Unchanged. `services.mcpProxy`. Composer does not auto-enable it.
A consumer who wants Composio/GitHub auth injection turns it on and
points `settings.mcp_servers` at `http://127.0.0.1:<port>/…`.

Site-specific `tools.deny` / account filters stay in the consumer.

## WebUI

Integral when the composer is on. Implementation is the official
module plus pairing defaults listed above. No forked WebUI package.

`After=` / `Wants=` on `hermes-agent.service` is an opinion (mkDefault
in the webui module). WebUI talking to a missing gateway is a bad
default.

## Checks

Keep:

- `checks.${system}.plugins` — plugin pytest / ruff as today
- `checks.${system}.mcp-proxy`

Add:

- `checks.${system}.modules` — `nixosSystem` eval with
  `services.hermesPnP.enable = true` and a dummy
  `services.hermes-agent.enable = true` (no secrets). Must evaluate.
- `checks.${system}.drop-in` — eval of official-only options (composer
  off) still evaluates; proves we did not break the library path.
- `checks.${system}.options` — assert the user-facing option paths
  exist (`services.hermesPnP.plugins.enable`, `webui.enable`,
  `toolbox.enable`, `runtime.mode`, `gbrain.enable`,
  `packageFixes.silenceMarkers`).

Do not require a full container image build in default checks.

## Implementation order

1. Docs: this file, README rewrite, AGENTS.md. No behavior change.
2. Flake inputs: add `hermes-agent`, `hermes-webui`. Re-export
   official modules. Composer `default.nix` imports official modules
   + existing plugins + mcp-proxy (still inert without enable).
3. `package.nix` — share env + optional silence wrap.
4. `agent.nix` + `webui.nix` — pairing defaults, gated on
   `hermesPnP.enable`.
5. `toolbox.nix` — small extraPackages set.
6. `runtime.nix` — extraBindMounts + `mode` (`upstream` implemented;
   `s6` may land as a stub that `throw`s "not yet" rather than a
   half-port). Prefer a complete upstream path over a broken s6 path.
7. `gbrain.nix` — thin optional hook.
8. Checks + example snippet in README.
9. Do **not** migrate rk3588 in this repo.

If time is tight, ship 1–5 + 7–8. `runtime.mode = "s6"` can wait.
`packageFixes` and WebUI pairing are not optional for a useful
composer.

## Acceptance

- `nix flake check` passes (eval + existing plugin/proxy tests).
- A config that only sets official `services.hermes-agent.*` and
  `services.hermesPnP.enable = true` evaluates and enables WebUI on
  127.0.0.1:8787 with the same user/package.
- `services.hermesPnP.plugins.enable = [ "model-router" ]` still
  materializes the symlink pair.
- `services.hermesPnP.enable = false` plus `plugins.enable = [ … ]`
  still works (library path).
- No new required options. A native user adds one `enable = true`.
- No HMC module. No gbrain systemd unit. No SOUL.md.
- Existing plugin Python and mcp-proxy behavior unchanged.

## Out of scope for v1 (write down, do not build)

- rk3588-nixos-nas cutover PR
- s6 runtime port (unless it is clean and complete)
- Declarative gbrain serve
- HMC
- Browser / CDP module
- Composio policy module
- Home-manager module
- darwin / Nix-on-Linux non-NixOS

## Implementation notes (v1, feat/pnp-composer)

Shipped as `feat(nix): implement v1 PnP composer`. Eval checks built and
passed: modules, drop-in, options, plugin tests, mcp-proxy tests.

Deltas from this document vs live upstream at implement time:

- Official module has no `container.extraPackages`. Toolbox writes
  `services.hermes-agent.extraPackages` and also
  `environment.systemPackages` when `container.enable` is false.
- GBrain URL is `mkDefault` on `services.hermes-agent.mcpServers.gbrain.url`,
  not inside `settings.mcp_servers`. `settings` is `deepConfigType`;
  `mkDefault` inside it is stored as a literal.
- `runtime.mode = "s6"` is a NixOS assertion, not `throw`. A `throw`
  inside `mkIf` is forced during module merge.
