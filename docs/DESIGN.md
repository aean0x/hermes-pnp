# Hermes PnP — Design

Opinionated NixOS composer for Hermes Agent. One flake input. Drop-in
on top of the official `services.hermes-agent` surface. WebUI is part of
the product. Site identity stays in the consumer flake.

This is not a host flake. It does not own secrets, hostnames, Telegram
IDs, mail routing, RAM caps, or SOUL.md. Browser CDP/dashboard provisioning
*is* a composer opinion (`services.hermesPnP.browser`); the engine choice
stays in the consumer.

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
};

services.hermesPnP = {
  enable = true;
  environmentFiles = [ config.sops.templates.hermesEnv.path ];
};
```

and get a cohesive agent + WebUI + first-party plugins + toolbox + MCP
proxy + CDP browser, with official options still working as documented.

A user who wants more control keeps writing `services.hermes-agent.*`
and `services.hermes-webui.*` exactly as the upstream modules declare
them. PnP only adds pairing, plugins, and a few extra modules.

## Non-goals

- Declarative GBrain *data* (PGLite, sources, embeddings, memory registry).
  Tertiary. We expose first-party GBrain *plugins*, an optional MCP URL
  hook, and — when `gbrain.enable` — a loopback `gbrain serve` unit.
  The consumer still bootstraps the CLI (`bun install -g`).
- Honcho, Telegram home-channel, Composio mail-filter policy.
  Those are site policy. The MCP *proxy mechanism* stays; the mail rules
  do not. HMC is an optional composer module (`hermesPnP.hmc.enable`),
  not a required default.
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
    lib.nix                      # shared helpers (docker --env, plugin dest)
    modules/
      default.nix                # composer: imports below
      agent.nix                  # official module + pairing opinions
      webui.nix                  # official webui + pairing defaults
      plugins.nix                # plugins list + extraPlugins + pluginInstall
      models.nix                 # seed official settings from models.*
      toolbox.nix                # everyday CLI buildEnv → /var/lib/hermes/toolbox/bin
      package.nix                # shared package + bundled-share env
      gbrain.nix                 # thin optional MCP URL + plugin env
      skills.nix                 # materialize first-party skills → $stateDir/skills
  plugins/                       # first-party plugins
  plugins/catalog.nix            # plugin name → path
  skills/                        # first-party skills
  skills/catalog.nix             # skill name → path
  services/mcp-proxy/            # services.hermesPnP.mcpProxy.* (alias: services.mcpProxy)
  services/browser/              # services.hermesPnP.browser (CDP + dashboard gate)
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
6. **HMC is opt-in.** `hermesPnP.hmc.enable` pins upstream and writes
   config.yaml. Off by default. Native compact stays the LLM summarizer.
7. **No secrets in JSON.** MCP credentials go through environmentFiles
   or the MCP proxy. `mcpServers` may contain URLs and headers that
   reference env vars; never raw tokens.
8. **Site policy stays out.** Telegram allowlists, home channel, RAM
   Percentage, Composio tool/account filters, hostnames, sops paths —
   consumer flake only. Browser CDP/dashboard provisioning is composer-owned;
   the engine (brave vs chromium) is a consumer choice.

### User-facing options

Official option names stay. Flatten PnP extras so a consumer flake
reads like a short list — comment a line to drop a thing.

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
- `services.hermesPnP.environmentFiles` — secrets drop-in. Forwarded
  to official `services.hermes-agent.environmentFiles`. Key list:
  `docs/hermes.env.example`.
- `services.hermesPnP.models.{low,medium,high}` — `{ provider, model }`.
  One block seeds official settings, model-router `config.json`, and
  WebUI commands. See **Three models**.
- `services.hermesPnP.plugins` — `listOf str` catalog names.
  Composer on defaults via `mkDefault` to model-router,
  tool-call-coherency, secret-handoff. Composer off defaults to `[]`.
- `services.hermesPnP.extraPlugins` — `attrsOf path` beside the catalog.
- `services.hermesPnP.webui.enable` — default `true` when composer
  is on. Escape hatch to run gateway-only.
- `services.hermesPnP.toolbox.enable` — default `true` when composer
  is on. Builds the everyday CLI `buildEnv` into
  `/var/lib/hermes/toolbox/bin` (container sees `/data/toolbox/bin` via the
  stateDir bind) and wires it onto the agent PATH.
- `services.hermesPnP.toolbox.extraPackages` — append-only.
- `services.hermesPnP.toolbox.hostPath` / `toolbox.binDir` — resolved
  host/container paths, exported for consumers to wire into units.
- `services.hermesPnP.container.enable` — default `false`. Sets official
  `services.hermes-agent.container.enable` + `backend` / `image`
  (`ubuntu:24.04`, docker). RAM caps and extra volumes stay official.
- `services.hermesPnP.hmc.enable` — default `false`. Pins
  hermes-context-manager as `extraPlugins` and creates `hmc_state`.
- `services.hermesPnP.gbrain.enable` — default `false`. Starts
  `gbrain-mcp-http` (`gbrain serve --http` on loopback), sets
  `services.hermes-agent.mcpServers.gbrain.url` (mkDefault), and
  exports `GBRAIN_MCP_URL` / `GBRAIN_TOKEN_FILE`. Appends the two
  gbrain plugins if missing. Does **not** ship PGLite, sources, or a
  memory registry.
- `services.hermesPnP.gbrain.url` — default `http://127.0.0.1:3131/mcp`.
- `services.hermesPnP.gbrain.bind` / `port` — serve listen (127.0.0.1:3131).
- `services.hermesPnP.gbrain.tokenFile` — default
  `${stateDir}/home/.gbrain/hermes-mcp.token`. Injected as env, never
  read into Nix.
- `services.hermesPnP.mcpProxy` — enable, listen, backends. Canonical
  tree (site policy still writes backends here). `services.mcpProxy`
  is an alias.
- `services.hermesPnP.browser.enable` / `package` / `engine` / `cdpPort`
  / `agent-browser` — persistent CDP browser + optional dashboard phone gate;
  seeds `BROWSER_CDP_URL` into the agent env. `runtime.*` is gone:
  consumers use official `container.extraVolumes` directly.
- `services.hermesPnP.packageFixes.silenceMarkers` — default `true`.
  Patch via PYTHONPATH. Escape hatch for when upstream ships the plural form.
- `services.hermesPnP.pluginInstall.*` — installer internals (`stateDir`,
  `user`, `group`, `webuiExtensionDir`). Not advertised.
- `services.mcpProxy.*` — alias of `services.hermesPnP.mcpProxy`.

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
explicitly set `plugins` / `extraPlugins` and `mcpProxy.enable`.
This preserves today's "library flake" use.

## Three models

Exactly three named models. Not numbered. No fourth model. User-facing
Nix, README, plugin.yaml, WebUI labels, and slash commands speak
`low` / `medium` / `high` only.

| name   | role                     | seeds                                   |
| ------ | ------------------------ | --------------------------------------- |
| low    | cheap helper             | mechanical auxiliary slots + unpinned cron    |
| medium | workhorse                | `settings.delegation` + reasoning auxiliary slots|
| high   | session identity + voice | `model.default`, `fallback_model`, rest |

Defaults (opinion, two strings each):

```nix
models.low    = { provider = "deepseek";  model = "deepseek-v4-flash"; };
models.medium = { provider = "deepseek";  model = "deepseek-v4-pro"; };
models.high   = { provider = "xai-oauth"; model = "grok-4.6"; };
```

When `hermesPnP.enable` (module `nix/modules/models.nix`):

- `settings.model.{provider,default}` ← high
- `settings.fallback_model.{provider,model}` ← high
- `settings.delegation.{provider,model}` ← medium
- `settings.cron.{model,model_provider}` ← low
- `settings.auxiliary.<slot> = { inherit (models.low or models.medium) provider model; reasoning_effort = "none"; }`

Auxiliary slots (opinion, not user options) match official
DEFAULT_CONFIG / rk3588 names: `title_generation` `compression`
`approval` `web_extract` `skills_hub` `mcp` `triage_specifier`
`kanban_decomposer` `profile_describer` `curator` `background_review`
`monitor` `memory_query_rewrite`. `background_review` `curator` `kanban_decomposer` ride `models.medium` (reasoning work); the rest ride `models.low`. Do not seed STT / TTS / vision.

`settings` is official `deepConfigType`. Do **not** wrap those leaves
in `mkDefault` — the merge stores the wrapper as a literal. Last writer
wins via `recursiveUpdate`; consumers override by assigning official
`services.hermes-agent.settings.*` after importing PnP.

Whenever `model-router` is in `plugins`, the installer writes
`config.json` + `webui/config.js` from the same `models` block. No
extra Nix overlay option.

Classifier / escalate policy: 4 consecutive tool errors on low, 3 on
medium, capped at `escalate_max` (high). No `final_voice`, no
`rest_on_high` — those were purged; high is reached by classification
or `/high`.

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
};
services.hermesPnP.enable = true;
services.hermesPnP.environmentFiles = [ config.sops.templates.hermesEnv.path ];
services.hermesPnP.plugins = [
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

Catalog stays the SoT (`plugins/catalog.nix`). Adding a plugin is: drop a
folder under `plugins/<name>`, add one catalog line. Skills follow the
same pattern (`skills/catalog.nix`).

Current first-party set (keep, do not rewrite Python unless Nix wiring
requires it, except `git-hook` which is the hook-only git
sync platform):

- `model-router`
- `tool-call-coherency`
- `gbrain-retrieval-reflex`
- `gbrain-memory-flush`
- `secret-handoff`
- `git-hook` — `pre_tool_call` ff-only pull on first read of
  a clean worktree; end of turn commits only this turn's porcelain
  delta and pushes. No sidecar script, no tools.

Installer rules (already true, keep):

- Empty `plugins` and no `extraPlugins` → no plugin files.
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

There is no `runtime.*` module. RAM caps and extra volumes stay
official. `hermesPnP.container.enable` is the composer knob that turns
on official `services.hermes-agent.container` (Ubuntu image, docker).
Off by default so native-only consumers stay native.

Extra host mounts are the official
`services.hermes-agent.container.extraVolumes` (`listOf str`, default
`[]`). Consumers use it directly — the composer-invented
`runtime.extraBindMounts` wrapper was deleted as scaffolding. Do not
re-add a wrapper around an official option.

Do not copy rk3588's 50% RAM cap, `/data/src` bind, or media mounts
into PnP defaults.

## Sandbox (webui + browser OCI)

Official `services.hermes-agent.container` already jails the gateway.
Official `services.hermes-webui` has no `container.*`. The composer
adds `hermesPnP.webui.container` and `hermesPnP.browser.container`
(same `docker create --network=host` + `/nix/store:ro` + identity hash
+ `start -a` as official). Both default on when
`hermesPnP.container.enable` is set. Opt out per-service.

Helper: `nix/lib/oci-container.nix`. Slim entrypoint (UID/GID +
setpriv, no sudo/apt). Upstream-shaped — lift into
nesquena/hermes-webui as `container.enable` when it lands.

WebUI container mounts: `/nix/store:ro`, agent stateDir → `/data`,
agent home → `/home/hermes`, webui stateDir same-path, `/etc/ssl:ro`.
Not `/etc/nixos`, not docker.sock. Terminal spawned from the WebUI
runs inside this container.

Browser container mounts: workspace, profile, cookies, logs, gate
state. Not hermes home, not `.hermes`, not `/etc`. Xvfb + browser
+ agent-browser dashboard share the container. `--no-sandbox` is fine: the
container is the jail.

Takeover stays local: one browser, two control planes.
- Agent: CDP `127.0.0.1:9222`
- Human: agent-browser dashboard on `listenAddress` (default `127.0.0.1`),
  reverse-proxied through Caddy with the same auth as the WebUI.
Do not bind `0.0.0.0:4848`. Do not open the firewall when loopback.
Set `browser.gate.publicUrl` to the Caddy URL the agent should relay
(`https://browser.example.com/`). No VNC password, no framebuffer.

Consumer recipe:

    services.caddy.proxyServices."browser.${domain}" = 4848;
    services.hermesPnP.browser.gate.publicUrl = "https://browser.${domain}/";

Do not Cloudflare-tunnel the browser host unless you want WAN
captcha handoff.

Do not require a full container image build in default flake checks.

## Toolbox

A `buildEnv` of everyday CLI tools, materialized to
`/var/lib/hermes/toolbox/bin` (container path `/data/toolbox/bin` via the
stateDir bind) and wired onto the agent PATH. This is the deployment's
"sauce": one PATH that works identically in the gateway, the WebUI, and
any host unit that sources it.

The opinion is "the agent can do real unix work" — not a bare-minimum set
and not "here is one person's workstation." The default set includes git, curl,
jq, ripgrep, file, unzip, python3 (with requests/pyyaml/toml). Browser
PATH aliases live in the browser module. `gh`, `docker`, `sops`, `age`, `nmap`, and
language toolchains are consumer `extraPackages`.

## GBrain (tertiary)

`nix/modules/gbrain.nix`, gated on `gbrain.enable` (default false):

- `url` / `bind` / `port` / `tokenFile`
- `mkDefault` `services.hermes-agent.mcpServers.gbrain`
- env for `gbrain-retrieval-reflex` / `gbrain-memory-flush`
- `gbrain-mcp-http` systemd unit: `gbrain serve --http` on loopback,
  PATH from the toolbox (or `~/.bun/bin`), EnvironmentFile from the
  agent. Binary is the consumer-bootstrapped bun-global CLI.

No PGLite. No sources. No memory registry. No `config.yaml` Bearer
rewrite — those stay in the consumer if needed.

Enabling the GBrain *plugins* does not require `gbrain.enable`. The
plugins no-op if the env is unset.

Operator one-shots live in `scripts/gbrain-setup.sh` and
`scripts/validate-gbrain.sh` (not Nix). See `docs/gbrain.md`.

## MCP proxy

`services.hermesPnP.mcpProxy`. Composer does not auto-enable it.
A consumer who wants Composio/GitHub auth injection turns it on and
points official `mcpServers` at `http://127.0.0.1:<port>/…`.
`services.mcpProxy` is a compatibility alias of the same tree.

Site-specific `tools.deny` / account filters stay in the consumer.
When the proxy is on, hermes-agent / hermes-webui wait for it.

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
  exist (`services.hermesPnP.models.low.model`, `plugins` is a list,
  `webui.enable`, `toolbox.enable`, `browser.enable`, `gbrain.enable`,
  `packageFixes.silenceMarkers`). Assert no `plugins.enable` /
  `plugins.modelRouter`. Assert composer seeds
  `settings.auxiliary.triage_specifier.model` from `models.low` and
  `settings.model.default` from `models.high`.

Do not require a full container image build in default checks.

## Implementation order

1. Docs: this file, README rewrite, AGENTS.md. No behavior change.
2. Flake inputs: add `hermes-agent`, `hermes-webui`. Re-export
   official modules. Composer `default.nix` imports official modules
   + existing plugins + mcp-proxy (still inert without enable).
3. `package.nix` — share env + optional silence wrap.
4. `agent.nix` + `webui.nix` — pairing defaults, gated on
   `hermesPnP.enable`.
5. `toolbox.nix` — everyday CLI buildEnv.
6. `services/browser/` — persistent CDP browser + optional dashboard gate.
7. `gbrain.nix` — thin optional hook.
8. Checks + example snippet in README.
9. Do **not** migrate rk3588 in this repo.

If time is tight, ship 1–5 + 7–8. `packageFixes` and WebUI pairing are
not optional for a useful composer.

## Acceptance

- `nix flake check` passes (eval + existing plugin/proxy tests).
- A config that only sets official `services.hermes-agent.*` and
  `services.hermesPnP.enable = true` evaluates and enables WebUI on
  127.0.0.1:8787 with the same user/package.
- `services.hermesPnP.plugins = [ "model-router" ]` still
  materializes the symlink pair.
- `services.hermesPnP.enable = false` plus `plugins = [ … ]`
  still works (library path).
- No new required options. A native user adds one `enable = true`.
- HMC is opt-in (`hmc.enable`), not a required default. GBrain serve
  is opt-in (`gbrain.enable`). No SOUL.md.
- Existing plugin Python and mcp-proxy behavior unchanged.

## Out of scope (write down, do not build)

- Composio policy module
- Home-manager module
- darwin / Nix-on-Linux non-NixOS

## Implementation notes (v1, feat/pnp-composer)

Shipped as `feat(nix): implement v1 PnP composer`. Eval checks built and
passed: modules, drop-in, options, plugin tests, mcp-proxy tests.

Deltas from this document vs live upstream at implement time:

- GBrain URL is `mkDefault` on `services.hermes-agent.mcpServers.gbrain.url`,
  not inside `settings.mcp_servers`. `settings` is `deepConfigType`;
  `mkDefault` inside it is stored as a literal.

## Implementation notes (named models + option beauty)

Shipped as `feat(pnp): named low/medium/high models + option beauty`.

- `plugins` flattened to `listOf str`; `extraPlugins` is a sibling
  attrset. `pluginInstall.*` holds installer internals.
- Deleted `plugins.modelRouter.settings`. model-router config is
  generated from `hermesPnP.models` whenever that plugin is listed.
- `gbrain.enable` appends the two gbrain plugins if missing. Enabling
  those plugins does not require `gbrain.enable`.
- Official `settings` seeds live in `nix/modules/models.nix`. Leaves
  are not `mkDefault` (deepConfigType stores the wrapper as a literal).
- Live upstream agrees with spec keys: `model.{provider,default}`,
  `fallback_model.{provider,model}`, `delegation.{provider,model}`,
  `cron.{model,model_provider}`, `auxiliary.<slot>.{provider,model,reasoning_effort}`.
  Official DEFAULT_CONFIG also has `fallback_providers`, `goal_judge`,
  `tts_audio_tags`, `moa_*`, `vision` — we do not seed those.
- Plugin surface is `low` / `medium` / `high`.

## Implementation notes (reorg: toolbox buildEnv + browser, drop runtime)

Shipped as `refactor: fold toolbox buildEnv + browser CDP into composer`;
the rk3588 cutover PR reworks the host tree to match.

- `toolbox.nix` is now the full everyday CLI `buildEnv` (was a small
  `extraPackages` passthrough). Materializes to
  `/var/lib/hermes/toolbox/bin`, container path `/data/toolbox/bin`;
  exports `hostPath` for consumers to wire into units.
- `services/browser/nix/module.nix` is new: persistent CDP browser + optional dashboard
  gate. Seeds `BROWSER_CDP_URL` + `BU_CDP_URL` and the gate URL
  into `services.hermes-agent.environment`. Engine (`package`/`engine`)
  is a consumer choice. Chromium-family PATH aliases live here.
- `runtime.nix` is deleted. `runtime.extraBindMounts` was invented
  scaffolding; the official `container.extraVolumes` is used directly.
  There is no `runtime.mode`; the s6 port is abandoned.
- Plugin catalog lives at `plugins/catalog.nix`. Skills catalog at
  `skills/catalog.nix`. First-party skill is `browser` (flake defaults
  only; consumer extras via `skills.extraSkills`).
