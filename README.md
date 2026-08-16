# Hermes PnP (Plug n Pray)

Reusable Hermes **services** and **plugins**. Not a host flake.

Site-specific labels, hostnames, mail filters, RAM caps, and identity stay
in the consumer. This repo ships mechanisms.

```
hermes-pnp/
  plugins/                 # first-party Hermes plugins
  services/mcp-proxy/      # loopback MCP reverse proxy
  nix/modules/             # NixOS modules (composer + plugin installer)
```

Later pieces (GBrain HTTP, toolbox, runtime pairing) land next to
`services/mcp-proxy/` and are imported from `nix/modules/default.nix`.

## Flake

```nix
{
  inputs.hermes-pnp.url = "github:aean0x/hermes-pnp";

  outputs = { nixpkgs, hermes-pnp, ... }: {
    nixosConfigurations.host = nixpkgs.lib.nixosSystem {
      modules = [
        hermes-pnp.nixosModules.default
        ./configuration.nix
      ];
    };
  };
}
```

Also exports:

- `nixosModules.mcp-proxy` — proxy only (`services.mcpProxy`)
- `nixosModules.plugins` — plugin installer only (`services.hermesPnP.plugins`)
- `packages.<system>.mcp-proxy` and `overlays.default`
- `plugins.<name>` — raw plugin source paths

## MCP proxy

A thin Streamable-HTTP reverse proxy. Clients talk to loopback. The proxy
injects upstream secrets and applies declarative tool / argument policy.

```nix
services.mcpProxy = {
  enable = true;
  backends.example = {
    upstream = "https://example.example/mcp";
    secrets.Authorization = {
      file = config.sops.secrets.example_api_key.path;
      prefix = "Bearer ";
    };
    unwrap = [{
      tool = "META_EXECUTE";
      each = "tools";
      name = "slug";
      args = "arguments";
    }];
    toolkits.search = {
      match.names = [ "FETCH_ITEMS" "LIST_THREADS" ];
      args.query = {
        requireTokens = [ "-label:archive" ];
        denyTokens = [ "label:archive" ];
      };
    };
  };
};

# Point the MCP client at the proxy, not the upstream:
#   url = "http://127.0.0.1:3140/example";
```

### Auth

```nix
auth.mode = "auto";         # default: inject if secrets ≠ {}, else passthrough
# auth.mode = "inject";     # always use secrets.*
# auth.mode = "passthrough"; # forward the client's Authorization; ignore secrets
```

When **injecting**, every `tools/list` description is prefixed with
`[authed via proxy] ` (override or disable via `auth.tag`). Hermes
`tool_search` shows the first ~60 characters, so the tag stays visible.

JSON config is generated from the NixOS module. Secrets are systemd
`LoadCredential` files, never written into the JSON.

```bash
mcp-proxy --config /path/to/config.json
# GET http://127.0.0.1:3140/healthz
```

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
  # Host pins (HMC, one-offs) stay in the consumer:
  # extraPlugins.hermes-context-manager = hmcSrc;
};
```

Materialize → `$stateDir/plugins/<name>`, discover via relative symlink
`$stateDir/.hermes/plugins/<name>`. Matches Hermes ≥0.19 (no
`plugins.external_dirs`).

| Plugin | Role | Common knobs |
| --- | --- | --- |
| `model-router` | Per-turn cheap / work / voice routing | `config.json` or `MODEL_ROUTER_T{n}_MODEL`, `MODEL_ROUTER_FINAL_VOICE` |
| `tool-call-coherency` | Heal double-wrapped / cold MCP tool calls | none |
| `gbrain-retrieval-reflex` | Ambient GBrain pointers over HTTP MCP | `GBRAIN_MCP_URL`, `GBRAIN_TOKEN_FILE`, `GBRAIN_RETRIEVAL_REFLEX_*` |
| `gbrain-memory-flush` | Nudge durable facts out of MEMORY.md | `GBRAIN_MEMORY_BUDGET_CHARS`, `HERMES_MEMORY_PATH` |
| `secret-handoff` | Ephemeral login paste via clarify + CDP | `BROWSER_CDP_URL` |
| `projects-auto-commit` | End-of-turn git commit for a working tree | `PROJECTS_ROOT`, `PROJECTS_AUTO_COMMIT=0` |

### model-router

Defaults are a 3-tier cheap / work / voice map (DeepSeek Flash / Pro /
Grok 4.6). Change models without forking:

```nix
services.hermesPnP.plugins.modelRouter.settings = {
  tiers."3" = { model = "grok-4"; provider = "xai-oauth"; label = "T3 Voice"; };
  final_voice = true;
  rest_on_final_tier = true;
};
```

Or env: `MODEL_ROUTER_T3_MODEL`, `MODEL_ROUTER_T3_PROVIDER`,
`MODEL_ROUTER_FINAL_VOICE=0`. WebUI extension dir:

`config.services.hermesPnP.plugins.webuiExtensionDir`

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
