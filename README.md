# Hermes PnP (Plug n Pray)

Opinionated NixOS composer for Hermes Agent. One flake input. Drop-in
on top of the official `services.hermes-agent` surface. WebUI is part of
the product. Site identity stays in the consumer flake.

This is not a host flake. It does not own secrets, hostnames, Telegram
IDs, mail routing, browser CDP, RAM caps, or SOUL.md.

## Drop-in

A native Hermes NixOS setup:

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

That enables agent + WebUI pairing + first-party plugin installer +
toolbox + the option to turn on the MCP proxy. Official
`services.hermes-agent.*` and `services.hermes-webui.*` still work as
documented.

```nix
services.hermesPnP.plugins.enable = [
  "model-router"
  "tool-call-coherency"
  "secret-handoff"
];
```

`settings.*` does not move. Secrets do not move. Delete hand-rolled
WebUI pairing, plugin symlink scripts, and bundled-share env once the
composer is on.

`services.hermesPnP.enable = false` (the default) keeps today's library
behavior: plugins and `services.mcpProxy` only.

## What the composer sets (`mkDefault`, overridable)

When `services.hermesPnP.enable = true`:

- WebUI on `127.0.0.1:8787`, same user/group/package as the agent,
  `hermesHome = ${stateDir}/.hermes`, same `environmentFiles`
- Bundled-share env (`HERMES_BUNDLED_PLUGINS`, skills, locales, …)
  injected into the agent environment and container `--env`
- Optional silence-marker PYTHONPATH wrap
  (`packageFixes.silenceMarkers`, default true)
- Small toolbox: git, curl, jq, ripgrep, file, unzip, gnused,
  coreutils, findutils

Escape hatches:

- `services.hermesPnP.webui.enable = false` — gateway-only
- `services.hermesPnP.toolbox.enable = false`
- `services.hermesPnP.packageFixes.silenceMarkers = false`

Official options you keep writing yourself: `settings`,
`environmentFiles`, `container.*`, `extraPythonPackages`,
`extraDependencyGroups`, `mcpServers`, `documents`.

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
services.hermesPnP.plugins = {
  enable = [
    "model-router"
    "tool-call-coherency"
    "gbrain-retrieval-reflex"
    "gbrain-memory-flush"
    "secret-handoff"
    "projects-auto-commit"
  ];
  # Host pins stay in the consumer:
  # extraPlugins.my-plugin = ./local;
};
```

Materialize → `$stateDir/plugins/<name>`, discover via relative symlink
`$stateDir/.hermes/plugins/<name>`. Matches Hermes ≥0.19 (no
`plugins.external_dirs`). First-party plugins are **not** installed
through official `extraPlugins`.

| Plugin | Role | Common knobs |
| --- | --- | --- |
| `model-router` | Per-turn cheap / work / voice routing | `config.json` or `MODEL_ROUTER_T{n}_MODEL`, `MODEL_ROUTER_FINAL_VOICE` |
| `tool-call-coherency` | Heal double-wrapped / cold MCP tool calls | none |
| `gbrain-retrieval-reflex` | Ambient GBrain pointers over HTTP MCP | `GBRAIN_MCP_URL`, `GBRAIN_TOKEN_FILE`, `GBRAIN_RETRIEVAL_REFLEX_*` |
| `gbrain-memory-flush` | Nudge durable facts out of MEMORY.md | `GBRAIN_MEMORY_BUDGET_CHARS`, `HERMES_MEMORY_PATH` |
| `secret-handoff` | Ephemeral login paste via clarify + CDP | `BROWSER_CDP_URL` |
| `projects-auto-commit` | End-of-turn git commit for a working tree | `PROJECTS_ROOT`, `PROJECTS_AUTO_COMMIT=0` |

### model-router

```nix
services.hermesPnP.plugins.modelRouter.settings = {
  tiers."3" = { model = "grok-4"; provider = "xai-oauth"; label = "T3 Voice"; };
  final_voice = true;
  rest_on_final_tier = true;
};
```

WebUI extension dir: `config.services.hermesPnP.plugins.webuiExtensionDir`

## GBrain (optional, thin)

Does not start `gbrain serve`. Sets a default MCP URL and plugin env:

```nix
services.hermesPnP.gbrain = {
  enable = true;
  url = "http://127.0.0.1:3131/mcp";
  tokenFile = "/var/lib/hermes/home/.gbrain/hermes-mcp.token";
};
```

Enabling the GBrain *plugins* does not require this hook.

## MCP proxy

Unchanged. Composer does not auto-enable it.

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
turn `container.enable` on.

`services.hermesPnP.runtime.extraBindMounts` appends host paths to
official `container.extraVolumes`. `runtime.mode = "s6"` is not
implemented.

## Out of scope

HMC, declarative gbrain serve, SOUL.md from Nix, Telegram allowlists,
browser CDP, Composio policy, home-manager, darwin.

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
  implementation is a rewrite (native providers, configurable tiers,
  no SOUL.md writes).
