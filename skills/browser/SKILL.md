---
name: browser
description: Use when driving the composer CDP browser or noVNC handoff.
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [browser, cdp, novnc, hermes-pnp]
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
- noVNC: `:6080` (on when `browser.noVNC.enable`, default true)
- Raw VNC: `:5900` (not opened in the firewall)
- PATH aliases: `chromium`, `chrome`, `google-chrome` → the configured engine
- Units: `hermes-browser`, `hermes-browser-vnc`, `hermes-browser-novnc`
- Helpers: `hermes-browser-status`, `hermes-browser-import-cookies`

Probe before clicking:

```
curl -sS --max-time 2 http://127.0.0.1:9222/json/version
```

## Drive it

1. Native `browser_*` tools against the sticky CDP URL. Same profile
   every time — do not spawn a second browser.
2. Gate: stop and hand the noVNC session to a human. Resume on the
   same CDP after they say the page is clear.
3. Skip `browser-cli` / `agent-browser` CLI; they fight the unit.

Consumer extras (different engine, different ports, site playbooks)
go in `services.hermesPnP.skills.extraSkills`, not this file.
