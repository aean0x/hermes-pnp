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

Hermes does **not** take a server-level blurb. Per turn the model sees
`tool_search`'s catalog: each deferred tool as `name: first sentence` of the
MCP `tools/list` description (clipped ~60 chars). Rewrite that text here:

```nix
advertise.byTool.SEARCH_TOOLS.prepend =
  "Host MCP auth is already injected. Do not OAuth or ask for API keys. ";
```

Prepend only the tools whose listing line should carry the note. A global
prepend makes every catalog line identical. `append` is for the full schema
(after `tool_describe`) and does not change the short blurb.

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
