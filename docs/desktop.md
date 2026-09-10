# Desktop mode

`services.hermesPnP.desktop.enable` is native-only pairing for
Hermes Desktop.

Official `backend.mode` cannot run inside the Ubuntu jail. This
mode `mkForce`s the composer container knob off and asserts
`services.hermes-agent.container.enable` is false.

## What it sets

- official `backend.mode` (`serve` default, or `dashboard`)
- official `backend.host` / `port` (loopback `:9119`)
- official `backend.sessionTokenFile`
- `addToSystemPackages`
- GUI logins in `desktop.users` as members of the hermes group
- wrapped `hermes-desktop` on `environment.systemPackages`

The wrapper bakes `HERMES_HOME`, `HERMES_MANAGED`,
`HERMES_DESKTOP_REMOTE_URL`, and `HERMES_DESKTOP_HERMES`. The session
token is read at start (`extraRun`). Never `--set` it; that copies
the secret into the Nix store.

A GUI launcher does not read the shell profile. Without this wrap,
Desktop starts its own serve against `~/.hermes` and reports
"no inference provider configured" even when `sudo -u hermes hermes
model` is correct.

## Consumer

```nix
services.hermesPnP.desktop = {
  enable = true;
  users = [ "alice" ];
  sessionTokenFile = config.sops.secrets.hermes_dashboard_session_token.path;
};
```

Token file: owner hermes, group hermes, mode 0440, one line, not a
Nix path literal. Drop any hand-rolled `systemd.services.hermes-serve`.

See `examples/desktop.nix`.

Identity stays the hermes service user. This is not a Home Manager
module. Personal/laptop identity: `docs/home-manager.md`.

A hosted Desktop client that is not using this shortcut sets
`HERMES_DESKTOP_REMOTE_URL` on the official module. PnP does not
generalize that URL.
