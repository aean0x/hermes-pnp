# Hermes PnP (Plug n Pray)

Opinionated NixOS composer for Hermes Agent. One flake input. Drop-in
on top of official `services.hermes-agent`. WebUI is part of the
product. Site identity stays in the consumer flake.

This is not a host flake. It does not own secrets, hostnames, Telegram
IDs, mail routing, RAM caps, or SOUL.md. Browser CDP/dashboard is a
composer opinion (`services.hermesPnP.browser`); the engine is a
consumer choice.

## Drop-in

```nix
{
  imports = [ inputs.hermes-pnp.nixosModules.default ];

  services.hermes-agent = {
    enable = true;
  };

  services.hermesPnP = {
    enable = true;
    environmentFiles = [ config.sops.templates.hermesEnv.path ];
    browser.package = pkgs.brave; # engine follows package.meta.mainProgram

    models.low    = { provider = "deepseek";  model = "deepseek-v4-flash"; };
    models.medium = { provider = "deepseek";  model = "deepseek-v4-pro"; };
    models.high   = { provider = "xai-oauth"; model = "grok-4.6"; };

    plugins = [
      "model-router"
      "tool-call-coherency"
      "secret-handoff"
      # "gbrain-retrieval-reflex"
      # "gbrain-memory-flush"
      # "git-hook"
    ];

    extraPlugins = {
      # my-plugin = ./plugins/my-plugin;
    };

    # webui.enable = true;
    # toolbox.enable = true;
    # gbrain.enable = false;
    # container.enable = false;
    # hmc.enable = false;
    # mcpProxy.enable = false;
  };
}
```

Comment a line to drop a customisation. Official
`services.hermes-agent.*` and `services.hermes-webui.*` still work as
documented.

`services.hermesPnP.enable = false` (the default) keeps the library
path: plugins and `services.hermesPnP.mcpProxy` only.

MCP backends live on `services.hermesPnP.mcpProxy.*`
(`services.mcpProxy` is an alias). Point official
`services.hermes-agent.mcpServers.<name>.url` at
`http://127.0.0.1:3140/<backend>`.

## Three models

One block seeds official settings, model-router, and WebUI.

| name   | role                      | seeds                                   |
| ------ | ------------------------- | --------------------------------------- |
| low    | cheap helper              | mechanical auxiliary slots + cron       |
| medium | workhorse                 | delegation + reasoning auxiliary slots  |
| high   | session identity + voice  | `model.default`, `fallback_model`       |

When the composer is on, those values are written into official
`services.hermes-agent.settings.*`. Override any seed with the official
option. Do not seed vision / tts / moa.

## What the composer sets

When `services.hermesPnP.enable = true`:

- WebUI on `127.0.0.1:8787`, same user/group/package as the agent,
  `hermesHome = ${stateDir}/.hermes`, same `environmentFiles`
- Bundled-share env (`HERMES_BUNDLED_PLUGINS`, skills, locales, …) on
  the agent environment and WebUI `extraEnvironment`
- Optional silence-marker PYTHONPATH wrap
  (`packageFixes.silenceMarkers`, default true)
- Toolbox buildEnv on the agent PATH
- Default `plugins` = model-router, tool-call-coherency, secret-handoff
- Official model / fallback / delegation / cron / auxiliary slots from
  `models.*`

Off switches: `webui.enable`, `toolbox.enable`,
`packageFixes.silenceMarkers`.

Official options you keep writing: `settings`, `container.*`,
`extraPythonPackages`, `extraDependencyGroups`, `mcpServers`,
`documents`. Env files go on `services.hermesPnP.environmentFiles`.

## Flake exports

- `nixosModules.default` / `nixosModules.hermesPnP` — composer
- `nixosModules.agent` / `nixosModules.webui` — official modules only
- `nixosModules.plugins` / `mcp-proxy` / `toolbox` / `browser` / `skills`
- `packages.<system>.mcp-proxy` / `agent-browser` and `overlays.default`
- `lib.mkDockerEnv` / `lib.remapStatePath` / `lib.forPkgs`
- `plugins.<name>` — raw plugin source paths

Double-import of the official modules is fine: they merge.

## Examples

Snippets for a consumer flake, not a host. `nix flake check` evaluates
each file.

| File | Role |
| --- | --- |
| [`examples/composer.nix`](examples/composer.nix) | Native drop-in |
| [`examples/container.nix`](examples/container.nix) | Agent + WebUI + browser in the Ubuntu OCI jail |
| [`examples/library-plugins.nix`](examples/library-plugins.nix) | Composer off; first-party plugins only |
| [`examples/mcp-proxy.nix`](examples/mcp-proxy.nix) | Loopback MCP proxy + `mcpServers` |
| [`examples/gbrain.nix`](examples/gbrain.nix) | Loopback `gbrain serve` |
| [`examples/browser.nix`](examples/browser.nix) | CDP engine + dashboard `publicUrl` |
| [`examples/toolbox.nix`](examples/toolbox.nix) | Extra CLI on the shared PATH |
| [`examples/skills.nix`](examples/skills.nix) | Consumer `extraSkills` |
| [`examples/hmc.nix`](examples/hmc.nix) | Pin hermes-context-manager |

Combine files when you want more than one product.

## Plugins

```nix
services.hermesPnP.plugins = [
  "model-router"
  "tool-call-coherency"
  "secret-handoff"
  # "gbrain-retrieval-reflex"
  # "gbrain-memory-flush"
  # "git-hook"
];

services.hermesPnP.extraPlugins = {
  # my-plugin = ./local;
};
```

Materialize → `$stateDir/plugins/<name>`, discover via
`$stateDir/.hermes/plugins/<name>`. First-party plugins are not
installed through official `extraPlugins`.

| Plugin | Role | Knobs |
| --- | --- | --- |
| `model-router` | Per-turn low / medium / high routing | `hermesPnP.models`, or `MODEL_ROUTER_LOW_MODEL` / `_PROVIDER` |
| `tool-call-coherency` | Heal double-wrapped / cold MCP tool calls | none |
| `gbrain-retrieval-reflex` | Ambient GBrain pointers over HTTP MCP | `GBRAIN_MCP_URL`, `GBRAIN_TOKEN_FILE`, `GBRAIN_RETRIEVAL_REFLEX_*` |
| `gbrain-memory-flush` | Nudge durable facts out of MEMORY.md | `GBRAIN_MEMORY_BUDGET_CHARS`, `HERMES_MEMORY_PATH` |
| `secret-handoff` | Ephemeral login paste via clarify + CDP | `BROWSER_CDP_URL` |
| `git-hook` | Pull-before-read; commit/push this turn's dirty files | `GIT_HOOK_COMMIT=0`, `GIT_HOOK_PUSH=0` |

Slash commands: `/low` `/medium` `/high` `/auto`. When `model-router`
is in `plugins`, Nix writes `config.json` and `webui/config.js` from
`hermesPnP.models`.

## GBrain

Off by default. When enabled, starts loopback `gbrain serve`, sets the
MCP URL + plugin env, and installs the two gbrain plugins. The CLI is
a consumer bootstrap (`bun install -g`).

See [`examples/gbrain.nix`](examples/gbrain.nix). Listing the plugins
does not require this hook.

Operator scripts: `scripts/gbrain-setup.sh`,
`scripts/validate-gbrain.sh`. See `docs/gbrain.md`.

## Secrets

Declare the rendered env file on the composer. It is forwarded to
`services.hermes-agent.environmentFiles`. WebUI inherits that list.
Do not put secrets in JSON.

```nix
sops.templates.hermesEnv = {
  owner = "hermes";
  group = "hermes";
  mode = "0600";
  path = "/run/hermes.env";
  content = ''
    DEEPSEEK_API_KEY=${config.sops.placeholder.deepseek_api_key}
    XAI_API_KEY=${config.sops.placeholder.xai_api_key}
  '';
};

services.hermesPnP.environmentFiles = [ config.sops.templates.hermesEnv.path ];
```

Include a key for every provider named in `models.*`. Optional tool
keys are listed in `docs/hermes.env.example`. GBrain tokens stay on
`gbrain.tokenFile`. Site identity stays in the consumer.

## MCP proxy

Composer does not auto-enable it. See
[`examples/mcp-proxy.nix`](examples/mcp-proxy.nix).

```nix
auth.mode = "auto";          # inject if secrets ≠ {}, else passthrough
# auth.mode = "inject";
# auth.mode = "passthrough";
```

When injecting, every `tools/list` description is prefixed with
`[authed via proxy] ` (`auth.tag`). Secrets are systemd
`LoadCredential` files, never written into the JSON.

## Container

`hermesPnP.container.enable` turns on official
`services.hermes-agent.container`. Extra host mounts use
`container.extraVolumes`. `hermesPnP.webui.container.extraVolumes` is
independent. WebUI/browser jails are ubuntu + `/nix/store:ro`.

## Toolbox + browser

`toolbox.enable` materializes the CLI buildEnv to
`/var/lib/hermes/toolbox/bin` (`/data/toolbox/bin` in the jail).
`browser.*` provisions a persistent CDP browser and seeds
`BROWSER_CDP_URL`.

## Out of scope

SOUL.md from Nix, Telegram allowlists, Composio policy, home-manager,
darwin. HMC and GBrain serve are opt-in.

## Develop

```bash
nix develop
nix flake check
PYTHONPATH=pkgs/mcp-proxy/src python3 -m unittest discover -s pkgs/mcp-proxy/tests -v
python3 -m unittest discover -s plugins/secret-handoff/tests -v
python3 -m unittest discover -s plugins/model-router/tests -v
```

## Credits

- [open-world-project/model-router](https://github.com/open-world-project/model-router)
  — inspiration for the per-turn cheap/work/voice router.
