# Home Manager

`homeManagerModules.default` is the personal composer. It imports
official `hermes-agent.homeManagerModules.default` and layers PnP
opinions that are safe at user level: models, plugins, skills, package
wrap.

Identity is the login user. Directories stay upstream:

- `hermesHome` = `$HOME/.hermes`
- `workingDirectory` = `$HOME`

Do not set those options unless you mean to.

## What it is not

NixOS `services.hermesPnP.desktop.enable` does **not** switch
`services.hermes-agent.user` to the GUI login. The hosted daemon stays
the `hermes` system user; GUI logins join group `hermes`.

Home Manager is the path that runs as the person.

## Enable

```nix
# flake.nix
inputs.hermes-pnp.url = "github:aean0x/hermes-pnp";

# home-manager module
imports = [ inputs.hermes-pnp.homeManagerModules.default ];

services.hermesPnP = {
  enable = true;
  desktop = {
    enable = true;
    sessionTokenFile = "${config.home.homeDirectory}/.hermes/desktop-token";
  };
};
```

Copy [examples/home-manager.nix](../examples/home-manager.nix).

On NixOS, linger the account or the user units die at logout:

```nix
users.users.aean.linger = true;
```

## Desktop

`pnp.enable` is the daemon: official `programs.hermes-agent` (CLI) and
`services.hermes-agent` with `gateway.enable` (messaging gateway user
unit). Desktop is optional and attaches to that daemon.

`desktop.enable` adds official `programs.hermes-agent.desktop` and
official `hermes-backend` (`serve` on loopback `:9119`). The official
launcher already bakes `HERMES_HOME` and the loopback remote URL.

Sealed venv extras: `pythonExtras` (agent-flake Python, PYTHONPATH
overlay) plus official `extraDependencyGroups` for pyproject extras.
Do not use host `pkgs.python312Packages` or official
`extraPythonPackages` for overlapping trees. User-unit PATH extras:
official `extraPackages` (there is no toolbox buildEnv on HM).

A hosted client that is **not** using this shortcut sets
`HERMES_DESKTOP_REMOTE_URL` on the official module. PnP does not
generalize that URL.

Token file: one line, mode 0400, owner the login user. Never a Nix
path literal (`--set` would copy it into the store).

## Carry vs leave

**Carry:** packageFixes, plugin/skill materialize under `$hermesHome`,
model-router JSON, `mcpServers` env-refs, Desktop wrap (official),
`hmc.enable`, `gbrain.enable` (user unit, `HOME` is the login home).

**Leave on NixOS:** OCI jails, WebUI/browser containers, docker,
`hermes-admin`, `/etc/gitconfig`, system `gbrain-mcp-http` (`User=hermes`).

`container.enable` on this module fails eval.

## Conflict

Do not import `nixosModules.default` and `homeManagerModules.default`
for the same login. That is two homes and two gateways.
