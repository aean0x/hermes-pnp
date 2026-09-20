# hermes-browser-lease

One Hermes session drives the browser at a time.

## Why

Every Hermes surface — WebUI threads, the gateway, cron agents, kanban workers —
reaches the same browser over one CDP endpoint. Each session that touches it adds
**its own tab set** to that engine, and the cost is one renderer process per tab
set. With several sessions browsing at once the engine's memory grows with the
number of sessions, and the sessions also fight over tabs: one driver's
`Target.activateTarget` yanks tabs and popups out from under another, which breaks
form fills and login flows mid-flight.

A container memory cap does not fix that. It only converts memory pressure into
OOM kills, and if the engine restarts into a restored session the same tab set
comes straight back.

## What it does

Serialises browser access through an advisory `flock` on
`<$HERMES_HOME>/cache/browser-lease.lock`:

- taken on the first `browser_*` call of a turn,
- renewed on each browser call,
- released at turn end (`post_llm_call`),
- released by a watchdog after `sticky_s` without a call, so an abandoned turn
  cannot wedge the engine,
- released by the kernel if the holder dies.

A session that cannot take the lease waits `wait_s` and then gets a **blocked**
tool result naming the holder, which the model can retry. It never silently
interleaves with the other session.

The lock file must sit on storage shared by every process that drives the
browser. Do not move it to `/tmp`: containers with private `/tmp` would take two
different locks and serialise nothing.

## Install

Ships in the `aean0x/hermes-pnp` plugin catalog as `browser-lease`
(`plugins/browser-lease`, listed in `plugins/catalog.nix`). Enable it wherever
plugins are listed:

```nix
services.hermesPnP.plugins = [ "browser-lease" ];
```

Enabling the name is the whole install: the catalog entry copies the tree to
`$stateDir/plugins/browser-lease` and symlinks it into `$stateDir/.hermes/plugins/`.
To run it without the composer, point `services.hermesPnP.extraPluginDirs` (or
`plugins.enabled` for a manual drop-in) at the tree instead.

## Configuration

| Key | Default | Meaning |
|---|---|---|
| `browser.lease.enabled` | `true` | Set `false` to disable the gate without removing the plugin. |
| `browser.lease.wait_s` | `45` | Seconds a contending session waits before its call is blocked. |
| `browser.lease.sticky_s` | `120` | Seconds of no browser calls before the watchdog hands the lease back. |
| `browser.lease.lock_path` | `<$HERMES_HOME>/cache/browser-lease.lock` | Override the lock file location. |
| `HERMES_HOME` | — | Environment fallback for lock placement (`/data/.hermes` when unset and present). |

## Tests

Stdlib only, no dependencies:

```bash
python -m unittest discover -s tests -t . -v
```

Covers cross-process mutual exclusion, wait-then-veto naming the holder, release
by a dead holder, idle release, renewal past the idle window, hook registration,
non-browser passthrough, turn-end release, and the disabled path.
