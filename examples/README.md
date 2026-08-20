# Examples

Copy a file into the consumer flake. These are NixOS **modules**, not
a host. Import `inputs.hermes-pnp.nixosModules.default` (or the
à-la-carte module named in the file) and merge the snippet.

`nix flake check` evaluates every file here (dummy agent/webui
packages).

| File | When to copy it |
| --- | --- |
| [composer.nix](composer.nix) | Native drop-in: pairing, models, default plugins, WebUI, toolbox, browser |
| [container.nix](container.nix) | Same, but agent + WebUI + browser in the Ubuntu OCI jail |
| [library-plugins.nix](library-plugins.nix) | Composer off. First-party plugins only (`nixosModules.plugins`) |
| [mcp-proxy.nix](mcp-proxy.nix) | Loopback MCP proxy + `mcpServers` pointed at it |
| [gbrain.nix](gbrain.nix) | Loopback `gbrain serve` + the two gbrain plugins |
| [browser.nix](browser.nix) | CDP engine + dashboard `publicUrl` (Caddy in the consumer) |
| [toolbox.nix](toolbox.nix) | Extra CLI on the shared PATH |
| [skills.nix](skills.nix) | Consumer skill trees beside the catalog |
| [hmc.nix](hmc.nix) | Pin hermes-context-manager as extraPluginDirs |

Combine them: `container.nix` + `gbrain.nix` is a jailed gateway with
GBrain. `composer.nix` + `mcp-proxy.nix` is the usual pairing plus
auth injection.
