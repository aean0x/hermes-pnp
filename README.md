# Hermes PnP (Plug n Pray)

Opinionated NixOS composer for Hermes Agent. One flake input. Drop-in
on top of the official `services.hermes-agent` surface. WebUI is part of
the product. Site identity stays in the consumer flake.

This is not a host flake. It does not own secrets, hostnames, Telegram
IDs, mail routing, RAM caps, or SOUL.md. Browser CDP/noVNC is a
composer opinion (`services.hermesPnP.browser`); the engine is a consumer
choice.

## Drop-in

```nix
{
  imports = [ inputs.hermes-pnp.nixosModules.default ];

  services.hermes-agent = {
    enable = true;
    # Official settings still work. PnP only seeds via the composer.
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
      # "projects-auto-commit"
    ];

    extraPlugins = {
      # my-plugin = ./plugins/my-plugin;
    };

    # webui.enable = true;     # default on when composer is on
    # toolbox.enable = true;   # everyday CLI buildEnv → /var/lib/hermes/toolbox/bin
    # gbrain.enable = false;
    # mcpProxy.enable = false;
  };
}
```

Comment a line to drop a customisation. Official
`services.hermes-agent.*` and `services.hermes-webui.*` still work as
documented.

`settings.*` does not move. Secrets do not move. Delete hand-rolled
WebUI pairing, plugin symlink scripts, and bundled-share env once the
composer is on.

`services.hermesPnP.enable = false` (the default) keeps the library
path: plugins and `services.mcpProxy` only.

## Three models

One block seeds every place a model must be named. Plugin, WebUI, and
slash commands use the same names.

| name   | role                      | seeds                                      |
| ------ | ------------------------- | ------------------------------------------ |
| low    | cheap helper              | every auxiliary slot + unpinned cron       |
| medium | workhorse                 | delegation (`delegate_task` children)      |
| high   | session identity + voice  | `model.default`, `fallback_model`, rest    |

When the composer is on, those values are written into official
`services.hermes-agent.settings.*`. Override any seed with the official
option. Do not seed STT / TTS / vision.

## What the composer sets (overridable)

When `services.hermesPnP.enable = true`:

- WebUI on `127.0.0.1:8787`, same user/group/package as the agent,
  `hermesHome = ${stateDir}/.hermes`, same `environmentFiles`
- Bundled-share env (`HERMES_BUNDLED_PLUGINS`, skills, locales, …)
  injected into the agent environment and container `--env`
- Optional silence-marker PYTHONPATH wrap
  (`packageFixes.silenceMarkers`, default true)
- Small toolbox: git, curl, jq, ripgrep, file, unzip, gnused,
  coreutils, findutils
- Default `plugins` = model-router, tool-call-coherency, secret-handoff
- Official model / fallback / delegation / cron / auxiliary slots from
  `models.*`

Escape hatches:

- `services.hermesPnP.webui.enable = false` — gateway-only
- `services.hermesPnP.toolbox.enable = false`
- `services.hermesPnP.packageFixes.silenceMarkers = false`

Official options you keep writing yourself: `settings`,
`container.*`, `extraPythonPackages`, `extraDependencyGroups`,
`mcpServers`, `documents`. Env files go on
`services.hermesPnP.environmentFiles` (forwarded). See Secrets.

PnP does **not** default `extraDependencyGroups` to `["mcp"]`. Native
`full` already has what most users need.

## Flake exports

- `nixosModules.default` — composer (official agent + webui + PnP extras)
- `nixosModules.agent` / `nixosModules.webui` — official modules only
- `nixosModules.plugins` — plugin installer only
- `nixosModules.mcp-proxy` — `services.mcpProxy` only
- `nixosModules.toolbox` / `nixosModules.runtime`
- `packages.<system>.mcp-proxy` and `overlays.default`
- `plugins.<name>` — raw plugin source paths

Double-import of the official modules is fine: they merge.

## Plugins

```nix
services.hermesPnP.plugins = [
  "model-router"
  "tool-call-coherency"
  "secret-handoff"
  # "gbrain-retrieval-reflex"
  # "gbrain-memory-flush"
  # "projects-auto-commit"
];

services.hermesPnP.extraPlugins = {
  # my-plugin = ./local;
};
```

Materialize → `$stateDir/plugins/<name>`, discover via relative symlink
`$stateDir/.hermes/plugins/<name>`. Matches Hermes ≥0.19 (no
`plugins.external_dirs`). First-party plugins are **not** installed
through official `extraPlugins`.

| Plugin | Role | Common knobs |
| --- | --- | --- |
| `model-router` | Per-turn low / medium / high routing | `hermesPnP.models`, or `MODEL_ROUTER_LOW_MODEL` / `_PROVIDER` (and medium/high) |
| `tool-call-coherency` | Heal double-wrapped / cold MCP tool calls | none |
| `gbrain-retrieval-reflex` | Ambient GBrain pointers over HTTP MCP | `GBRAIN_MCP_URL`, `GBRAIN_TOKEN_FILE`, `GBRAIN_RETRIEVAL_REFLEX_*` |
| `gbrain-memory-flush` | Nudge durable facts out of MEMORY.md | `GBRAIN_MEMORY_BUDGET_CHARS`, `HERMES_MEMORY_PATH` |
| `secret-handoff` | Ephemeral login paste via clarify + CDP | `BROWSER_CDP_URL` |
| `projects-auto-commit` | Pull-before-read + commit/push only this turn's dirty files, any git worktree | `PROJECTS_AUTO_COMMIT=0`, `PROJECTS_AUTO_PUSH=0` |

### model-router

Slash commands: `/low` `/medium` `/high` `/auto`. WebUI labels: Low /
Medium / High. Classifier replies with one of those three words.

When `model-router` is in `plugins`, Nix writes `config.json` and
`webui/config.js` from `hermesPnP.models`.

## GBrain (optional, thin)

Does not start `gbrain serve`. Sets a default MCP URL and plugin env,
and installs the two gbrain plugins even if they are not in `plugins`:

```nix
services.hermesPnP.gbrain = {
  enable = true;
  url = "http://127.0.0.1:3131/mcp";
  tokenFile = "/var/lib/hermes/home/.gbrain/hermes-mcp.token";
};
```

Listing the GBrain plugins does not require this hook.

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
keys (search, crawl, TTS) are listed in `docs/hermes.env.example`.
GBrain tokens stay on `gbrain.tokenFile`, not this file. Site identity
(Telegram, mail) stays in the consumer.

## MCP proxy

Composer does not auto-enable it. `services.hermesPnP.mcpProxy.enable = true`
turns on `services.mcpProxy`.

```nix
services.mcpProxy = {
  enable = true;
  backends.example = {
    upstream = "https://example.example/mcp";
    secrets.Authorization = {
      file = config.sops.secrets.example_api_key.path;
      prefix = "Bearer ";
    };
  };
};

# Point the MCP client at the proxy:
#   url = "http://127.0.0.1:3140/example";
```

### Auth

```nix
auth.mode = "auto";         # default: inject if secrets ≠ {}, else passthrough
# auth.mode = "inject";     # always use secrets.*
# auth.mode = "passthrough"; # forward the client's Authorization; ignore secrets
```

When **injecting**, every `tools/list` description is prefixed with
`[authed via proxy] ` (override or disable via `auth.tag`).

JSON config is generated from the NixOS module. Secrets are systemd
`LoadCredential` files, never written into the JSON.

## Runtime

Default is the official module's native or container path. PnP does not
turn `container.enable` on. Extra host mounts use the official
`services.hermes-agent.container.extraVolumes` directly — there is no
`runtime.*` wrapper.

## Toolbox + browser

`toolbox.enable` builds the everyday CLI `buildEnv` into
`/var/lib/hermes/toolbox/bin` (container `/data/toolbox/bin`) and wires
it onto the agent PATH. `browser.*` provisions a persistent CDP browser
(+ optional noVNC phone handoff) and seeds `BROWSER_CDP_URL` into the
agent env.

## Out of scope

HMC, declarative gbrain serve, SOUL.md from Nix, Telegram allowlists,
Composio policy, home-manager, darwin.

## Develop

```bash
nix develop
nix flake check
PYTHONPATH=services/mcp-proxy/src python3 -m unittest discover -s services/mcp-proxy/tests -v
python3 -m unittest discover -s plugins/secret-handoff/tests -v
python3 -m unittest discover -s plugins/model-router/tests -v
```

## Credits

- [open-world-project/model-router](https://github.com/open-world-project/model-router)
  — inspiration for the per-turn cheap/work/voice router. Our
  implementation is a rewrite (native providers, named models,
  no SOUL.md writes).
