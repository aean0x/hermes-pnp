(() => {
  if (window.__modelRouterExtLoaded) return;
  window.__modelRouterExtLoaded = true;

  const DEFAULT_MODELS = [
    { cmd: "/auxiliary", label: "Auxiliary", short: "Auxiliary", model: "deepseek-v4-flash", title: "Pin Auxiliary" },
    { cmd: "/medium", label: "Medium", short: "Medium", model: "deepseek-v4-pro", title: "Pin Medium" },
    { cmd: "/high", label: "High", short: "High", model: "grok-4.6", title: "Pin High" },
  ];
  const cfg = window.__MODEL_ROUTER_CONFIG;
  const fromCfg = (cfg && cfg.models) || [];
  const listed = Array.isArray(fromCfg) ? fromCfg.filter((row) => row && row.cmd !== "/auto") : [];
  const MODELS = listed.length ? listed : DEFAULT_MODELS;

  let lastCmd = "/auto";
  let overlaying = false;

  function $(id) {
    return document.getElementById(id);
  }

  function sessionModel() {
    const S = window.S;
    if (S && S.session && S.session.model) return String(S.session.model);
    const sel = $("modelSelect");
    return sel && sel.value ? String(sel.value) : "";
  }

  function matchModel(modelId) {
    const needle = String(modelId || "").trim().toLowerCase();
    if (!needle) return null;
    const tail = needle.split("/").pop();
    for (const spec of MODELS) {
      if (needle === spec.model || tail === spec.model || needle.endsWith("/" + spec.model)) {
        return spec;
      }
    }
    return null;
  }

  function isBusy() {
    return !!(window.S && window.S.busy);
  }

  function isPinned() {
    return lastCmd !== "/auto";
  }

  function chipText() {
    const model = sessionModel();
    const row = matchModel(model);
    const shortModel = model ? model.split("/").pop() : "";
    if (!isPinned()) {
      if (isBusy() && row) {
        return shortModel ? `Auto · ${row.short} · ${shortModel}` : `Auto · ${row.short}`;
      }
      return "Auto";
    }
    const pinned = MODELS.find((m) => m.cmd === lastCmd) || row;
    if (!pinned) return lastCmd;
    return shortModel ? `${pinned.short} · ${shortModel}` : pinned.short;
  }

  function chipTitle() {
    const model = sessionModel();
    const row = matchModel(model);
    if (!isPinned()) {
      if (isBusy() && row) return `Model Router auto-routing (${row.label}): ${model}`;
      return "Model Router auto-routing";
    }
    const pinned = MODELS.find((m) => m.cmd === lastCmd) || row;
    if (!pinned) return "Model Router pinned";
    return `Model Router pinned: ${pinned.label}${model ? " → " + model : ""}`;
  }

  function overlayChip() {
    const text = chipText();
    const title = chipTitle();
    const label = $("composerModelLabel");
    const mobile = $("composerMobileModelLabel");
    const chip = $("composerModelChip");
    overlaying = true;
    try {
      if (label && label.textContent !== text) label.textContent = text;
      if (mobile && mobile.textContent !== text) mobile.textContent = text;
      if (chip && chip.title !== title) chip.title = title;
    } finally {
      overlaying = false;
    }
    paintPressed();
  }

  function wrapSyncModelChip() {
    const orig = window.syncModelChip;
    if (typeof orig !== "function" || orig._mrWrapped) return;
    const wrapped = function () {
      const result = orig.apply(this, arguments);
      overlayChip();
      return result;
    };
    wrapped._mrWrapped = true;
    window.syncModelChip = wrapped;
  }

  async function runSlash(cmd) {
    lastCmd = cmd;
    paintPressed();
    overlayChip();
    const input = $("msg");
    if (input && typeof window.send === "function") {
      input.value = cmd;
      if (typeof window.autoResize === "function") window.autoResize();
      try {
        await window.send();
      } catch (err) {
        if (typeof window.showToast === "function") {
          window.showToast(`Model Router: ${err.message || err}`, 3200);
        }
      }
      overlayChip();
      return;
    }
    if (typeof window.showToast === "function") {
      window.showToast("Model Router: composer send() not available", 2800);
    }
  }

  function paintPressed() {
    const model = sessionModel();
    const active = matchModel(model);
    document.querySelectorAll(".mr-btn").forEach((btn) => {
      const cmd = btn.dataset.cmd;
      let on = cmd === lastCmd;
      if (!isPinned() && isBusy() && active && cmd === active.cmd) {
        on = true;
      }
      if (!isPinned() && cmd === "/auto") on = true;
      if (isPinned() && cmd === "/auto") on = false;
      btn.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  function mountBar(host) {
    if (!host || host.querySelector(".mr-bar")) return;
    const bar = document.createElement("div");
    bar.className = "mr-bar";
    bar.id = "model-router-bar";
    const label = document.createElement("span");
    label.className = "mr-label";
    label.textContent = "Router";
    bar.appendChild(label);
    const autoBtn = document.createElement("button");
    autoBtn.type = "button";
    autoBtn.className = "mr-btn";
    autoBtn.dataset.cmd = "/auto";
    autoBtn.textContent = "Auto";
    autoBtn.title = "Resume per-turn routing";
    autoBtn.onclick = (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      void runSlash("/auto");
    };
    bar.appendChild(autoBtn);
    for (const spec of MODELS) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "mr-btn";
      btn.dataset.cmd = spec.cmd;
      btn.textContent = spec.label;
      btn.title = spec.title;
      btn.onclick = (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        void runSlash(spec.cmd);
      };
      bar.appendChild(btn);
    }
    host.appendChild(bar);
    paintPressed();
    overlayChip();
  }

  function findHost() {
    return (
      $("composer") ||
      document.querySelector(".composer") ||
      document.querySelector("footer.composer") ||
      $("msg")?.closest("form") ||
      $("msg")?.parentElement
    );
  }

  function tryMount() {
    wrapSyncModelChip();
    const host = findHost();
    if (host) mountBar(host);
    overlayChip();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", tryMount);
  } else {
    tryMount();
  }
  const obs = new MutationObserver(() => {
    if (overlaying) return;
    tryMount();
  });
  obs.observe(document.documentElement, { childList: true, subtree: true });
  setInterval(() => {
    wrapSyncModelChip();
    overlayChip();
  }, 800);
})();
