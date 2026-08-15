# mcp-proxy

A thin Streamable-HTTP reverse proxy for MCP.

Clients talk to loopback. The proxy injects upstream secrets and applies
declarative tool / argument policy — allow, deny, inject, strip — per backend
and per toolkit.

## Flake

```nix
{
  inputs.mcp-proxy.url = "github:aean0x/mcp-proxy";

  outputs = { nixpkgs, mcp-proxy, ... }: {
    nixosConfigurations.host = nixpkgs.lib.nixosSystem {
      modules = [
        mcp-proxy.nixosModules.default
        ./configuration.nix
      ];
    };
  };
}
```

Also exports `packages.<system>.mcp-proxy` (CLI) and `overlays.default`.

## Declare

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
#                            # (filters still apply — use this for client OAuth)
```

When **injecting**, every `tools/list` description is prefixed with
`[auth via proxy] ` (override or disable via `auth.tag`). Hermes
`tool_search` shows the first ~60 characters, so the tag stays visible
and the original first sentence still fits. No tag in passthrough mode.

```nix
advertise.byTool.SEARCH_TOOLS.append = " Extra schema note.";
```

`advertise.prepend` / `byTool.prepend` still work and are placed after the inject tag.

Site-specific labels, account names, and other private tokens belong in the
**consumer** flake. This repo only ships the engine and generic examples.

## CLI

```bash
mcp-proxy --config /path/to/config.json
# GET http://127.0.0.1:3140/healthz
```

JSON config is generated from the NixOS module. Secrets are systemd
`LoadCredential` files, never written into the JSON.

## Develop

```bash
nix develop
PYTHONPATH=src python3 -m unittest discover -s tests -v
nix flake check
```
