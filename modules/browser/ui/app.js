// Gate bootstrap: discover the CDP browser WebSocket endpoint through the
// same-origin proxy, then mount @agent-infra/browser-ui against it.
(async () => {
  const status = document.getElementById("status");
  const set = (m) => { status.textContent = m; };

  async function cdpVersion() {
    const res = await fetch("/json/version", { cache: "no-store" });
    if (!res.ok) throw new Error("CDP /json/version -> " + res.status);
    return res.json();
  }

  let version;
  for (let i = 0; i < 60; i++) {
    try {
      version = await cdpVersion();
      break;
    } catch (e) {
      set("waiting for CDP… (" + e.message + ")");
      await new Promise((r) => setTimeout(r, 2000));
    }
  }
  if (!version) {
    set("browser did not come up — reload to retry");
    return;
  }

  // Chromium reports ws://127.0.0.1:9222/devtools/browser/<id>; the gate
  // proxies /devtools/* same-origin, so rewrite to this page's origin.
  const wsPath = version.webSocketDebuggerUrl.replace(/^[a-z]+:\/\/[^/]+/, "");
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const endpoint = scheme + "://" + location.host + wsPath;
  set("connected: " + (version.Browser || "browser"));

  const BrowserUI = window.agent_infra_browser_ui && window.agent_infra_browser_ui.BrowserUI;
  if (!BrowserUI) {
    set("browser-ui bundle missing (window.agent_infra_browser_ui)");
    return;
  }

  try {
    const created = BrowserUI.create({
      root: document.getElementById("browserContainer"),
      browserOptions: {
        connect: {
          browserWSEndpoint: endpoint,
          defaultViewport: { width: 1400, height: 900 },
        },
      },
    });
    if (created && typeof created.then === "function") {
      created.catch((e) => set("browser-ui failed: " + (e && e.message ? e.message : e)));
    }
  } catch (e) {
    set("browser-ui failed: " + (e && e.message ? e.message : e));
  }
})();
