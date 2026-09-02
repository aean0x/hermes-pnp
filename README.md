# Hermes PnP (Plug n Pray)

Uncomplicated, uncompromising, hardened Hermes. One flake input
replaces a weekend of pairing, PATH, jail, and plugin work with a
stack we run and test: official agent + WebUI, Ubuntu OCI jails,
a curated toolbox, a persistent CDP browser, three-model routing
that actually saves tokens, and the plugins that survived production.
You bring secrets and site identity. We bring the shortcuts, the
sandboxing, and the add-ons that should have been one enable.

```nix
# flake.nix:  inputs.hermes-pnp.url = "github:aean0x/hermes-pnp";
{
  imports = [ inputs.hermes-pnp.nixosModules.default ];

  services.hermes-agent.enable = true;

  services.hermesPnP = {
    enable = true;
    environmentFiles = [ config.sops.templates.hermesEnv.path ];

    container.enable = true; # Ubuntu OCI jails (agent + WebUI + browser)

    browser.package = pkgs.brave; # engine follows package.meta.mainProgram

    models.low       = { provider = "deepseek";  model = "deepseek-v4-flash"; }; # cheap helper, cron
    models.medium    = { provider = "deepseek";  model = "deepseek-v4-pro"; };   # workhorse, delegation
    models.high      = { provider = "xai-oauth"; model = "grok-4.6"; };          # session voice + fallback
    # models.auxiliary = { provider = "deepseek"; model = "deepseek-v4-flash"; }; # aux tasks; reasoning_effort = "none"
    # models.low.best_for = [ "Short acknowledgements" ]; # classifier matrix; plugin defaults otherwise

    plugins = [
      "model-router"
      "tool-call-coherency"
      "secret-handoff"
      "git-hook"
    ];

    # services.hermes-agent.extraPackages = [ pkgs.sops ]; # folded into the toolbox
    # extraPluginDirs.my-plugin = ./plugins/my-plugin;
    # skills.extraSkills.ops = ./skills/ops;

    # browser.gate.publicUrl = "https://browser.example.com/"; # Caddy in the consumer

    # mcpProxy.enable = true;
    # mcpProxy.backends.github = {
    #   upstream = "https://api.githubcopilot.com/mcp/";
    #   auth.mode = "passthrough";
    # };

    # hmc.enable = true;

    # gbrain.enable = true; # then scripts/gbrain-setup.sh — see GBrain below
  };

  # services.hermes-agent.mcpServers.github.url = "http://127.0.0.1:3140/github";
  # services.hermes-agent.container.extraVolumes = [ "/home/alice/src:/data/src" ];
  # services.hermesPnP.webui.container.extraVolumes = [ "/home/alice/src:/data/src" ];

  # Site author stays here. Composer adds credential.helper for github.com HTTPS.
  # programs.git.config.user = { name = "you"; email = "you@users.noreply.github.com"; };
}
```
[Living example of hermes-pnp in prod](https://github.com/aean0x/rk3588-nixos-nas/tree/main/hosts/system/hermes)

Official `services.hermes-agent.*` / `services.hermes-webui.*` still
work. `hermesPnP.enable = false` is the library path (plugins +
mcp-proxy only). Keys for every `models.*` provider go in the env
file; the list is `docs/hermes.env.example`.

## Hermes Agent

Composer turns on the official gateway and pairs everything else to
it: same user, same `HERMES_HOME`, same secrets file. A `buildEnv`
toolbox lands at `$stateDir/toolbox/bin` (`/data/toolbox/bin` in the
jail) — python3 plus a curated everyday CLI (git, rg, jq, bun, node,
ffmpeg, gh, age, …). Official `extraPackages` fold into that env
(native profile + jail bind). Jail PATH is `--env`. `container.enable`
is the recommended host: Ubuntu 24.04, `/nix/store:ro`, no view of
`/etc/nixos`. Network follows official `container.network` when that
option exists (else host). Extra binds stay on official
`container.extraVolumes`. RAM caps stay in the consumer.

## Hermes WebUI

Paired automatically on `127.0.0.1:8787` (no WAN bind). Same
user/group/package/env files as the agent, `hermesHome` pointed at
`${stateDir}/.hermes`, forwarded-proto / trusted-proxy set for a
loopback Caddy. When `container.enable` is on, WebUI is its own OCI
jail — terminals it spawns see only the binds you add. model-router
ships a WebUI extension (`/low` `/medium` `/high` `/auto`). Caddy and
the public hostname are consumer work. Set `webui.enable = false` for
gateway-only.

## Browser (CDP + browser-ui gate)

A sticky Chromium-family profile on loopback CDP `:9222` plus a
browser-ui cast on `:4848` for human captcha / phone handoff.
The agent attaches at `127.0.0.1:9222`; humans hit the gate (put
`gate.publicUrl` on your Caddy, LAN/Tailscale, not a public tunnel).
`--remote-allow-origins` is computed from loopback + that URL. Engine
is `browser.package` (Brave above). Profile, cookies, and logs live
under the agent stateDir; the jail does not mount hermes home or
`/etc`.

`browser.profileImport` seeds the sticky profile from a Chromium
user-data dir on the build machine — cookies, saved logins,
preferences — so the browser comes up already logged in. The copy is
filtered (auth files only, no Cache / WAL sidecars) and one-shot
(empty profile or `overwrite = true`), so gate logins stay sticky.
Reading the source path at eval is impure: `nixos-rebuild switch
--impure`. Example in `examples/browser.nix`.

## GBrain

Optional. GBrain is a bun-global CLI + PGLite + a minted HTTP Bearer
that Hermes will not expand from `${GBRAIN_REMOTE_TOKEN}` — it does
not drop into a declarative host on its own. The hook starts loopback
`gbrain serve` (`gbrain-mcp-http` on `:3131`), sets the MCP URL, and
re-applies a **literal** Bearer after official config merge. Then run
[`scripts/gbrain-setup.sh`](scripts/gbrain-setup.sh) (CLI, init, mint,
import/embed). Operator notes: [`docs/gbrain.md`](docs/gbrain.md).

`gbrain.enable` also installs the two plugins (you can list them
without the hook; they no-op if the env is unset):

- **gbrain-retrieval-reflex** — ambient `volunteer_context` / query
  into the next turn
- **gbrain-memory-flush** — nudge durable facts out of MEMORY.md
  via `put_page` when the file is fat

Never a second `gbrain serve`. Never `autopilot --install`.

## MCP proxy

Loopback reverse proxy (`127.0.0.1:3140`). Hermes talks to
`/backend`; this process injects host-held secrets
(`LoadCredential`, never JSON) and applies tool allow/deny.
`auth.mode = auto` injects when secrets exist, else passthrough.
Composer defaults `clientAuth = token` (`X-MCP-Proxy-Token`);
à-la-carte stays `none`. Point official `mcpServers.<name>.url` at
the proxy path. OAuth stays in the MCP client.

## HMC

Opt-in [hermes-context-manager](https://github.com/NousResearch/hermes-context-manager)
as an extra plugin. Composer pins the src and writes its
`config.yaml`. Hermes compact stays the LLM summarizer; HMC does
per-tool work (dedupe, purge errors, cap). Token savings on long
tool traces.

## Plugins

Materialize to `$stateDir/plugins/<name>`, discovered via
`$stateDir/.hermes/plugins/<name>`. First-party plugins are not
installed through official `extraPlugins`. `extraPluginDirs` is
`attrsOf path` for your own trees (`extraPlugins` is a renamed alias).

**model-router** (v0.8.4) — per-turn low / medium / high, labelled
Quick / Standard / Expert. Auto classifies all three; `high` is only
money / irreversible / security. Pins: `/low` `/medium` `/high`
`/auto`. Writes `config.json` + WebUI extension from
`hermesPnP.models` (model, provider, label, short, best_for). Catalog
JSON holds labels / `best_for` / escalate_* defaults, not model IDs.
Official aux tasks use `models.auxiliary`, not a router tier.

**tool-call-coherency** — unwrap double-nested `tool_call`, rewrite
bare skill names, stop Grok/OpenRouter thrash on MCP and deferred
tools.

**secret-handoff** — `request_secret` asks via stock clarify, pastes
through a direct CDP websocket, returns status only. Never lands on
disk or in the tool result.

**git-hook** — fetch/pull before reads; commit/push only the files
this turn dirtied. `GIT_HOOK_COMMIT=0` / `GIT_HOOK_PUSH=0` to mute.
HTTPS GitHub uses `GITHUB_TOKEN` via the credential helper.

GBrain plugins live under [GBrain](#gbrain).

## Misc

**Git.** Composer installs `git-credential-github-env` on
`programs.git` (fail-open if `GITHUB_TOKEN` / `GH_TOKEN` is unset).
When that helper is on, toolbox `gh` wraps `git credential fill` into
`GH_TOKEN`. `user.name` / `user.email` stay in the consumer.

**Secrets.** One rendered env file on `environmentFiles`. 

Credits:
[open-world-project/model-router](https://github.com/open-world-project/model-router)
for the cheap/work/voice idea.
