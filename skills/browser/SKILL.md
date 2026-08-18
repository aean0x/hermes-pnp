---
name: browser
description: Use when driving the composer CDP browser or dashboard gate.
version: 1.1.0
author: Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [browser, cdp, dashboard, hermes-pnp]
---

# Browser

What `services.hermesPnP.browser` provisions. Defaults below are the
module defaults; the consumer may swap the engine/package.

## When to use

- Attach Hermes `browser_*` tools to the sticky CDP session
- Hand a captcha / bot-gate to a human on the same profile
- Check whether the browser unit is actually up

Do not use this skill for site-specific extras (egress, checkout,
credentials, vendor quirks). Those belong in the consumer via
`skills.extraSkills`.

## Defaults

- Engine: `chromium` by default. Set `browser.package` (e.g. `pkgs.brave`);
  `browser.engine` follows `package.meta.mainProgram`.
- CDP: `http://127.0.0.1:9222` — also `BROWSER_CDP_URL` and `BU_CDP_URL`
- Profile / cookies / logs: `$stateDir/browser-{profile,cookies,logs}`
- Gate: agent-browser dashboard `:4848` (on when `browser.gate.enable`,
  default true). Relay URL is `HERMES_BROWSER_GATE_URL`.
- PATH aliases: `chromium`, `chrome`, `google-chrome` → the configured engine
- Units (host-native): `hermes-browser`, `hermes-browser-gate`
- Units (container): `hermes-browser` only (gate runs inside)
- Helpers: `hermes-browser-status`, `hermes-browser-import-cookies`

Probe before clicking:

```
curl -sS --max-time 2 http://127.0.0.1:9222/json/version
curl -sS --max-time 2 -o /dev/null -w '%{http_code}\n' http://127.0.0.1:4848/
```

## Drive it

1. Native `browser_*` tools against the sticky CDP URL. Same profile
   every time — do not spawn a second browser.
2. Gate: stop and hand the dashboard URL to a human. Resume on the
   same CDP after they say the page is clear.
3. Do not run `agent-browser connect` / `open` yourself — the unit
   already attached. A second connect can steal the session.

Consumer extras (different engine, different ports, site playbooks)
go in `services.hermesPnP.skills.extraSkills`, not this file.
