# Hermes PnP — Design

Opinionated NixOS composer for Hermes Agent. One flake input. Drop-in
on top of official `services.hermes-agent`. WebUI is part of the
product. Site identity stays in the consumer flake.

This is not a host flake. It does not own secrets, hostnames, Telegram
IDs, mail routing, RAM caps, or SOUL.md. Browser CDP/dashboard
provisioning is a composer opinion (`services.hermesPnP.browser`); the
engine stays in the consumer.

## Goal

A native Hermes NixOS user adds one extra enable:

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

and gets agent + WebUI + first-party plugins + toolbox + MCP proxy +
CDP browser. Official options still work as documented.

A user who wants more control keeps writing `services.hermes-agent.*`
and `services.hermes-webui.*` as upstream declares them. PnP adds
pairing, plugins, and a few extra modules.

## Non-goals

- Declarative GBrain data (PGLite, sources, embeddings, memory registry).
  First-party GBrain plugins, an optional MCP URL hook, and — when
  `gbrain.enable` — a loopback `gbrain serve` unit. The consumer
  bootstraps the CLI (`bun install -g`).
- Honcho, Telegram home-channel, Composio mail-filter policy. Site
  policy. The MCP proxy mechanism stays; the mail rules do not. HMC is
  opt-in (`hermesPnP.hmc.enable`).
- Shipping SOUL.md / USER.md / MEMORY.md from Nix.
- A parallel settings DSL that replaces the official option tree.
- A full host flake (networking, sops, disks, users beyond hermes).

## Official modules

PnP imports `hermes-agent.nixosModules.default` and
`hermes-webui.nixosModules.default`. It does not re-declare
`services.hermes-agent.enable`, `settings`, `environmentFiles`,
`container.*`, `documents`, `mcpServers`, `extraPythonPackages`, or
`extraDependencyGroups`.

Official options are the user-facing config language. PnP opinions
land as `mkDefault` or implicit pairing on that tree.

Double-import is fine: the same option set merges.

Flake inputs (followed, not vendored):

- `nixpkgs`
- `hermes-agent` — `github:NousResearch/hermes-agent`
- `hermes-webui` — `github:nesquena/hermes-webui`
- `mcp-proxy` — in-tree `pkgs/mcp-proxy`

Exports:

- `nixosModules.default` — composer (agent + webui + pnp extras)
- `nixosModules.agent` / `nixosModules.webui` — official modules only
- `nixosModules.plugins` / `mcp-proxy` / `toolbox` / `browser` / `skills`

## Layout

```
hermes-pnp/
  flake.nix                      # inputs + output map
  lib/                           # mkDockerEnv, remapStatePath, mkOciJail
  modules/                       # nixosModules — options next to config
    default.nix                  # composer
    enable.nix                   # enable, environmentFiles, container.*
    webui/                       # pairing + host harden + OCI jail
    browser/                     # CDP browser + dashboard
    mcp-proxy.nix                # services.hermesPnP.mcpProxy (alias: services.mcpProxy)
  pkgs/                          # packages + overlays.default
  checks/                        # eval + plugin/proxy tests
  examples/                      # consumer snippets; evalled by checks
  plugins/                       # first-party plugin source + catalog.nix
  skills/                        # first-party skill source + catalog.nix
  scripts/                       # operator one-shots (not Nix)
```

`services.hermesPnP.enable` turns on composer opinions (WebUI pairing,
share env, plugin dest). Plugins and mcp-proxy stay independently
selectable.

## Opinion vs option

### Core opinions — no knobs

If a consumer needs something else, they skip the composer or override
the official option PnP set via `mkDefault`.

1. **One identity.** Gateway and WebUI share `user`, `group`, `package`,
   and the same store-safe env map.
2. **Plugin dest.** Materialize to `$stateDir/plugins/<name>` and link
   `$stateDir/.hermes/plugins/<name>` → `../../plugins/<name>`. Official
   `extraPlugins` (`listOf package`) stays available for consumer
   packages.
3. **WebUI bind.** `127.0.0.1:8787`, `hermesHome = ${stateDir}/.hermes`,
   same user as the agent. Opening the firewall or binding `0.0.0.0` is
   a consumer override of `services.hermes-webui.*`.
4. **Bundled share env.** `HERMES_BUNDLED_*` (and optional silence
   `PYTHONPATH`) go on `services.hermes-agent.environment` and
   `services.hermes-webui.extraEnvironment`. The official wrapper
   `--set`s those for the jailed gateway. Store paths stay off
   `container.extraOptions` (official identity hashes that list).
5. **No identity-from-Nix.** PnP never writes SOUL / USER / MEMORY.
6. **HMC is opt-in.** `hermesPnP.hmc.enable` pins upstream and writes
   config.yaml. Hermes compact stays the LLM summarizer.
7. **No secrets in JSON.** MCP credentials go through environmentFiles
   or the MCP proxy. `mcpServers` may reference env vars; never raw tokens.
8. **Site policy stays out.** Telegram allowlists, home channel, RAM
   caps, Composio filters, hostnames, sops paths — consumer flake.
   Browser CDP/dashboard is composer-owned; the engine is a consumer
   choice.

### Official (passthrough)

- `services.hermes-agent.enable`
- `services.hermes-agent.settings`
- `services.hermes-agent.environmentFiles` / `environment`
- `services.hermes-agent.documents` / `mcpServers`
- `services.hermes-agent.container.*`
- `services.hermes-agent.extraPythonPackages` / `extraDependencyGroups`
- `services.hermes-agent.user` / `group` / `stateDir` / `workingDirectory`
- `services.hermes-webui.*`

### PnP extras

- `services.hermesPnP.enable` — composer on. Default `false`.
- `services.hermesPnP.environmentFiles` — forwarded to official
  `environmentFiles`. Key list: `docs/hermes.env.example`.
- `services.hermesPnP.models.{low,medium,high}` — `{ provider, model }`.
- `services.hermesPnP.plugins` — `listOf str`. Composer on defaults
  via `mkDefault` to model-router, tool-call-coherency, secret-handoff.
- `services.hermesPnP.extraPlugins` — `attrsOf path` beside the catalog.
- `services.hermesPnP.webui.enable` — default `true` when composer is on.
- `services.hermesPnP.toolbox.enable` — default `true` when composer is
  on. buildEnv at `/var/lib/hermes/toolbox/bin` (`/data/toolbox/bin`
  in the jail).
- `services.hermesPnP.toolbox.extraPackages` — append-only.
- `services.hermesPnP.toolbox.hostPath` / `containerPath` /
  `toolboxDir` / `containerToolboxDir`.
- `services.hermesPnP.container.enable` — default `false`. Sets official
  `services.hermes-agent.container.enable` + `backend` / `image`
  (`ubuntu:24.04`, docker). RAM caps and extra volumes stay official.
- `services.hermesPnP.hmc.enable` — default `false`.
- `services.hermesPnP.gbrain.enable` — default `false`. Loopback
  `gbrain serve`, `mcpServers.gbrain.url`, plugin env, literal Bearer
  rewrite after official config merge. Appends the two gbrain plugins
  if missing. No PGLite, sources, or memory registry. CLI is
  `scripts/gbrain-setup.sh`.
- `services.hermesPnP.gbrain.url` / `bind` / `port` / `tokenFile`.
- `services.hermesPnP.mcpProxy` — enable, listen, backends,
  `clientAuth` (`none` / `token`), `clientTokenFile`.
  `services.mcpProxy` is an alias. Composer sets `clientAuth` to
  `token` via `mkDefault`; à-la-carte stays `none`.
- `services.hermesPnP.browser.enable` / `package` / `engine` / `cdpPort`
  / `cdpAllowOrigins` / `gate.*` — persistent CDP browser + dashboard.
  Seeds `BROWSER_CDP_URL` and `settings.browser.{cdp_url,engine}`. Extra
  host mounts use official `container.extraVolumes`.
- `services.hermesPnP.packageFixes.silenceMarkers` — default `true`.
  Autonomous silence match via PYTHONPATH.
- `services.hermesPnP.pluginInstall.*` — installer internals. Not
  advertised.

### `mkDefault` when composer is on

- `services.hermes-webui.enable = true` (unless `hermesPnP.webui.enable = false`)
- `services.hermes-webui.user/group` = agent user/group
- `services.hermes-webui.agent.package` = agent package
- `services.hermes-webui.hermesHome` = `${agent.stateDir}/.hermes`
- `services.hermes-webui.host = "127.0.0.1"`
- `services.hermes-webui.port = 8787`
- `services.hermes-webui.environmentFiles` = agent environmentFiles

When `services.hermesPnP.enable = false`, PnP is inert except
explicit `plugins` / `extraPlugins`, `mcpProxy.enable`, and opt-in
`gbrain` / `hmc`.

## Three models

Exactly three named models. User-facing Nix, README, plugin.yaml,
WebUI labels, and slash commands speak `low` / `medium` / `high` only.

| name   | role                     | seeds                                    |
| ------ | ------------------------ | ---------------------------------------- |
| low    | cheap helper             | mechanical auxiliary slots + cron        |
| medium | workhorse                | `settings.delegation` + reasoning slots  |
| high   | session identity + voice | `model.default`, `fallback_model`        |

```nix
models.low    = { provider = "deepseek";  model = "deepseek-v4-flash"; };
models.medium = { provider = "deepseek";  model = "deepseek-v4-pro"; };
models.high   = { provider = "xai-oauth"; model = "grok-4.6"; };
```

When `hermesPnP.enable` (`modules/models.nix`):

- `settings.model.{provider,default}` ← high
- `settings.fallback_model.{provider,model}` ← high
- `settings.delegation.{provider,model}` ← medium
- `settings.cron.{model,model_provider}` ← low
- `settings.auxiliary.<slot> = { inherit (models.low or models.medium) provider model; reasoning_effort = "none"; }`

Slots match official DEFAULT_CONFIG. Medium: `background_review`,
`curator`, `kanban_decomposer`. Low: `title_generation`, `compression`,
`approval`, `web_extract`, `skills_hub`, `mcp`, `triage_specifier`,
`profile_describer`, `monitor`, `memory_query_rewrite`. Do not seed
vision, tts, moa, or goal_judge.

`settings` is official `deepConfigType`. Do not wrap those leaves in
`mkDefault` — the merge stores the wrapper as a literal. Last writer
wins via `recursiveUpdate`. Consumers assign official
`services.hermes-agent.settings.*` after importing PnP.

When `model-router` is in `plugins`, the installer writes `config.json`
+ `webui/config.js` from the same `models` block.

Classifier: 4 consecutive tool errors on low, 3 on medium, cap
`escalate_max` (high). High is reached by classification or `/high`.

## Plugins

Catalog is the SoT (`plugins/catalog.nix`). Add a plugin: drop
`plugins/<name>/`, add one catalog line. Skills follow the same
pattern (`skills/catalog.nix`: `browser`, `retrieval-reflex`,
`gbrain-http-auth`; consumer trees via `skills.extraSkills`).

First-party: `model-router`, `tool-call-coherency`,
`gbrain-retrieval-reflex`, `gbrain-memory-flush`, `secret-handoff`,
`git-hook` (ff-only pull on first read of a clean worktree; end of
turn commits this turn's porcelain delta and pushes).

- Empty `plugins` and no `extraPlugins` → no plugin files.
- Materialize to `$stateDir/plugins/<name>`.
- Discover via relative symlink under `$stateDir/.hermes/plugins/`.
- Dest stays under official `stateDir` (native `ReadWritePaths`).

Do not install first-party plugins via official `extraPlugins`.

## Package / share env

`modules/package.nix`:

1. Optional silence-marker wrap (`packageFixes.silenceMarkers`).
2. Same `services.hermes-agent.package` for gateway and WebUI.
3. Share map: `HERMES_BUNDLED_PLUGINS`, `HERMES_BUNDLED_SKILLS`,
   `HERMES_OPTIONAL_SKILLS`, `HERMES_BUNDLED_LOCALES`,
   `HERMES_OPTIONAL_MCPS`, `HERMES_WEB_DIST`, `HERMES_TUI_DIR`, plus
   optional silence `PYTHONPATH`.
4. Apply that map to `environment{}` and WebUI `extraEnvironment`.
   Not official `container.extraOptions`.

Forward `extraDependencyGroups` / `extraPythonPackages`. Do not
default extras. Native `full` already has what most users need.

## Container

`hermesPnP.container.enable` turns on official
`services.hermes-agent.container` (Ubuntu, docker). Off by default.

Extra host mounts are official `container.extraVolumes`. Official
agent RAM caps stay `extraOptions`. Composer WebUI/browser jails
use first-class `*.container.memory` / `cpus` / `shmSize` /
`oomScoreAdj` / `memorySwap`. `extraOptions` is the escape hatch.
Do not `mkForce` extraOptions just to set RAM.

Official `services.hermes-webui` has no `container.*`. The composer
adds `hermesPnP.webui.container` and `hermesPnP.browser.container`.
Both default on when `hermesPnP.container.enable` is set.

`lib/oci-container.nix` (`mkOciJail`): `--user` host hermes uid,
`--init` (tini), `--cap-drop=ALL`, `--read-only`, nosuid tmpfs
`/tmp`+`/run`, `--security-opt=no-new-privileges` after
`extraOptions`. Consumers cannot drop `--init`. Browser jail
supervises the engine (restart loop + `logDir/supervisor.log`); a
tab OOM must not exit the container. Identity is
`/var/lib/hermes-oci/<name>` (root 0700). Docker backend `requires
docker.service`. `--network=host` so the jail reaches the same
loopback services as native hermes. Host-native flags live in
`lib/harden-host.nix`. Path remaps (`stateDir` → `/data`,
`${stateDir}/home` → `/home/hermes`) live in `lib.remapStatePath`.
Host `/home/hermes` (gbrain activation, only if that path is missing)
is an alias of `${stateDir}/home`. Durability is `${stateDir}/home`
on the host, not the alias.
WebUI CA/gitconfig binds stay in `modules/webui/container.nix`.

Jail image is ubuntu + `/nix/store:ro`. WebUI `extraVolumes` is
independent of the agent list.

`hermesPnP.browser.maxTabs` (default 2) adds
`--renderer-process-limit` and a CDP prune loop. The container
supervisor drops Chromium session-restore files on each engine
start. Gate watchdog is dashboard HTTP + CDP, not
`session info --json` (0.34 has no `connectionMethod`; grepping it
reconnects every 5s). Leftover `.agent-browser` sockets/version
under gateHome are wiped on start so an old store path cannot
keep a 0.27 daemon alive. The gate always `callPackage`s
`pkgs/agent-browser.nix` (0.34 musl). Do not use
`pkgs.agent-browser or pin` — consumers typically skip
`overlays.default`, so nixpkgs 0.27.0 would win.

`hermesPnP.admin.enable` (off by default) is a host unix socket
at `/run/hermes-admin/admin.sock` (0660, group hermes). The
allowlist is `status` / `restart` / `reset-failed` of
`hermes-agent`, `hermes-webui`, `hermes-browser`. Restart also
runs `reset-failed` and has a 15s cooldown. Jails bind that
directory only — never `/run`, never the docker socket. CLI:
`hermes-admin`. Sudo inside the jail cannot work
(`no-new-privileges`).

WebUI mounts: `/nix/store:ro`, agent stateDir → `/data`, agent home →
`/home/hermes`, webui stateDir same-path, nss-cacert onto Ubuntu
`/etc/ssl/certs/ca-certificates.crt` + `/etc/ssl/cert.pem`,
`/etc/gitconfig:ro`. Not host `/etc/ssl` or `/etc/static` (Docker
bind-mounts the NixOS `/etc/static` symlink as an empty dir). Not
`/etc/nixos`, not docker.sock.

Browser mounts: workspace, profile, cookies, logs, gate. Not hermes
home, not `.hermes`, not `/etc`. `--no-sandbox`: the container is the
jail.

Agent jail also bind-mounts `/etc/gitconfig:ro` when
`hermesPnP.git.credentialHelper.enable` (composer default). That file
is `programs.git` on the host: credential helper only — no
`user.name` / `user.email`. The helper (`scripts/git-credential-github-env`)
feeds `GITHUB_PAT` / `GH_TOKEN` for `github.com` HTTPS (git-hook
push/pull). No token → exit 0. Nix cannot see sops at eval, so the
helper is not gated on the secret existing.

When official `container.enable` is on, **stable** jail paths go on
`container.extraOptions --env` (`mkDockerEnv`), not `$HERMES_HOME/.env`:

- `HERMES_BROWSER_PROFILE=/data/browser-profile`
- `GBRAIN_TOKEN_FILE=/home/hermes/.gbrain/hermes-mcp.token`
- toolbox `PATH` / `HERMES_PYTHON` (`~/.venv` then `/data/toolbox/bin`)

Activation drops a host `HERMES_BROWSER_PROFILE` from `.env` in jail
mode so the remapped `--env` wins. CDP / gate URLs stay on
`environment{}` (`127.0.0.1`). Native toolbox lands on official
`extraPackages`.

One browser, two control planes:

- Agent: CDP `127.0.0.1:9222`
- Human: dashboard on `listenAddress` (default `127.0.0.1`), via Caddy

`--remote-allow-origins` is HTTP origins (scheme+host+port), not CIDR.
Default is loopback CDP + dashboard, plus `gate.publicUrl` / a
non-loopback `listenAddress` when set. `[ "*" ]` is the wildcard.

Do not bind `0.0.0.0:4848` unless you accept an unauthenticated
screencast. Do not open the firewall when loopback. Set
`browser.gate.publicUrl` to the Caddy URL.

```
services.caddy.proxyServices."browser.${domain}" = 4848;
services.hermesPnP.browser.gate.publicUrl = "https://browser.${domain}/";
```

Default flake checks do not build a container image.

## Toolbox

`buildEnv` at `/var/lib/hermes/toolbox/bin` (`/data/toolbox/bin` in
the jail). Native gateway PATH is official `extraPackages`. Jail PATH
is extraOptions `--env`.

Default set includes git, curl, jq, ripgrep, file, unzip, python3
(with requests/pyyaml/toml/pip), gh, age. Browser PATH aliases live in the
browser module. `docker`, `sops`, `nmap`, and language toolchains are
consumer `extraPackages`. Activation seeds a writable `~/.venv` from
that interpreter (`HERMES_PYTHON`); the Nix prefix is immutable and
has user-site disabled. ubuntu:24.04 has no distro python and the
jail is read-only (no apt).

## GBrain

`modules/gbrain.nix`, `gbrain.enable` (default false):

- `url` / `bind` / `port` / `tokenFile`
- `mkDefault` `services.hermes-agent.mcpServers.gbrain` (url + timeouts;
  not headers — token is minted at runtime)
- activation after `hermes-agent-setup` re-applies literal Bearer
- env for the two gbrain plugins
- `gbrain-mcp-http`: `gbrain serve --http` on loopback. Binary is the
  consumer-bootstrapped bun-global CLI.

Enabling the plugins does not require `gbrain.enable`. They no-op if
the env is unset.

Operator scripts: `scripts/gbrain-setup.sh` (needs the unit),
`scripts/validate-gbrain.sh`, `scripts/gbrain-wire-config.py`. See
`docs/gbrain.md`.

## MCP proxy

`services.hermesPnP.mcpProxy`. Composer does not auto-enable it.
Point official `mcpServers` at `http://127.0.0.1:<port>/…`.
`services.mcpProxy` is an alias.

`clientAuth` is `none` on the library path so the proxy can run
without Hermes. Composer sets `mkDefault "token"`: a host token file,
`X-MCP-Proxy-Token: ${MCP_PROXY_TOKEN}` on mcpServers named like a
backend, and `/run/mcp-proxy/client.env` on official
`environmentFiles`. `/healthz` stays open. Set `clientAuth = "none"`
to turn that off.

Site-specific `tools.deny` / account filters stay in the consumer.
When the proxy is on, hermes-agent and hermes-webui wait for it
(including the WebUI jail).

## WebUI

Official module plus pairing defaults above. No forked WebUI package.
Host unit waits for `hermes-agent.service`. Pairing sets
`HERMES_WEBUI_TRUST_FORWARDED_PROTO` / `SECURE` and
`HERMES_WEBUI_TRUSTED_PROXY_CIDRS=127.0.0.1/32,::1/128` (Caddy on
this host). Jail entrypoint `umask 0077`; host unit `UMask=0077`.

## Checks

`nix flake check` stays eval-cheap (dummy agent/webui packages).

- `checks.${system}.plugins` — plugin pytest / ruff
- `checks.${system}.mcp-proxy`
- `checks.${system}.modules` — composer on, dummy packages
- `checks.${system}.drop-in` — composer off, official-only options
- `checks.${system}.options` — user-facing option paths; no
  `plugins.enable` / `plugins.modelRouter`; composer seeds
  `settings.auxiliary.triage_specifier.model` from `models.low` and
  `settings.model.default` from `models.high`
- `checks.${system}.examples` — every file in `examples/`

## Out of scope

- Composio policy module
- Home-manager module
- darwin / Nix-on-Linux non-NixOS
