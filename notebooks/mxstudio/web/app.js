/* mxstudio frontend: tab routing + polling + streaming chat. */

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

const TAB_TITLES = {
  models: "Models",
  notebooks: "Notebooks",
  bench: "Benchmark",
  architectures: "Architectures",
  learn: "Learn",
  about: "About",
  settings: "Settings",
};

let activeTab = "models";
let models = [];
let serverStatus = { state: "stopped" };
let chatMessages = [];
let chatBusy = false;
let lastActivityIds = new Set();
let lastBenchState = "idle";
let lastNbState = "idle";
let lastServeState = "stopped";
let lastArchState = "idle";
let backendOfflineNotified = false;
let lastModelsRenderKey = "";
let modelSearchQuery = "";
let modelPage = 1;
const MODEL_PAGE_SIZE = 10;

/* ---------- helpers ---------- */

async function api(path, opts = {}) {
  const controller = new AbortController();
  const callerSignal = opts.signal;
  const timeoutMs = opts.timeoutMs ?? 20000;
  let timedOut = false;
  const relayAbort = () => controller.abort(callerSignal.reason);
  callerSignal?.addEventListener("abort", relayAbort, { once: true });
  const timeout = timeoutMs > 0
    ? setTimeout(() => {
        timedOut = true;
        controller.abort();
      }, timeoutMs)
    : null;
  const { signal: _signal, timeoutMs: _timeoutMs, ...fetchOpts } = opts;
  let res;
  try {
    res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...fetchOpts,
      signal: controller.signal,
    });
    backendOfflineNotified = false;
  } catch (cause) {
    if (timedOut) {
      const err = new Error(`Request timed out after ${Math.round(timeoutMs / 1000)} seconds`);
      err.cause = cause;
      err.timeout = true;
      throw err;
    }
    if (callerSignal?.aborted) throw cause;
    const err = new Error(
      "mxstudio backend is offline. Restart it with: cd ~/unifiedram/mxstudio && pixi run studio"
    );
    err.cause = cause;
    err.network = true;
    if (!backendOfflineNotified) {
      backendOfflineNotified = true;
      toast("Backend disconnected", err.message, "err", { persist: true });
    }
    throw err;
  } finally {
    if (timeout) clearTimeout(timeout);
    callerSignal?.removeEventListener("abort", relayAbort);
  }
  if (!res.ok) {
    let detail = res.statusText;
    let rawDetail = null;
    try {
      const body = await res.json();
      rawDetail = body.detail;
      detail = body.detail;
      if (detail && typeof detail === "object") {
        detail = detail.message || JSON.stringify(detail);
      }
      detail = detail || res.statusText;
    } catch {}
    const err = new Error(detail);
    err.status = res.status;
    err.detail = rawDetail;
    throw err;
  }
  return res.json();
}

async function waitForJob(jobId, { onTick, timeoutMs = 30 * 60 * 1000, signal } = {}) {
  const id = encodeURIComponent(jobId);
  const deadline = Date.now() + timeoutMs;
  let delayMs = 750;
  for (;;) {
    if (Date.now() >= deadline) {
      throw new Error(`Job did not finish within ${Math.round(timeoutMs / 60000)} minutes`);
    }
    if (signal?.aborted) throw signal.reason || new DOMException("Aborted", "AbortError");
    const j = await api(`/api/jobs/${id}`, { signal });
    if (onTick) onTick(j);
    if (j.state === "done") return j;
    if (j.state === "failed") {
      const err = new Error(j.error || j.detail || "job failed");
      err.job = j;
      throw err;
    }
    await new Promise((resolve, reject) => {
      const onAbort = () => {
        clearTimeout(timer);
        reject(signal.reason || new DOMException("Aborted", "AbortError"));
      };
      const timer = setTimeout(() => {
        signal?.removeEventListener("abort", onAbort);
        resolve();
      }, delayMs);
      signal?.addEventListener("abort", onAbort, { once: true });
    });
    delayMs = Math.min(Math.round(delayMs * 1.25), 3000);
  }
}

function jobIdFrom(res) {
  return res?.job_id || res?.job?.job_id || null;
}

function fmtGB(bytes) {
  return bytes == null ? "?" : (bytes / 1e9).toFixed(2) + " GB";
}

function esc(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function enhanceSelect(select) {
  if (!select || select.dataset.enhanced === "true") return;
  select.dataset.enhanced = "true";
  select.classList.add("select-ui-native");
  select.setAttribute("aria-hidden", "true");
  select.tabIndex = -1;

  const root = document.createElement("div");
  root.className = "select-ui";
  const trigger = document.createElement("button");
  trigger.type = "button";
  trigger.className = "select-ui-trigger";
  trigger.setAttribute("aria-haspopup", "listbox");
  trigger.setAttribute("aria-expanded", "false");
  trigger.setAttribute("aria-label", select.getAttribute("aria-label") || "Choose an option");
  const list = document.createElement("div");
  list.className = "select-ui-list";
  list.setAttribute("role", "listbox");
  list.hidden = true;
  root.append(trigger, list);
  select.insertAdjacentElement("afterend", root);

  const close = () => {
    list.hidden = true;
    trigger.setAttribute("aria-expanded", "false");
    root.classList.remove("open");
  };
  const open = () => {
    if (select.disabled) return;
    document.querySelectorAll(".select-ui.open").forEach((item) => {
      if (item !== root) item.querySelector(".select-ui-trigger")?.click();
    });
    list.hidden = false;
    trigger.setAttribute("aria-expanded", "true");
    root.classList.add("open");
    list.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
  };
  const choose = (value) => {
    select.value = value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
    close();
    trigger.focus();
  };
  const sync = () => {
    const options = [...select.options];
    const selected = options.find((option) => option.value === select.value) || options[0];
    trigger.textContent = selected?.textContent || "Choose…";
    trigger.disabled = select.disabled;
    root.dataset.state = ["running", "starting", "stopping", "crashed", "stopped"]
      .find((state) => select.classList.contains(state)) || "";
    list.innerHTML = options.map((option) => `
      <button type="button" role="option" data-value="${esc(option.value)}"
        aria-selected="${option === selected}" ${option.disabled ? "disabled" : ""}>
        ${esc(option.textContent)}
      </button>`).join("");
  };

  trigger.addEventListener("click", () => list.hidden ? open() : close());
  list.addEventListener("click", (event) => {
    const option = event.target.closest?.("[role=option]");
    if (option && !option.disabled) choose(option.dataset.value || "");
  });
  root.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      close();
      trigger.focus();
      return;
    }
    if (!["ArrowDown", "ArrowUp", "Home", "End", "Enter", " "].includes(event.key)) return;
    event.preventDefault();
    if (list.hidden) {
      open();
      if (event.key === "Enter" || event.key === " ") return;
    }
    const options = [...list.querySelectorAll('[role="option"]:not(:disabled)')];
    if (!options.length) return;
    const current = document.activeElement;
    if ((event.key === "Enter" || event.key === " ") && current?.matches?.('[role="option"]')) {
      choose(current.dataset.value || "");
      return;
    }
    let index = options.indexOf(current);
    if (event.key === "Home") index = 0;
    else if (event.key === "End") index = options.length - 1;
    else index = Math.max(0, Math.min(options.length - 1, index + (event.key === "ArrowUp" ? -1 : 1)));
    options[index].focus();
  });
  document.addEventListener("click", (event) => {
    if (!root.contains(event.target)) close();
  });
  new MutationObserver(sync).observe(select, {
    attributes: true,
    childList: true,
    subtree: true,
  });
  select.addEventListener("change", sync);
  sync();
}

/* ---------- theme ---------- */

(function setupTheme() {
  const KEY = "mxstudio.theme";
  const select = $("#theme-switcher");
  const cycleBtn = $("#theme-cycle");
  const media = matchMedia("(prefers-color-scheme: light)");
  const ICONS = {
    dark:
      '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15.5 3.5a8.5 8.5 0 1 0 5 14.7A7 7 0 0 1 15.5 3.5z"/></svg>',
    light:
      '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
  };
  const apply = (preference) => {
    let pref = preference;
    if (pref === "system") pref = media.matches ? "light" : "dark";
    if (pref !== "light" && pref !== "dark") pref = "dark";
    document.documentElement.dataset.themePreference = pref;
    document.documentElement.dataset.theme = pref;
    localStorage.setItem(KEY, pref);
    if (select) select.value = pref;
    if (cycleBtn) {
      // Show the icon for the *other* mode (click to switch), or current — show current.
      cycleBtn.innerHTML = ICONS[pref] || ICONS.dark;
      cycleBtn.title = pref === "dark" ? "Dark · click for light" : "Light · click for dark";
      cycleBtn.setAttribute("aria-label", `Theme ${pref}. Click to switch.`);
      cycleBtn.dataset.pref = pref;
      cycleBtn.dataset.resolved = pref;
    }
    setTimeout(refreshChartsForTheme, 0);
  };
  const saved = localStorage.getItem(KEY) || "system";
  apply(saved === "system" ? (media.matches ? "light" : "dark") : saved);
  select?.addEventListener("change", () => apply(select.value));
  cycleBtn?.addEventListener("click", () => {
    const cur = document.documentElement.dataset.theme || "dark";
    apply(cur === "dark" ? "light" : "dark");
  });
})();

function toast(title, body = "", kind = "warn", opts = {}) {
  const host = $("#toasts");
  if (!host) return;
  const el = document.createElement("div");
  el.className = "toast " + kind;
  const tag = kind === "ok" ? "ok" : kind === "err" ? "err" : "msg";
  let actionHtml = "";
  if (opts.actionLabel && opts.onAction) {
    actionHtml = `<button type="button" class="toast-action">${esc(opts.actionLabel)}</button>`;
  }
  el.innerHTML =
    `<button type="button" class="toast-close" aria-label="Dismiss">×</button>` +
    `<div class="toast-title"><span class="toast-tag">[${tag}]</span> ${esc(title)}</div>` +
    (body ? `<div class="toast-body">${esc(body)}</div>` : "") +
    actionHtml;
  el.querySelector(".toast-close")?.addEventListener("click", () => el.remove());
  if (opts.actionLabel && opts.onAction) {
    el.querySelector(".toast-action")?.addEventListener("click", (ev) => {
      ev.stopPropagation();
      opts.onAction();
    });
  }
  host.prepend(el);
  // Keep stack short so topbar stays readable.
  while (host.children.length > 4) host.lastElementChild?.remove();
  const ms = opts.persist ? 12000 : 5200;
  setTimeout(() => el.remove(), ms);
}

function showFolderResult(res, successTitle = "Opened folder") {
  const path = res?.target_windows || res?.target || res?.path || "";
  if (res?.opened) {
    toast(successTitle, path, "ok");
  } else {
    toast(
      "Could not open Explorer",
      `${path}${res?.error ? ` · ${res.error}` : ""}`,
      "warn",
      { persist: true }
    );
  }
}

async function openModelFolder(modelId) {
  try {
    const res = await api(`/api/models/${encodeModelPath(modelId)}/open-folder`, {
      method: "POST",
    });
    showFolderResult(res);
    return res;
  } catch (err) {
    toast("Could not open folder", err.message, "err");
    return null;
  }
}

async function openNotebookFolder(name) {
  try {
    const res = await api(`/api/notebooks/${encodeURIComponent(name)}/open-folder`, {
      method: "POST",
    });
    showFolderResult(res, "Opened notebooks folder");
  } catch (err) {
    toast("Could not open notebooks folder", err.message, "err");
  }
}

async function openArchFolder() {
  const modelId = ($("#arch-id")?.value || "").trim();
  if (!modelId) {
    toast("Model id required", "Inspect and scaffold a model first", "warn");
    return;
  }
  try {
    const res = await api("/api/architectures/open-folder", {
      method: "POST",
      body: JSON.stringify({ model_id: modelId }),
    });
    showFolderResult(res, "Opened architecture package");
  } catch (err) {
    toast("Could not open architecture package", err.message, "err", { persist: true });
  }
}

function renderActivity(jobs) {
  const bar = $("#activity-bar");
  const list = $("#activity-list");
  if (!bar || !list) return;
  if (!jobs.length) {
    bar.hidden = true;
    list.innerHTML = "";
    return;
  }
  bar.hidden = false;
  list.innerHTML = jobs
    .map((j) => {
      const detail =
        j.detail ||
        (j.progress != null && j.progress > 0 ? `${j.progress}%` : "") ||
        j.state ||
        "";
      return (
        `<span class="activity-chip"><strong>${esc(j.label)}</strong>` +
        `<span class="detail">${esc(detail)}</span></span>`
      );
    })
    .join("");
}

/* ---------- tabs ---------- */

$$(".nav-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    if (activeTab === "learn" && btn.dataset.tab !== "learn") {
      hideLearnPreview();
    }
    activeTab = btn.dataset.tab;
    $$(".nav-btn").forEach((b) => b.classList.toggle("active", b === btn));
    $$(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + activeTab));
    const title = $("#tab-title");
    if (title) title.textContent = TAB_TITLES[activeTab] || activeTab;
    if (activeTab === "bench") {
      loadHardware();
      wireBenchInfo();
loadBenchReadiness();
      refreshBenchData();
      $("#tab-bench")?.classList.add("tab-wide");
    } else {
      $("#tab-bench")?.classList.remove("tab-wide");
    }
    if (activeTab === "notebooks") loadNotebooks();
    if (activeTab === "settings") loadSettings();
    if (activeTab === "architectures") initArchWizard();
    closeMobileSidebar();
    window.__mxDissolveNav?.();
  });
});

(function setupDissolvingNav() {
  const shell = document.querySelector(".shell");
  const btn = $("#sidebar-toggle");
  const sidebar = $("#sidebar");
  if (!shell || !btn || !sidebar) return;
  const KEY = "mxstudio.navPinned";
  const OLD = "mxstudio.sidebarCollapsed";
  let leaveTimer = null;

  const syncCollapsed = () => {
    const pinned = shell.classList.contains("nav-pinned");
    const expanded = shell.classList.contains("nav-expanded");
    shell.classList.toggle("sidebar-collapsed", !pinned && !expanded);
  };

  const setPinned = (pinned) => {
    shell.classList.toggle("nav-pinned", pinned);
    shell.classList.toggle("nav-auto", !pinned);
    if (pinned) shell.classList.remove("nav-expanded");
    btn.textContent = pinned ? "●" : "○";
    btn.setAttribute("aria-pressed", pinned ? "true" : "false");
    btn.title = pinned ? "Unpin — nav dissolves when idle" : "Pin navigation open";
    btn.setAttribute("aria-label", btn.title);
    localStorage.setItem(KEY, pinned ? "1" : "0");
    syncCollapsed();
  };

  let pinned = localStorage.getItem(KEY);
  if (pinned == null) {
    // Prior expand preference becomes pin; otherwise dissolve by default.
    pinned = localStorage.getItem(OLD) === "0" ? "1" : "0";
  }
  setPinned(pinned === "1");

  btn.addEventListener("click", (ev) => {
    ev.stopPropagation();
    setPinned(!shell.classList.contains("nav-pinned"));
  });

  const open = () => {
    if (shell.classList.contains("nav-pinned")) return;
    if (matchMedia("(max-width: 800px)").matches) return;
    clearTimeout(leaveTimer);
    shell.classList.add("nav-expanded");
    syncCollapsed();
  };
  const scheduleClose = () => {
    if (shell.classList.contains("nav-pinned")) return;
    clearTimeout(leaveTimer);
    leaveTimer = setTimeout(() => {
      shell.classList.remove("nav-expanded");
      syncCollapsed();
    }, 240);
  };

  sidebar.addEventListener("mouseenter", open);
  sidebar.addEventListener("mouseleave", scheduleClose);
  sidebar.addEventListener("focusin", open);
  sidebar.addEventListener("focusout", (ev) => {
    if (!sidebar.contains(ev.relatedTarget)) scheduleClose();
  });

  // Expose for tab switches — chrome gets out of the way after navigation.
  window.__mxDissolveNav = () => {
    if (shell.classList.contains("nav-pinned")) return;
    shell.classList.remove("nav-expanded");
    syncCollapsed();
  };
})();

function closeMobileSidebar() {
  const shell = $(".shell");
  const backdrop = $("#sidebar-backdrop");
  const menu = $("#mobile-menu");
  shell?.classList.remove("sidebar-mobile-open");
  if (backdrop) backdrop.hidden = true;
  menu?.setAttribute("aria-expanded", "false");
}

(function setupMobileSidebar() {
  const shell = $(".shell");
  const menu = $("#mobile-menu");
  const backdrop = $("#sidebar-backdrop");
  if (!shell || !menu || !backdrop) return;
  menu.addEventListener("click", () => {
    const open = !shell.classList.contains("sidebar-mobile-open");
    shell.classList.toggle("sidebar-mobile-open", open);
    backdrop.hidden = !open;
    menu.setAttribute("aria-expanded", open ? "true" : "false");
  });
  backdrop.addEventListener("click", closeMobileSidebar);
  addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") {
      closeMobileSidebar();
      window.__mxDissolveNav?.();
    }
  });
})();

/* ---------- server status + active model switcher ---------- */

let modelSwitcherWired = false;
let modelSwitchBusy = false;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function setServeProgress({ label, detail = "", pct = null, indeterminate = false, hidden = false } = {}) {
  const box = $("#serve-progress");
  const bar = $("#serve-progress-bar");
  const labelEl = $("#serve-progress-label");
  const detailEl = $("#serve-progress-detail");
  if (!box) return;
  box.hidden = !!hidden;
  box.classList.toggle("indeterminate", !!indeterminate && !hidden);
  if (labelEl && label != null) labelEl.textContent = label;
  if (detailEl && detail != null) detailEl.textContent = detail;
  if (bar && !indeterminate && pct != null) bar.style.width = `${Math.max(0, Math.min(100, pct))}%`;
  if (bar && (hidden || indeterminate)) bar.style.width = indeterminate ? "36%" : "0%";
}

async function refreshServerStatus() {
  try {
    serverStatus = await api("/api/server");
  } catch {
    /* keep last */
  }
  renderServerStatus();
  return serverStatus || { state: "stopped" };
}

async function waitForServerState(predicate, { timeoutMs = 240000, intervalMs = 1000, onTick } = {}) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const status = await refreshServerStatus();
    if (onTick) onTick(status, Date.now() - started);
    if (predicate(status)) return status;
    await sleep(intervalMs);
  }
  throw new Error("Timed out waiting for the model server");
}

async function stopServerGracefully({ label } = {}) {
  const prev = serverStatus?.model_id || label || "model";
  setServeProgress({
    label: "Stopping",
    detail: String(prev).split("/").pop(),
    indeterminate: true,
  });
  await api("/api/server/stop", { method: "POST" });
  const status = await waitForServerState(
    (s) => !s || s.state === "stopped" || !s.model_id,
    {
      timeoutMs: 45000,
      intervalMs: 600,
      onTick: (s, elapsed) => {
        setServeProgress({
          label: "Stopping",
          detail: `${Math.round(elapsed / 1000)}s`,
          pct: Math.min(90, 20 + elapsed / 500),
        });
      },
    }
  );
  setServeProgress({ label: "Stopped", detail: "", pct: 100 });
  await sleep(350);
  setServeProgress({ hidden: true });
  return status;
}

async function startServerWithProgress(modelId, { name } = {}) {
  const short = name || String(modelId).split("/").pop();
  setServeProgress({
    label: "Starting",
    detail: short,
    pct: 8,
  });
  await api("/api/server/start", {
    method: "POST",
    body: JSON.stringify({ model_id: modelId }),
  });
  const status = await waitForServerState(
    (s) => s.state === "running" || s.state === "crashed",
    {
      timeoutMs: 300000,
      intervalMs: 1200,
      onTick: (s, elapsed) => {
        const up = Number(s.uptime_s) || Math.round(elapsed / 1000);
        const pct = Math.min(92, 12 + up * 1.8);
        setServeProgress({
          label: s.state === "starting" ? "Starting" : s.state || "Starting",
          detail: `${short} · ${up}s`,
          pct,
        });
      },
    }
  );
  if (status.state === "crashed") {
    setServeProgress({
      label: "Failed",
      detail: status.crash_reason || "see logs",
      pct: 100,
    });
    throw new Error(status.crash_reason || "Server crashed while starting");
  }
  setServeProgress({ label: "Running", detail: short, pct: 100 });
  await sleep(500);
  setServeProgress({ hidden: true });
  return status;
}

function renderServerStatus() {
  const s = serverStatus || { state: "stopped" };
  const pill = $("#server-pill");
  const state = s.state || "stopped";
  if (pill) {
    pill.className = "pill " + state;
    const extra = state === "starting" && s.uptime_s != null ? ` (${s.uptime_s}s)` : "";
    const stateLabels = {
      running: "Serving",
      starting: "Starting",
      stopping: "Stopping",
      crashed: "Crashed",
      idle: "Stopped",
      stopped: "Stopped",
    };
    const label = (stateLabels[state] || state) + extra;
    const modelName =
      (models || []).find((model) => model.id === s.model_id)?.name ||
      s.model_id?.split("/").pop() ||
      "";
    pill.textContent = stateLabels[state] || state;
    pill.title = modelName ? `${label}: ${modelName}` : `Server ${label.toLowerCase()}`;
    pill.setAttribute("aria-label", pill.title);
  }
  renderModelSwitcher();
}

function renderModelSwitcher() {
  const sel = $("#model-switcher");
  if (!sel) return;
  if (!modelSwitcherWired) {
    modelSwitcherWired = true;
    sel.addEventListener("change", onModelSwitcherChange);
  }
  if (modelSwitchBusy) return;

  const installed = (models || []).filter((m) => m.installed);
  const cur = serverStatus?.model_id || "";
  const state = serverStatus?.state || "stopped";
  const sig = installed.map((m) => m.id).join("|") + "::" + cur + "::" + state;
  if (sel.dataset.sig === sig) {
    sel.className = "model-switcher " + state + " select-ui-native";
    return;
  }
  sel.dataset.sig = sig;
  sel.innerHTML =
    `<option value="">Stopped</option>` +
    installed
      .map((m) => {
        const label = m.name || m.id;
        const mark = m.id === cur && (state === "running" || state === "starting") ? " ●" : "";
        return `<option value="${esc(m.id)}">${esc(label)}${mark}</option>`;
      })
      .join("");
  if (cur && installed.some((m) => m.id === cur)) sel.value = cur;
  else sel.value = "";
  sel.className = "model-switcher " + state + " select-ui-native";
}

async function onModelSwitcherChange(ev) {
  const sel = ev.target;
  const id = sel.value;
  const prev = serverStatus?.model_id || "";
  const state = serverStatus?.state || "stopped";
  const prevName =
    (models || []).find((m) => m.id === prev)?.name || prev.split("/").pop() || prev;

  const revert = () => {
    sel.dataset.sig = "";
    if (prev && (state === "running" || state === "starting")) sel.value = prev;
    else sel.value = "";
    renderModelSwitcher();
  };

  // Stopped selected
  if (!id) {
    if (state === "stopped" || state === "idle") return;
    const ok = confirm(
      `Stop the active API?\n\n${prevName || "Current model"} is ${state} on :8100.\nChat and benchmarks will pause until you serve another model.`
    );
    if (!ok) {
      revert();
      return;
    }
    modelSwitchBusy = true;
    sel.disabled = true;
    try {
      await stopServerGracefully({ label: prevName });
      toast("Server stopped", prevName || "", "ok");
      await pollModels();
      pollActivity();
    } catch (err) {
      toast("Stop failed", err.message, "err", { persist: true });
      setServeProgress({ hidden: true });
    } finally {
      modelSwitchBusy = false;
      sel.disabled = false;
      sel.dataset.sig = "";
      renderModelSwitcher();
    }
    return;
  }

  if (id === prev && (state === "running" || state === "starting")) return;

  const m = models.find((x) => x.id === id);
  const nextName = m?.name || id.split("/").pop() || id;
  const busy = state === "running" || state === "starting";
  if (busy && prev && prev !== id) {
    const ok = confirm(
      `Switch active model?\n\nStop ${prevName} (${state}), then start ${nextName}.\nCompile can take a few minutes — wait for the progress bar.`
    );
    if (!ok) {
      revert();
      return;
    }
  } else if (m?.serve_enabled === false) {
    toast("Serve unavailable", m.serve_disabled_reason || nextName, "warn", { persist: true });
    revert();
    return;
  }

  modelSwitchBusy = true;
  sel.disabled = true;
  try {
    if (busy && prev && prev !== id) {
      await stopServerGracefully({ label: prevName });
    }
    toast("Starting model", `${nextName} — wait for Running`, "warn");
    await startServerWithProgress(id, { name: nextName });
    toast("Model ready", `${nextName} is serving`, "ok");
    await pollModels();
    pollActivity();
  } catch (err) {
    toast("Switch failed", err.message, "err", { persist: true });
    setServeProgress({ hidden: true });
    await pollModels();
  } finally {
    modelSwitchBusy = false;
    sel.disabled = false;
    sel.dataset.sig = "";
    renderModelSwitcher();
  }
}


/* ---------- models tab ---------- */

let drawerModelId = null;
const loadedCards = new Set();
const loadingCards = new Set();
let lastHfReport = null;
let drawerWired = false;

function modelStatusBadges(m) {
  const badges = [];
  const dl = m.download;
  const downloading = dl && dl.state === "downloading";
  if (downloading) badges.push('<span class="badge downloading">downloading</span>');
  else if (m.installed) badges.push('<span class="badge installed">installed</span>');
  if (m.experimental) badges.push('<span class="badge exp">experimental</span>');
  if (m.server_state) {
    const st = String(m.server_state).toLowerCase();
    const cls =
      st === "running" ? "running" :
      st === "starting" ? "starting" :
      st === "crashed" ? "crashed" :
      "serving";
    badges.push(`<span class="badge ${cls}">${esc(m.server_state)}</span>`);
  }
  if (!badges.length) badges.push('<span class="badge idle">not installed</span>');
  return badges.join(" ");
}

function modelTableStatusBadge(m) {
  const dl = m.download;
  if (dl && dl.state === "downloading") {
    return '<span class="badge downloading">downloading</span>';
  }
  if (m.server_state) {
    const state = String(m.server_state).toLowerCase();
    const cls =
      state === "running" ? "running" :
      state === "starting" ? "starting" :
      state === "crashed" ? "crashed" :
      "serving";
    return `<span class="badge ${cls}">${esc(m.server_state)}</span>`;
  }
  if (m.installed) return '<span class="badge installed">installed</span>';
  return '<span class="badge idle">not installed</span>';
}

function modelTableSize(params) {
  const value = String(params || "?");
  const moe = value.match(/^(.+?)\s+total\s*\/\s*(.+?)\s+active/i);
  return moe ? `${moe[1]} / ${moe[2]}` : value;
}

const DRAWER_SVG = {
  pocket: `<svg viewBox="0 0 96 96" aria-hidden="true">
    <rect x="18" y="18" width="60" height="60" rx="14"></rect>
    <path d="M33 33h30v30H33zM48 8v10M48 78v10M8 48h10M78 48h10"></path>
    <path class="spark" d="m53 29-13 20h10l-7 18 16-23H48z"></path>
  </svg>`,
  workbench: `<svg viewBox="0 0 96 96" aria-hidden="true">
    <path d="M18 68h60M25 68V35h46v33M34 35V24h28v11"></path>
    <path class="spark" d="M36 51h24M48 41v20"></path>
    <circle cx="27" cy="76" r="4"></circle><circle cx="69" cy="76" r="4"></circle>
  </svg>`,
  experts: `<svg viewBox="0 0 96 96" aria-hidden="true">
    <circle cx="20" cy="48" r="8"></circle><circle cx="48" cy="20" r="8"></circle>
    <circle cx="76" cy="34" r="8"></circle><circle cx="76" cy="66" r="8"></circle>
    <circle class="spark" cx="48" cy="48" r="11"></circle>
    <path d="M28 48h9M48 37v-9M58 43l11-6M58 53l11 7"></path>
  </svg>`,
  heavyweight: `<svg viewBox="0 0 96 96" aria-hidden="true">
    <path d="M18 64h60l-8 14H26zM28 64l5-28h30l5 28M39 36V24h18v12"></path>
    <path class="spark" d="M39 49h18M48 40v18"></path>
  </svg>`,
};

function concise(text, max = 190) {
  const value = String(text || "").trim();
  if (value.length <= max) return value;
  const clipped = value.slice(0, max);
  const sentence = clipped.lastIndexOf(".");
  return (sentence > 80 ? clipped.slice(0, sentence + 1) : clipped.trimEnd() + "…");
}

function modelPersona(m) {
  const text = `${m.params || ""} ${m.architecture || ""} ${m.family || ""}`.toLowerCase();
  const approx = Number(m.approx_gb) || 0;
  if (text.includes("moe") || text.includes("mixture")) {
    return {
      icon: "experts",
      title: "An expert relay",
      summary: "A router activates only the specialists needed for each token—more knowledge without using every parameter at once.",
      best: m.when_to_use || "Learning expert routing, sparse inference, and systems ideas such as expert caching.",
      caution: m.serve_enabled === false
        ? m.serve_disabled_reason || "Production serving until its custom MAX adapter is complete."
        : "Simple demos where a smaller dense model starts faster.",
    };
  }
  if (approx && approx <= 2) {
    return {
      icon: "pocket",
      title: "A pocket laboratory",
      summary: "Small enough to experiment freely, fast enough to keep the feedback loop playful.",
      best: m.when_to_use || "Local API experiments, notebooks, UI demos, and learning how inference behaves.",
      caution: "Difficult multi-step reasoning, demanding coding, or tasks where factual depth matters most.",
    };
  }
  if (approx && approx <= 8) {
    return {
      icon: "workbench",
      title: "A capable local workbench",
      summary: "A practical balance between responsiveness and useful instruction-following on local hardware.",
      best: m.when_to_use || "Everyday local prompting, prototypes, and richer notebook experiments.",
      caution: "Very large-context workloads or frontier-level reasoning expectations.",
    };
  }
  return {
    icon: "heavyweight",
    title: "A local heavyweight",
    summary: "Built for deeper capability, with a larger memory and startup footprint to match.",
    best: m.when_to_use || "Tasks that benefit from more capacity and can justify a longer startup.",
    caution: "Quick smoke tests or hardware without comfortable VRAM headroom.",
  };
}

function architectureStory(m) {
  const text = `${m.architecture || ""} ${m.family || ""}`.toLowerCase();
  const moe = text.includes("moe") || text.includes("mixture");
  if (moe) {
    return `
      <div class="architecture-flow moe">
        <span class="architecture-node">Prompt</span><span class="architecture-arrow">→</span>
        <span class="architecture-node accent">Router</span><span class="architecture-arrow">→</span>
        <span class="expert-cluster"><span>Expert A</span><span>Expert B</span><span>Expert C</span></span>
        <span class="architecture-arrow">→</span><span class="architecture-node">Next token</span>
      </div>
      <p>The router chooses a small team of experts for each token, then merges their work.</p>`;
  }
  return `
    <div class="architecture-flow">
      <span class="architecture-node">Prompt</span><span class="architecture-arrow">→</span>
      <span class="architecture-node accent">Attention</span><span class="architecture-arrow">→</span>
      <span class="architecture-node">MLP</span><span class="architecture-arrow">→</span>
      <span class="architecture-node">Next token</span>
    </div>
    <p>Attention gathers context; the feed-forward layers transform it; the cycle repeats one token at a time.</p>`;
}

function setDrawerTab(name) {
  const selected = ["overview", "works", "publisher"].includes(name) ? name : "overview";
  $$(".drawer-tab").forEach((btn) => {
    const active = btn.dataset.drawerTab === selected;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-selected", active ? "true" : "false");
  });
  $$("[data-drawer-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.drawerPanel !== selected;
  });
  if (selected === "publisher" && drawerModelId) {
    const card = $("#drawer-card");
    if (!loadedCards.has(drawerModelId) || card?.dataset.for !== drawerModelId) {
      loadCard(drawerModelId);
    }
  }
}

function wireModelDrawer() {
  if (drawerWired) return;
  drawerWired = true;
  const close = () => closeModelDrawer();
  $("#model-drawer-close")?.addEventListener("click", close);
  $("#model-drawer-backdrop")?.addEventListener("click", close);
  $$(".drawer-tab").forEach((btn) => {
    btn.addEventListener("click", () => setDrawerTab(btn.dataset.drawerTab));
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && drawerModelId) closeModelDrawer();
  });
}

function closeModelDrawer() {
  drawerModelId = null;
  const drawer = $("#model-drawer");
  const backdrop = $("#model-drawer-backdrop");
  if (drawer) {
    drawer.hidden = true;
    drawer.setAttribute("aria-hidden", "true");
  }
  if (backdrop) backdrop.hidden = true;
  document.querySelectorAll(".models-table tr.is-open").forEach((r) => r.classList.remove("is-open"));
}

function openModelDrawer(modelId) {
  wireModelDrawer();
  drawerModelId = modelId;
  const m = models.find((x) => x.id === modelId);
  const drawer = $("#model-drawer");
  const backdrop = $("#model-drawer-backdrop");
  if (!m || !drawer) return;
  if (backdrop) backdrop.hidden = false;
  drawer.hidden = false;
  drawer.setAttribute("aria-hidden", "false");
  fillModelDrawer(m);
  setDrawerTab("overview");
  document.querySelectorAll(".models-table tr[data-id]").forEach((r) => {
    r.classList.toggle("is-open", r.dataset.id === modelId);
  });
}

function fillModelDrawer(m) {
  const dl = m.download;
  const downloading = dl && dl.state === "downloading";
  const canServe =
    m.installed &&
    m.serve_enabled !== false &&
    m.server_state !== "running" &&
    m.server_state !== "starting";
  const isServing = m.server_state === "running" || m.server_state === "starting";

  $("#drawer-name").textContent = m.name;
  $("#drawer-id").textContent = m.id;
  $("#drawer-badges").innerHTML = modelStatusBadges(m);
  const persona = modelPersona(m);
  $("#drawer-story-icon").innerHTML = DRAWER_SVG[persona.icon] || DRAWER_SVG.workbench;
  $("#drawer-story-title").textContent = persona.title;
  $("#drawer-story-summary").textContent = persona.summary;
  $("#drawer-best-for").textContent = concise(persona.best);
  $("#drawer-watch-for").textContent = concise(persona.caution);
  $("#drawer-desc").textContent = m.description || "";
  $("#drawer-meta").textContent =
    `${m.params} · ~${m.approx_gb} GB · ${Number(m.context || 0).toLocaleString()} ctx · ${m.license || ""}`;
  $("#drawer-innovation").textContent = m.innovation || "";
  $("#drawer-architecture-visual").innerHTML = architectureStory(m);
  $("#drawer-architecture-text").textContent =
    m.architecture || "The publisher has not provided an architecture summary yet.";
  $("#drawer-key-ideas").innerHTML = (m.key_ideas || [])
    .map(
      (idea, i) => `<article class="key-idea">
        <span>${String(i + 1).padStart(2, "0")}</span>
        <p>${esc(idea)}</p>
      </article>`
    )
    .join("") || '<p class="drawer-section-hint">Key ideas will appear when model metadata is available.</p>';
  $("#drawer-links").innerHTML = Object.entries(m.links || {})
    .map(([k, v]) => `<a href="${String(v).replace(/"/g, "&quot;")}" target="_blank" rel="noopener">${esc(k)} ↗</a>`)
    .join("");

  const prog = $("#drawer-progress");
  if (prog) {
    if (downloading) {
      prog.hidden = false;
      const pct = dl.pct != null ? dl.pct : 0;
      prog.innerHTML = `
        <div class="progress"><div style="width:${pct}%"></div></div>
        <div class="progress-label">${dl.pct != null ? dl.pct + "%" : ""} ${fmtGB(dl.done_bytes)} / ${fmtGB(dl.total_bytes)}</div>`;
    } else if (dl && dl.state === "error") {
      prog.hidden = false;
      prog.innerHTML = `<div class="progress-label" style="color:var(--err)">download failed: ${esc(dl.error || "")}</div>`;
    } else {
      prog.hidden = true;
      prog.innerHTML = "";
    }
  }

  const actions = $("#drawer-actions");
  if (actions) {
    actions.innerHTML = `
      ${!m.installed && !downloading ? `<button class="btn primary" data-act="download">Download</button>` : ""}
      ${downloading ? `<button class="btn" disabled>Downloading…</button>` : ""}
      ${canServe ? `<button class="btn primary" data-act="serve">Serve</button>` : ""}
      ${m.installed && m.serve_enabled === false ? `<button class="btn" disabled title="${esc(m.serve_disabled_reason || "Serving unavailable")}">Serve unavailable</button>` : ""}
      ${isServing ? `<button class="btn danger" data-act="stop">Stop</button>` : ""}
      ${m.installed ? `<button class="btn" data-act="folder" title="${esc(m.local_path || "HF cache")}">Open folder</button>` : ""}
      ${m.installed && !isServing && !downloading ? `<button class="btn danger" data-act="uninstall">Uninstall</button>` : ""}
      <button class="btn" data-act="notebook" title="Generate and open a code + slides learning lab">Create lab</button>`;
    actions.onclick = (ev) => {
      const act = ev.target.dataset && ev.target.dataset.act;
      if (act) handleModelAction(act, m, ev.target);
    };
  }

  const card = $("#drawer-card");
  if (card && card.dataset.for !== m.id) {
    card.dataset.for = m.id;
    card.dataset.loaded = "";
    card.textContent = "Open Publisher notes to load the Hugging Face README.";
  }
}

function renderModels() {
  wireModelDrawer();
  const list = $("#model-list");
  if (!list) return;

  const query = modelSearchQuery.trim().toLowerCase();
  const filteredModels = models.filter((model) => {
    if (!query) return true;
    return [model.name, model.id, model.family, model.architecture, model.license, model.params]
      .filter(Boolean)
      .join(" ")
      .toLowerCase()
      .includes(query);
  });
  const pageCount = Math.max(1, Math.ceil(filteredModels.length / MODEL_PAGE_SIZE));
  modelPage = Math.min(Math.max(modelPage, 1), pageCount);
  const pageStart = (modelPage - 1) * MODEL_PAGE_SIZE;
  const pageModels = filteredModels.slice(pageStart, pageStart + MODEL_PAGE_SIZE);
  const count = $("#model-result-count");
  if (count) count.textContent = `${filteredModels.length} of ${models.length} models`;
  const pagination = $("#model-pagination");
  if (pagination) pagination.hidden = filteredModels.length <= MODEL_PAGE_SIZE;
  const pageLabel = $("#model-page-label");
  if (pageLabel) pageLabel.textContent = `Page ${modelPage} of ${pageCount}`;
  const previous = $("#model-page-prev");
  const next = $("#model-page-next");
  if (previous) previous.disabled = modelPage === 1;
  if (next) next.disabled = modelPage === pageCount;

  const rows = pageModels
    .map((m) => {
      const dl = m.download;
      const downloading = dl && dl.state === "downloading";
      const canServe =
        m.installed &&
        m.serve_enabled !== false &&
        m.server_state !== "running" &&
        m.server_state !== "starting";
      const isServing = m.server_state === "running" || m.server_state === "starting";
      const open = drawerModelId === m.id;

      let primary = "";
      if (!m.installed && !downloading) {
        primary = `<button class="btn primary" data-act="download">Download</button>`;
      } else if (downloading) {
        primary = `<button class="btn" disabled>…</button>`;
      } else if (canServe) {
        primary = `<button class="btn primary" data-act="serve">Serve</button>`;
      } else if (m.installed && m.serve_enabled === false) {
        primary = `<button class="btn" disabled title="${esc(m.serve_disabled_reason || "Serving unavailable")}">Unavailable</button>`;
      } else if (isServing) {
        primary = `<button class="btn danger" data-act="stop">Stop</button>`;
      }

      return `
        <tr data-id="${esc(m.id)}" class="${open ? "is-open" : ""}">
          <td class="col-name">
            <div class="name-cell" title="${esc(m.id)}">
              <span class="nm">${esc(m.name)}</span>
            </div>
          </td>
          <td class="col-size col-num" title="${esc(m.params)} · estimated download: ~${m.approx_gb} GB">${esc(modelTableSize(m.params))}</td>
          <td class="col-ctx col-num">${Number(m.context || 0).toLocaleString()}</td>
          <td class="col-status">${modelTableStatusBadge(m)}</td>
          <td class="col-actions">
            ${primary}
            <button class="btn" data-act="details">Details</button>
          </td>
        </tr>`;
    })
    .join("");

  list.innerHTML = `
    <div class="models-table-wrap">
      <table class="models-table">
        <thead>
          <tr>
            <th>Model</th>
            <th>Size</th>
            <th>Context</th>
            <th>Status</th>
            <th></th>
          </tr>
        </thead>
        <tbody>${rows || `<tr><td colspan="5" class="models-empty">No matching models.</td></tr>`}</tbody>
      </table>
    </div>`;

  list.querySelector("tbody")?.addEventListener("click", (ev) => {
    const tr = ev.target.closest("tr[data-id]");
    if (!tr) return;
    const id = tr.dataset.id;
    const m = models.find((x) => x.id === id);
    if (!m) return;
    const act = ev.target.dataset && ev.target.dataset.act;
    if (act === "details" || (!act && !ev.target.closest("button"))) {
      if (drawerModelId === id) closeModelDrawer();
      else openModelDrawer(id);
      return;
    }
    if (act) handleModelAction(act, m, ev.target);
  });

  if (drawerModelId) {
    const still = models.find((x) => x.id === drawerModelId);
    if (still) fillModelDrawer(still);
    else closeModelDrawer();
  }
}

async function handleModelAction(act, m, btn) {
  try {
    if (act === "download") {
      if (!confirm(`Download ${m.name} in the background?`)) return;
      btn.disabled = true;
      const force = false;
      const res = await api(`/api/models/${encodeModelPath(m.id)}/download`, {
        method: "POST",
        body: JSON.stringify({ force }),
      });
      const jid = jobIdFrom(res);
      toast("Download queued", jid || m.name, "warn");
      if (jid) {
        waitForJob(jid)
          .then((job) => {
            const path = job.result?.local?.path || m.local_path || m.id;
            toast("Download done", path, "ok", {
              persist: true,
              actionLabel: "Open containing folder",
              onAction: () => openModelFolder(m.id),
            });
            pollModels();
          })
          .catch(async (e) => {
            const msg = e.message || "";
            if (/force|compatible|hardware|risk|VRAM|MAX/i.test(msg)) {
              const ok = confirm(
                msg + "\n\nForce download even if MAX/VRAM warnings apply?"
              );
              if (ok) {
                try {
                  const res2 = await api(`/api/models/${encodeModelPath(m.id)}/download`, {
                    method: "POST",
                    body: JSON.stringify({ force: true }),
                  });
                  const jid2 = jobIdFrom(res2);
                  toast("Download queued (force)", jid2 || m.name, "warn");
                  if (jid2) {
                    waitForJob(jid2)
                      .then((job) => {
                        const path = job.result?.local?.path || m.id;
                        toast("Download done", path, "ok", {
                          persist: true,
                          actionLabel: "Open containing folder",
                          onAction: () => openModelFolder(m.id),
                        });
                        pollModels();
                      })
                      .catch((e2) => toast("Download failed", e2.message, "err"));
                  }
                  pollActivity();
                  return;
                } catch (err2) {
                  toast("Download failed", err2.message, "err");
                  return;
                }
              }
            }
            toast("Download failed", msg, "err");
          });
      }
      pollModels();
      pollActivity();
    } else if (act === "serve") {
      const state = serverStatus?.state || "stopped";
      const cur = serverStatus?.model_id || "";
      if ((state === "running" || state === "starting") && cur && cur !== m.id) {
        const curName = models.find((x) => x.id === cur)?.name || cur;
        const ok = confirm(
          `Switch active model?

Stop ${curName} (${state}), then start ${m.name}.
Wait for the progress bar before chatting.`
        );
        if (!ok) {
          if (btn?.isConnected) btn.disabled = false;
          return;
        }
      }
      btn.disabled = true;
      modelSwitchBusy = true;
      try {
        if ((state === "running" || state === "starting") && cur && cur !== m.id) {
          await stopServerGracefully({ label: cur });
        }
        toast("Starting model", `${m.name} — wait for Running`, "warn");
        await startServerWithProgress(m.id, { name: m.name });
        toast("Model ready", `${m.name} is serving`, "ok");
      } finally {
        modelSwitchBusy = false;
      }
      pollModels();
      pollActivity();
    } else if (act === "stop") {
      const ok = confirm(
        `Stop the active API?

${m.name} will go offline on :8100.`
      );
      if (!ok) return;
      btn.disabled = true;
      modelSwitchBusy = true;
      try {
        await stopServerGracefully({ label: m.name });
        toast("Server stopped", m.name, "ok");
      } finally {
        modelSwitchBusy = false;
      }
      pollModels();
      pollActivity();
    } else if (act === "folder") {
      await openModelFolder(m.id);
    } else if (act === "uninstall") {
      const ok = confirm(
        `Uninstall ${m.name}?\n\nThis deletes the local Hugging Face cache for ${m.id} and frees disk space.`
      );
      if (!ok) return;
      btn.disabled = true;
      const res = await api(`/api/models/${encodeModelPath(m.id)}`, { method: "DELETE" });
      const gb = res.freed_bytes != null ? (res.freed_bytes / 1e9).toFixed(2) : "?";
      toast("Uninstalled", `${m.name} · freed ~${gb} GB`, "ok");
      if (drawerModelId === m.id) closeModelDrawer();
      pollModels();
    } else if (act === "details") {
      if (drawerModelId === m.id) closeModelDrawer();
      else openModelDrawer(m.id);
    } else if (act === "notebook") {
      await generateModelLab(m.id, btn, { navigate: true, label: m.name });
    }
  } catch (err) {
    if (btn?.isConnected) btn.disabled = false;
    toast("Action failed", err.message, "err", { persist: true });
    pollModels();
  }
}

async function loadCard(modelId) {
  const el = $("#drawer-card");
  if (!el || drawerModelId !== modelId) return;
  if (loadingCards.has(modelId)) return;
  if (loadedCards.has(modelId) && el.dataset.for === modelId && el.dataset.loaded === "1") {
    return;
  }
  loadingCards.add(modelId);
  el.dataset.for = modelId;
  el.textContent = "Fetching publisher documentation in the background…";
  try {
    const queued = await api("/api/jobs/start", {
      method: "POST",
      body: JSON.stringify({ kind: "model_card", model_id: modelId }),
    });
    const jid = jobIdFrom(queued);
    const job = jid ? await waitForJob(jid) : null;
    const res = job?.result || queued;
    if (drawerModelId !== modelId) return;
    const md = res.markdown || "(model card not available)";
    el.innerHTML = window.marked ? marked.parse(md) : `<pre>${esc(md)}</pre>`;
    el.dataset.loaded = "1";
    loadedCards.add(modelId);
  } catch (err) {
    if (drawerModelId !== modelId) return;
    el.innerHTML = `<p>Publisher documentation unavailable: ${esc(err.message)}</p>
      <button class="btn subtle" type="button" data-retry-card>Retry documentation</button>`;
    el.querySelector("[data-retry-card]")?.addEventListener("click", () => loadCard(modelId));
  } finally {
    loadingCards.delete(modelId);
  }
}

async function loadEnvPill() {
  const el = $("#env-pill");
  if (!el) return;
  try {
    const env = await api("/api/env");
    const maxStatus = env.max_cli
      ? `${env.max_version || "MAX installed"}${env.max_cli_source === "pixi" ? " via project pixi" : ""}`
      : `MAX unavailable · ${env.max_install_command || "run pixi install"}`;
    const parts = [
      env.hf_token_set ? "HF_TOKEN ok" : "HF_TOKEN missing (ok for public models)",
      `cache ${env.hf_hub_cache}`,
      env.disk_free_gb != null ? `${env.disk_free_gb} GB free` : null,
      maxStatus,
    ].filter(Boolean);
    el.textContent = parts.join(" · ");
    el.className = "env-pill " + (env.max_cli ? "ok" : "warn");
    renderSetupPanel(env);
  } catch (err) {
    el.textContent = "env check failed: " + err.message;
    el.className = "env-pill warn";
  }
}

function renderSetupPanel(env) {
  const panel = $("#setup-panel");
  const supportsSetup = typeof env.project_environment === "boolean"
    && typeof env.expected_environment_path === "string";
  if (!panel || !supportsSetup || env.setup_confirmed) {
    if (panel) panel.hidden = true;
    return;
  }
  const safe = env.project_environment;
  $("#setup-title").textContent = safe
    ? "Your Python stays untouched."
    : "Open mxstudio safely.";
  $("#setup-message").textContent = safe
    ? "mxstudio uses its own project-local Pixi environment. It will not modify your system Python."
    : "This launch is outside mxstudio's project environment. Restart with Pixi so your system Python remains unchanged.";
  $("#setup-path").textContent = safe
    ? env.environment_path
    : `Expected: ${env.expected_environment_path}`;
  $("#setup-commands").hidden = safe;
  $("#setup-confirm").hidden = !safe;
  panel.hidden = false;
}

async function confirmEnvironmentSetup() {
  const button = $("#setup-confirm");
  button.disabled = true;
  try {
    await api("/api/setup", { method: "PUT" });
    $("#setup-panel").hidden = true;
    toast("Environment ready", "mxstudio will keep using its isolated Pixi environment.", "ok");
  } catch (err) {
    toast("Restart with Pixi", err.message, "warn");
  } finally {
    button.disabled = false;
  }
}

function renderHfReport(report) {
  const box = $("#hf-report");
  const dlBtn = $("#hf-download");
  lastHfReport = report;
  if (!box) return;
  if (!report) {
    box.hidden = true;
    if (dlBtn) dlBtn.disabled = true;
    return;
  }
  if (!report.ok) {
    box.hidden = false;
    box.innerHTML = `<div style="color:var(--err)">${esc(report.error || "check failed")}</div>
      <p class="tab-hint">${esc(report.hint || "")}</p>`;
    if (dlBtn) dlBtn.disabled = true;
    return;
  }
  const hw = report.hardware || {};
  const env = report.env || {};
  const compat = report.modular_compatible
    ? '<span class="badge installed">modular ok</span>'
    : '<span class="badge exp">compat unknown</span>';
  const risk = report.risk || "ok";
  const riskClass =
    risk === "ok" ? "ok" : risk === "tight_vram" ? "warn" : "err";
  const warns = (report.warnings || [])
    .map((w) => `<li>${esc(w)}</li>`)
    .join("");
  const steps = (report.steps || [])
    .map((s) => `<li>${esc(s)}</li>`)
    .join("");
  const hfUrl = report.hf_url || `https://huggingface.co/${report.model_id}`;
  box.hidden = false;
  box.innerHTML = `
    <div class="row"><span class="k">Model</span><span>
      <a href="${esc(hfUrl)}" target="_blank" rel="noopener">${esc(report.model_id)}</a>
    </span> ${compat}</div>
    <div class="row"><span class="k">Architecture</span><span>${esc(report.architecture || "?")}</span></div>
    <div class="row"><span class="k">Size</span><span>~${report.approx_gb ?? "?"} GB · safetensors ${report.has_safetensors ? "yes" : "no"}</span></div>
    <div class="row"><span class="k">Gated</span><span>${report.gated ? "yes" : "no"} · token ${
      env.hf_token_set ? `set (${esc(env.hf_token_source || "unknown")})` : "missing"
    }</span></div>
    <div class="row"><span class="k">VRAM fit</span><span>${esc(hw.level || "?")} — ${esc(hw.message || "")}</span></div>
    <div class="row"><span class="k">Docs</span>
      <a href="${report.handbook || "https://handbook.modular.com/"}" target="_blank" rel="noopener">Handbook</a> ·
      <a href="${report.models_docs || "https://docs.modular.com/max/models/"}" target="_blank" rel="noopener">MAX models</a>
    </div>
    <div class="risk-banner ${riskClass}">${esc(report.risk_message || risk)}</div>
    ${warns ? `<ul class="warn-list">${warns}</ul>` : ""}
    ${steps ? `<ol class="warn-list" style="list-style:decimal">${steps}</ol>` : ""}
    <p class="tab-hint" style="margin:8px 0 0">
      Can download: <strong>${report.can_download ? "yes" : "no"}</strong> ·
      Recommend: <strong>${report.recommend_download ? "yes" : "no"}</strong>
    </p>`;
  if (dlBtn) dlBtn.disabled = !(report.can_download || !report.gated);
}

$("#hf-help-toggle")?.addEventListener("click", () => {
  const help = $("#hf-help");
  const btn = $("#hf-help-toggle");
  if (!help || !btn) return;
  const open = help.hidden;
  help.hidden = !open;
  btn.setAttribute("aria-expanded", open ? "true" : "false");
  btn.textContent = open ? "Hide help" : "Help";
});

$("#model-search")?.addEventListener("input", (event) => {
  modelSearchQuery = event.target.value || "";
  modelPage = 1;
  renderModels();
});

$("#model-page-prev")?.addEventListener("click", () => {
  modelPage -= 1;
  renderModels();
});

$("#model-page-next")?.addEventListener("click", () => {
  modelPage += 1;
  renderModels();
});

$("#hf-check")?.addEventListener("click", async () => {
  const id = ($("#hf-id").value || "").trim();
  if (!id) {
    toast("Hugging Face id required", "Use org/name format", "warn");
    return;
  }
  try {
    $("#hf-check").disabled = true;
    const res = await api("/api/models/check", {
      method: "POST",
      body: JSON.stringify({ model_id: id }),
    });
    const jid = jobIdFrom(res);
    let report = res.report || (!res.queued ? res : null);
    if (res.queued && jid) {
      toast("queued in background", jid, "warn");
      const job = await waitForJob(jid, {
        onTick: (j) => {
          const pill = $("#env-pill");
          if (pill && j.detail) pill.textContent = j.detail;
        },
      });
      report = job.result?.report;
    }
    renderHfReport(report);
    if (report?.ok) {
      toast(
        "Check complete",
        report.model_id,
        report.modular_compatible ? "ok" : "warn"
      );
    }
  } catch (err) {
    toast("Check failed", err.message, "err");
  } finally {
    $("#hf-check").disabled = false;
  }
});

$("#hf-download")?.addEventListener("click", async () => {
  const id = ($("#hf-id")?.value || "").trim();
  if (!id) return;
  try {
    $("#hf-download").disabled = true;
    let force = false;
    const hwLevel = lastHfReport?.hardware?.level;
    const needsForce =
      lastHfReport &&
      (!lastHfReport.modular_compatible ||
        hwLevel === "slow" ||
        hwLevel === "insufficient");
    if (needsForce) {
      force = confirm(
        (lastHfReport.risk_message ||
          `${id} may not run well on this machine / MAX.`) +
          "\n\nDownload anyway (force)?"
      );
      if (!force) {
        $("#hf-download").disabled = false;
        return;
      }
    }
    const res = await api("/api/models/download-hf", {
      method: "POST",
      body: JSON.stringify({ model_id: id, force }),
    });
    const jid = jobIdFrom(res);
    toast("Download queued", jid || id, "warn");
    if (jid) {
      waitForJob(jid)
        .then((job) => {
          const path = job.result?.local?.path || id;
          toast("Download done", path, "ok", {
            persist: true,
            actionLabel: "Open containing folder",
            onAction: () => openModelFolder(id),
          });
        })
        .catch((e) => toast("Download failed", e.message, "err"))
        .finally(() => {
          pollModels();
          pollActivity();
        });
    }
    pollModels();
    pollActivity();
  } catch (err) {
    const msg = typeof err.message === "string" ? err.message : JSON.stringify(err.message);
    toast("Download blocked", msg, "err");
  } finally {
    $("#hf-download").disabled = false;
  }
});

/* ---------- polling ---------- */

async function pollModels() {
  try {
    const [ms, ss] = await Promise.all([api("/api/models"), api("/api/server")]);
    const renderKey = JSON.stringify(ms);
    const modelsChanged = renderKey !== lastModelsRenderKey;
    models = ms;
    serverStatus = ss;
    renderServerStatus();
    if (activeTab === "models" && modelsChanged) {
      lastModelsRenderKey = renderKey;
      renderModels();
    }
  } catch {
    /* backend briefly unavailable; retry on next tick */
  }
}

async function pollActivity() {
  try {
    const act = await api("/api/activity");
    const jobs = act.jobs || [];
    renderActivity(jobs);

    // Toasts when background jobs start / finish
    const ids = new Set(jobs.map((j) => j.id));
    for (const j of jobs) {
      if (!lastActivityIds.has(j.id)) {
        const kind = String(j.kind || j.label || "");
        // Architecture wizard announces its own jobs; skip the duplicate chip toast.
        if (kind.startsWith("arch_") || /scaffold|smoke|inspect/i.test(kind + " " + (j.label || ""))) {
          continue;
        }
        toast("Background job started", `${j.label} — ${j.detail || "running"}`, "warn");
      }
    }
    lastActivityIds = ids;

    const serve = act.server || {};
    if (lastServeState === "starting" && serve.state === "running") {
      toast("Model ready", `${serve.model_id} is serving`, "ok");
    } else if (lastServeState === "starting" && serve.state === "crashed") {
      const why = serve.crash_reason || "Check server logs in the Models tab";
      toast("Serve failed", why, "err", { persist: true });
    } else if (lastServeState === "running" && serve.state === "crashed") {
      const why = serve.crash_reason || "Model process died while serving";
      toast("Serve crashed", why, "err", { persist: true });
    }
    lastServeState = serve.state || "stopped";
    serverStatus = serve;
    renderServerStatus();

    const bench = act.bench || {};
    if (bench.state === "running" || lastBenchState === "running") {
      if (activeTab === "bench") renderBenchResult(bench);
    }
    if (lastBenchState === "running" && bench.state === "done") {
      const btn = $("#bench-btn");
      if (btn) btn.disabled = false;
      if (activeTab === "bench") {
        renderBenchResult(bench);
        renderBenchHistory();
        loadHardware();
      }
    } else if (lastBenchState === "running" && bench.state === "error") {
      const btn = $("#bench-btn");
      if (btn) btn.disabled = false;
      if (activeTab === "bench") renderBenchResult(bench);
    }
    lastBenchState = bench.state || "idle";

    const nb = act.notebook || {};
    if (nb.state === "running" && openNotebook && nb.notebook === openNotebook) {
      applyNotebookJobProgress(nb);
    }
    if (lastNbState === "running" && nb.state === "done") {
      toast("Notebook finished", nb.notebook || "run complete", "ok");
      if (openNotebook && nb.notebook === openNotebook) {
        applyNotebookJobProgress(nb);
        finishNotebookJob(nb, true);
      }
      nbBusy = false;
      setKernelStatus("Python 3 | idle", false);
      $("#nb-run-all") && ($("#nb-run-all").disabled = false);
      $("#nb-run") && ($("#nb-run").disabled = false);
    } else if (lastNbState === "running" && nb.state === "error") {
      toast("Notebook failed", nb.error || "error", "err");
      nbBusy = false;
      setKernelStatus("Python 3 | error", false);
      $("#nb-run-all") && ($("#nb-run-all").disabled = false);
      $("#nb-run") && ($("#nb-run").disabled = false);
    }
    lastNbState = nb.state || "idle";

    const arch = act.architecture || {};
    if (lastArchState === "running" || lastArchState === "processing") {
      if (arch.state === "done") {
        if (activeTab === "architectures") {
          applyArchJobResult(arch);
          setArchJobStatus(arch.detail || "Done", "ok");
        }
      } else if (arch.state === "error" || arch.state === "failed") {
        if (activeTab === "architectures") {
          setArchJobStatus(arch.error || "Failed", "err");
        } else {
          toast("Architecture job failed", arch.error || "error", "err");
        }
        if (activeTab === "architectures") applyArchJobResult(arch);
      }
    }
    lastArchState = arch.state || "idle";
  } catch {
    /* ignore transient */
  }
}

function startPolling(task, activeInterval, idleInterval = 15000) {
  let timer = null;
  let running = false;

  const schedule = (delay) => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(run, delay);
  };
  const run = async () => {
    if (running) return;
    running = true;
    try {
      await task();
    } finally {
      running = false;
      const idle = document.hidden || !navigator.onLine;
      schedule(idle ? idleInterval : activeInterval);
    }
  };
  const wake = () => {
    if (!document.hidden && navigator.onLine && !running) schedule(0);
  };

  document.addEventListener("visibilitychange", wake);
  window.addEventListener("online", wake);
  run();
}

let lastHwSnapshot = null;
let hardwareLoading = false;

startPolling(pollModels, 2500);
startPolling(pollActivity, 1500);
$("#setup-confirm")?.addEventListener("click", confirmEnvironmentSetup);
$("#setup-later")?.addEventListener("click", () => { $("#setup-panel").hidden = true; });
$("#setup-dismiss")?.addEventListener("click", () => { $("#setup-panel").hidden = true; });
loadEnvPill();
refreshBenchData();
loadBenchReadiness();
startPolling(loadHardware, 15000, 60000);

/* ---------- floating chat ---------- */

function isChatOpen() {
  return !!$("#chat-panel")?.classList.contains("is-open");
}

function isChatSettingsOpen() {
  return !!$("#chat-panel")?.classList.contains("settings-open");
}

function setChatSettingsOpen(open) {
  const panel = $("#chat-panel");
  const toggle = $("#chat-settings-toggle");
  const settings = $("#chat-settings");
  if (!panel) return;
  open = !!open;
  panel.classList.toggle("settings-open", open);
  toggle?.setAttribute("aria-expanded", open ? "true" : "false");
  settings?.setAttribute("aria-hidden", open ? "false" : "true");
}

function setChatOpen(open) {
  const panel = $("#chat-panel");
  const fab = $("#chat-fab");
  if (!panel) return;
  open = !!open;
  panel.classList.toggle("is-open", open);
  // Do not use the HTML `hidden` attribute — it fights `display:flex` in CSS.
  fab?.classList.toggle("open", open);
  fab?.setAttribute("aria-expanded", open ? "true" : "false");
  fab?.setAttribute("title", open ? "Close chat" : "Open chat");
  fab?.setAttribute("aria-label", open ? "Close chat" : "Open chat");
  if (!open) setChatSettingsOpen(false);
}

function toggleChat() {
  setChatOpen(!isChatOpen());
}

// Event delegation so close/toggle still work even if nodes are re-rendered.
document.addEventListener("click", (ev) => {
  const t = ev.target;
  if (!(t instanceof Element)) return;
  if (t.closest("#chat-fab")) {
    ev.preventDefault();
    toggleChat();
    return;
  }
  if (t.closest("#chat-panel-close")) {
    ev.preventDefault();
    setChatOpen(false);
    return;
  }
  if (t.closest("#chat-settings-toggle")) {
    ev.preventDefault();
    setChatSettingsOpen(!isChatSettingsOpen());
    return;
  }
  if (t.closest("#chat-settings-backdrop")) {
    ev.preventDefault();
    setChatSettingsOpen(false);
  }
});
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && isChatOpen()) {
    if (isChatSettingsOpen()) setChatSettingsOpen(false);
    else setChatOpen(false);
  }
});

$("#temperature")?.addEventListener("input", (e) => {
  const out = $("#temp-out");
  if (out) out.textContent = e.target.value;
});
$("#top_p")?.addEventListener("input", (e) => {
  const out = $("#topp-out");
  if (out) out.textContent = e.target.value;
});

$("#chat-clear")?.addEventListener("click", () => {
  chatMessages = [];
  renderChat();
});

(function setupChatMic() {
  const mic = $("#chat-mic");
  if (!mic) return;
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) {
    mic.addEventListener("click", () => {
      toast("Voice input unavailable", "SpeechRecognition is not supported in this browser", "warn");
    });
    return;
  }
  const recog = new SR();
  recog.continuous = false;
  recog.interimResults = false;
  recog.lang = "en-US";
  let listening = false;
  mic.addEventListener("click", () => {
    if (listening) {
      try { recog.stop(); } catch {}
      return;
    }
    try {
      recog.start();
    } catch (err) {
      toast("Mic failed", err.message || String(err), "err");
    }
  });
  recog.onstart = () => {
    listening = true;
    mic.classList.add("listening");
  };
  recog.onend = () => {
    listening = false;
    mic.classList.remove("listening");
  };
  recog.onerror = () => {
    listening = false;
    mic.classList.remove("listening");
  };
  recog.onresult = (ev) => {
    const text = [...ev.results].map((r) => r[0].transcript).join(" ").trim();
    const input = $("#chat-input");
    if (input && text) {
      input.value = (input.value ? input.value + " " : "") + text;
      input.focus();
    }
  };
})();

function renderChat() {
  const box = $("#chat-messages");
  if (!box) return;
  if (chatMessages.length === 0) {
    box.innerHTML = '<div class="chat-empty">Serve a model from Models, then say hello — or hold the mic.</div>';
    return;
  }
  box.innerHTML = chatMessages
    .map((m) => `<div class="msg ${m.role}">${esc(m.content)}</div>`)
    .join("");
  box.scrollTop = box.scrollHeight;
}

function apiMessages() {
  // Only roles the OpenAI/MAX API accepts — never send UI-only "error" rows.
  return chatMessages
    .filter((m) => m.role === "user" || m.role === "assistant" || m.role === "system")
    .filter((m) => m.content); // drop the empty assistant placeholder
}

async function requestGeneratedProbe() {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      messages: [{ role: "user", content: "Reply with exactly: READY" }],
      temperature: 0,
      top_p: 1,
      max_tokens: 8,
    }),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch {}
    throw new Error(`generation probe failed: ${detail}`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let generated = "";
  for (;;) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const events = buffer.split("\n\n");
    buffer = events.pop() || "";
    for (const event of events) {
      if (!event.startsWith("data: ")) continue;
      const data = JSON.parse(event.slice(6));
      if (data.error) throw new Error(data.error);
      if (data.delta) generated += data.delta;
    }
    if (done) break;
  }
  if (!generated.trim()) throw new Error("generation probe returned no text");
  return generated.trim();
}

$("#chat-form")?.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  if (chatBusy) return;
  if (serverStatus.state !== "running") {
    chatMessages.push({
      role: "error",
      content: `Wait until the server pill shows "running" (currently: ${serverStatus.state}).`,
    });
    renderChat();
    return;
  }
  const input = $("#chat-input");
  if (!input) return;
  const text = input.value.trim();
  if (!text) return;
  input.value = "";

  // Drop previous UI error banners so they don't clutter the thread.
  chatMessages = chatMessages.filter((m) => m.role !== "error");
  chatMessages.push({ role: "user", content: text });
  chatMessages.push({ role: "assistant", content: "" });
  renderChat();
  chatBusy = true;

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        messages: apiMessages(),
        temperature: parseFloat($("#temperature")?.value || "0.7"),
        top_p: parseFloat($("#top_p")?.value || "0.95"),
        max_tokens: parseInt($("#max_tokens")?.value || "256", 10),
      }),
    });
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch {}
      throw new Error(detail);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split("\n\n");
      buffer = events.pop();
      for (const evt of events) {
        if (!evt.startsWith("data: ")) continue;
        const data = JSON.parse(evt.slice(6));
        if (data.delta) {
          chatMessages[chatMessages.length - 1].content += data.delta;
          renderChat();
        }
        if (data.error) throw new Error(data.error);
      }
    }
  } catch (err) {
    chatMessages.pop(); // remove empty/partial assistant turn
    chatMessages.push({ role: "error", content: err.message });
    renderChat();
  } finally {
    chatBusy = false;
  }
});

/* ---------- notebooks tab — Jupyter-style in-app editor ---------- */

let openNotebook = null;
let openNotebookModelId = null;
let revealDeck = null;
let revealResizeObserver = null;

function relayoutRevealDeck() {
  if (!revealDeck) return;
  try {
    revealDeck.layout();
  } catch {}
  requestAnimationFrame(() => {
    try {
      revealDeck?.layout?.();
    } catch {}
  });
}

function watchRevealSize() {
  const el = document.querySelector(".nb-reveal");
  if (!el || typeof ResizeObserver === "undefined") return;
  if (revealResizeObserver) revealResizeObserver.disconnect();
  revealResizeObserver = new ResizeObserver(() => relayoutRevealDeck());
  revealResizeObserver.observe(el);
}
let nbBusy = false;
let nbCells = []; // {index, cell_type, source, exec?}
let nbSelected = 0;
let nbMode = "command"; // command | edit
let nbExecCounter = 0;
let lastNotebookProgressKey = "";

function setKernelStatus(text, busy) {
  const el = $("#nb-kernel-status");
  if (!el) return;
  el.textContent = text;
  el.classList.toggle("busy", !!busy);
}

async function loadNotebooks() {
  const select = $("#nb-model");
  if (models.length) {
    select.innerHTML = models
      .map((m) => `<option value="${m.id}">${esc(m.name)}</option>`)
      .join("");
  }
  try {
    const nbs = await api("/api/notebooks");
    const list = $("#nb-list");
    if (!nbs.length) {
      list.innerHTML = '<p class="tab-hint">No notebooks yet — generate one above.</p>';
      return;
    }
    list.innerHTML = nbs
      .map(
        (nb) => `
      <div class="nb-item">
        <div>
          <div>${esc(nb.name)}</div>
          <div class="path">${esc(nb.path)}</div>
        </div>
        <div class="nb-item-actions">
          <button class="btn" data-show-folder="${esc(nb.name)}">Show in Explorer</button>
          <button class="btn primary" data-open="${esc(nb.name)}">Open</button>
        </div>
      </div>`
      )
      .join("");
    list.querySelectorAll("[data-open]").forEach((btn) => {
      btn.addEventListener("click", () => openNotebookViewer(btn.dataset.open));
    });
    list.querySelectorAll("[data-show-folder]").forEach((btn) => {
      btn.addEventListener("click", () => openNotebookFolder(btn.dataset.showFolder));
    });
  } catch {}
}

async function generateModelLab(modelId, btn, { navigate = false, label = modelId } = {}) {
  const original = btn?.textContent || "Notebook";
  try {
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Queuing lab…";
    }
    const res = await api(
      `/api/models/${encodeModelPath(modelId)}/notebook?background=true`,
      { method: "POST" }
    );
    const jid = jobIdFrom(res);
    toast("Lab queued", `${label} · notebook with optional slides`, "warn");
    let result = res;
    if (jid) {
      if (btn) btn.textContent = "Generating…";
      const job = await waitForJob(jid, {
        onTick: (j) => {
          if (btn && j.detail) btn.textContent = j.detail;
        },
      });
      result = job.result || job;
    }
    if (navigate) {
      closeModelDrawer();
      $(`.nav-btn[data-tab="notebooks"]`)?.click();
    }
    await loadNotebooks();
    if (result.name) {
      await openNotebookViewer(result.name);
    }
    toast("Lab ready", `${result.name || label} · notebook opened`, "ok");
    return result;
  } catch (err) {
    toast("Lab generation failed", err.message, "err", { persist: true });
    throw err;
  } finally {
    if (btn?.isConnected) {
      btn.disabled = false;
      btn.textContent = original;
    }
  }
}

$("#nb-btn").addEventListener("click", async () => {
  const id = $("#nb-model").value;
  if (!id) return;
  const btn = $("#nb-btn");
  try {
    await generateModelLab(id, btn);
  } catch {}
});

$("#nb-back").addEventListener("click", () => {
  closeNotebookSlides(false);
  $("#nb-viewer").hidden = true;
  $("#nb-browser").hidden = false;
  openNotebook = null;
  openNotebookModelId = null;
});

$("#nb-slides-btn")?.addEventListener("click", () => openNotebookSlides());
$("#nb-slides-close")?.addEventListener("click", () => closeNotebookSlides(true));
$$("[data-nb-focus]").forEach((btn) => {
  btn.addEventListener("click", () => setNotebookFocus(btn.dataset.nbFocus));
});

(function setupNotebookResizer() {
  const handle = $("#nb-resizer");
  const ws = $("#nb-workspace");
  if (!handle || !ws) return;
  const setRatio = (clientX) => {
    const rect = ws.getBoundingClientRect();
    const pct = Math.max(30, Math.min(70, ((clientX - rect.left) / rect.width) * 100));
    ws.style.setProperty("--nb-left", `${pct}%`);
    handle.setAttribute("aria-valuenow", String(Math.round(pct)));
    relayoutRevealDeck();
  };
  handle.addEventListener("pointerdown", (ev) => {
    handle.setPointerCapture(ev.pointerId);
    handle.classList.add("dragging");
  });
  handle.addEventListener("pointermove", (ev) => {
    if (handle.hasPointerCapture(ev.pointerId)) setRatio(ev.clientX);
  });
  handle.addEventListener("pointerup", (ev) => {
    handle.releasePointerCapture(ev.pointerId);
    handle.classList.remove("dragging");
  });
  handle.addEventListener("keydown", (ev) => {
    if (ev.key !== "ArrowLeft" && ev.key !== "ArrowRight") return;
    ev.preventDefault();
    const current = parseFloat(handle.getAttribute("aria-valuenow") || "50");
    const next = current + (ev.key === "ArrowLeft" ? -5 : 5);
    const rect = ws.getBoundingClientRect();
    setRatio(rect.left + (Math.max(30, Math.min(70, next)) / 100) * rect.width);
  });
})();

$("#nb-run").addEventListener("click", () => runSelectedCell());
$("#nb-run-all").addEventListener("click", () => runAllCells());
$("#nb-save").addEventListener("click", () => saveNotebook());
$("#nb-add-code").addEventListener("click", () => insertCell("code"));
$("#nb-add-md").addEventListener("click", () => insertCell("markdown"));
$("#nb-reset").addEventListener("click", async () => {
  if (!openNotebook) return;
  try {
    await api(`/api/notebooks/${openNotebook}/reset`, { method: "POST" });
    nbExecCounter = 0;
    nbCells.forEach((c) => { c.exec = null; c.output = null; });
    renderJpNotebook();
    setKernelStatus("Python 3 | restarted", false);
  } catch (err) {
    alert(err.message);
  }
});

function syncSourcesFromDom() {
  $$(".jp-cell").forEach((el) => {
    const i = parseInt(el.dataset.index, 10);
    const cell = nbCells[i];
    if (!cell) return;
    if (cell.cell_type === "code") {
      const ta = el.querySelector(".jp-code");
      if (ta) cell.source = ta.value;
    } else {
      const ta = el.querySelector(".jp-md-edit");
      if (ta) cell.source = ta.value;
    }
  });
}

function collectCells() {
  syncSourcesFromDom();
  return nbCells.map((c, i) => ({
    index: i,
    cell_type: c.cell_type,
    source: c.source,
  }));
}

async function openNotebookViewer(name) {
  try {
    openNotebook = name;
    const nb = await api(`/api/notebooks/${name}`);
    openNotebookModelId = nb.metadata?.mxstudio?.model_id || null;
    closeNotebookSlides(false);
    $("#nb-title").textContent = name;
    $("#nb-browser").hidden = true;
    $("#nb-viewer").hidden = false;
    nbCells = nb.cells.map((c) => ({
      ...c,
      exec: null,
      output: null,
    }));
    nbSelected = 0;
    nbMode = "command";
    nbExecCounter = 0;
    lastNotebookProgressKey = "";
    renderJpNotebook();
    setKernelStatus("Python 3 | idle", false);
    $("#nb-cells").focus();
  } catch (err) {
    alert(err.message);
  }
}

function setNotebookFocus(mode) {
  const focus = ["notebook", "split", "slides"].includes(mode) ? mode : "notebook";
  const ws = $("#nb-workspace");
  const tab = $("#tab-notebooks");
  if (!ws) return;
  ws.classList.remove("focus-notebook", "focus-split", "focus-slides");
  ws.classList.add(`focus-${focus}`);
  tab?.classList.toggle("tab-wide", focus !== "notebook");
  $$("[data-nb-focus]").forEach((btn) => {
    const active = btn.dataset.nbFocus === focus;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-pressed", active ? "true" : "false");
  });
  sessionStorage.setItem("mxstudio.notebookFocus", focus);
  requestAnimationFrame(() => relayoutRevealDeck());
  setTimeout(relayoutRevealDeck, 80);
}

function renderSlideCards(cards) {
  if (!Array.isArray(cards) || !cards.length) return "";
  return `<div class="slide-use-grid">${cards
    .map(
      (card, idx) => `
      <article class="slide-use-card ${esc(card.tone || "")} fragment" data-fragment-index="${idx}">
        <div class="slide-use-label">${esc(card.label || "")}</div>
        <p>${esc(card.text || "")}</p>
      </article>`
    )
    .join("")}</div>`;
}

function renderSlideFlow(flow) {
  if (!flow || !Array.isArray(flow.nodes) || !flow.nodes.length) return "";
  const accent = flow.accent || "";
  const nodes = flow.nodes
    .map((node, i) => {
      const mark = node === accent ? " accent" : "";
      const arrow =
        i < flow.nodes.length - 1
          ? '<span class="slide-flow-arrow" aria-hidden="true">→</span>'
          : "";
      return `<span class="slide-flow-node fragment${mark}" data-fragment-index="${i}">${esc(node)}</span>${arrow}`;
    })
    .join("");
  const caption = flow.caption
    ? `<p class="slide-flow-caption fragment">${esc(flow.caption)}</p>`
    : "";
  return `<div class="slide-flow" aria-hidden="true">${nodes}</div>${caption}`;
}

function renderSlideLinks(links) {
  if (!links || typeof links !== "object") return "";
  const entries = Object.entries(links).slice(0, 8);
  if (!entries.length) return "";
  return `<div class="slide-links">${entries
    .map(
      ([label, href], idx) =>
        `<a class="fragment" data-fragment-index="${idx}" href="${String(href).replace(/"/g, "&quot;")}" target="_blank" rel="noopener">${esc(label)} ↗</a>`
    )
    .join("")}</div>`;
}

function renderSlideSteps(steps) {
  if (!Array.isArray(steps) || !steps.length) return "";
  return `<ol class="slide-steps">${steps
    .map(
      (step, idx) => `
      <li class="fragment" data-fragment-index="${idx}">
        <strong>${esc(step.label || `Step ${idx + 1}`)}</strong>
        <span>${esc(step.text || "")}</span>
      </li>`
    )
    .join("")}</ol>`;
}

function renderSlideCode(code, lang = "python") {
  if (!code) return "";
  return `<pre class="slide-code fragment"><code class="language-${esc(lang)}">${esc(code)}</code></pre>`;
}

function renderSlideBullets(bullets, { fragments = false } = {}) {
  if (!Array.isArray(bullets) || !bullets.length) return "";
  return `<ul>${bullets
    .map((b, idx) => {
      const frag = fragments ? ` class="fragment" data-fragment-index="${idx}"` : "";
      return `<li${frag}>${esc(b)}</li>`;
    })
    .join("")}</ul>`;
}

function renderRevealSlide(s) {
  const kind = esc(s.kind || "content");
  const animate = s.animate ? " data-auto-animate" : "";
  const personaIcon =
    s.persona && DRAWER_SVG[s.persona]
      ? `<div class="slide-persona-icon" data-id="persona">${DRAWER_SVG[s.persona]}</div>`
      : "";
  const showBullets =
    Array.isArray(s.bullets) &&
    s.bullets.length &&
    !s.cards &&
    !(s.kind === "sources" && s.links) &&
    !(s.kind === "walkthrough" && s.steps?.length);
  return `
    <section class="slide-kind-${kind}"${animate}>
      <div class="kicker" data-id="kicker">${esc(s.kicker || "")}</div>
      <div class="slide-title-row">
        ${personaIcon}
        <div>
          <h2 data-id="title">${esc(s.title || "")}</h2>
          ${s.body ? `<p data-id="body">${esc(s.body)}</p>` : ""}
        </div>
      </div>
      ${renderSlideCards(s.cards)}
      ${renderSlideFlow(s.flow)}
      ${renderSlideSteps(s.steps)}
      ${renderSlideCode(s.code, s.code_lang || "python")}
      ${showBullets ? renderSlideBullets(s.bullets, { fragments: !!s.fragments }) : ""}
      ${renderSlideLinks(s.links)}
    </section>`;
}

async function openNotebookSlides() {
  if (!openNotebook) {
    toast("Open a notebook first", "Slides need an active lab", "warn");
    return;
  }
  try {
    let data;
    try {
      data = await api(`/api/notebooks/${encodeURIComponent(openNotebook)}/slides`);
    } catch (err) {
      if (!openNotebookModelId) throw err;
      data = await api(`/api/models/${encodeModelPath(openNotebookModelId)}/slides`);
    }
    const host = $("#nb-reveal-slides");
    if (!host) return;
    const deckSlides = data.slides || [];
    if (!deckSlides.length) throw new Error("No slides were generated for this notebook");
    host.classList.remove("reveal-fallback");
    host.innerHTML = deckSlides.map(renderRevealSlide).join("");
    const titleEl = $("#nb-slides-title");
    if (titleEl) titleEl.textContent = data.title || "Model deck";
    $("#nb-slides").hidden = false;
    const layoutControls = $("#nb-layout-controls");
    if (layoutControls) layoutControls.hidden = false;
    setNotebookFocus("slides");
    requestAnimationFrame(() => {
      const started = Date.now();
      const wait = () => {
        if (!window.Reveal) {
          if (Date.now() - started > 5000) {
            toast("Slides unavailable", "Reveal.js did not load. The slide content is still shown as plain sections.", "warn");
            host.classList.add("reveal-fallback");
            return;
          }
          setTimeout(wait, 50);
          return;
        }
        if (revealDeck) {
          try {
            revealDeck.destroy();
          } catch {}
          revealDeck = null;
        }
        const root = document.querySelector(".nb-reveal");
        revealDeck = new Reveal(root, {
          embedded: true,
          hash: false,
          controls: true,
          progress: true,
          center: true,
          width: 1200,
          height: 700,
          margin: 0.05,
          minScale: 0.4,
          maxScale: 2.4,
          transition: "fade",
          backgroundTransition: "fade",
          fragments: true,
          fragmentInURL: false,
          autoAnimate: true,
          autoAnimateDuration: 0.55,
          autoAnimateEasing: "ease",
        });
        Promise.resolve(revealDeck.initialize()).then(() => {
          watchRevealSize();
          relayoutRevealDeck();
          setTimeout(relayoutRevealDeck, 60);
          setTimeout(relayoutRevealDeck, 220);
        });
      };
      wait();
    });
  } catch (err) {
    toast("Slides failed", err.message, "err");
  }
}

function closeNotebookSlides(animate = true) {
  const slides = $("#nb-slides");
  if (slides) slides.hidden = true;
  const layoutControls = $("#nb-layout-controls");
  if (layoutControls) layoutControls.hidden = true;
  setNotebookFocus("notebook");
  if (revealDeck) {
    try {
      revealDeck.destroy();
    } catch {}
    revealDeck = null;
  }
}

function encodeModelPath(modelId) {
  return String(modelId)
    .split("/")
    .map((part) => encodeURIComponent(part))
    .join("/");
}

function renderJpNotebook() {
  const box = $("#nb-cells");
  const prevScroll = box.scrollTop;
  box.innerHTML = "";
  nbCells.forEach((cell, i) => {
    const block = document.createElement("div");
    block.className = "jp-block";

    const wrap = document.createElement("div");
    wrap.className =
      "jp-cell " +
      (cell.cell_type === "code" ? "code" : "md") +
      (i === nbSelected ? " selected" : "") +
      (i === nbSelected && nbMode === "edit" ? " edit" : "");
    wrap.dataset.index = i;

    if (cell.cell_type === "code") {
      const prompt = cell.exec != null ? `In [${cell.exec}]:` : "In [ ]:";
      wrap.innerHTML = `
        <div class="jp-prompt in">${prompt}</div>
        <div class="jp-input-area">
          <textarea class="jp-code" spellcheck="false"></textarea>
        </div>`;
      wrap.querySelector(".jp-code").value = cell.source;
    } else {
      const rendered = window.marked
        ? marked.parse(cell.source || "")
        : `<pre>${esc(cell.source)}</pre>`;
      wrap.innerHTML = `
        <div class="jp-prompt"></div>
        <div class="jp-input-area">
          <div class="jp-md-render">${rendered}</div>
          <textarea class="jp-md-edit" spellcheck="false"></textarea>
        </div>`;
      wrap.querySelector(".jp-md-edit").value = cell.source;
    }

    wrap.addEventListener("click", (ev) => {
      if (ev.target.closest("textarea")) return;
      selectCell(i, "command");
    });
    wrap.addEventListener("dblclick", () => selectCell(i, "edit"));
    block.appendChild(wrap);

    if (cell.cell_type === "code" && cell.output) {
      const outPrompt = cell.exec != null ? `Out[${cell.exec}]:` : "";
      const outRow = document.createElement("div");
      outRow.className =
        "jp-output visible" +
        (cell.output.running ? " running" : cell.output.ok === false ? " err" : "");
      outRow.innerHTML = `
        <div class="jp-prompt out">${outPrompt}</div>
        <div class="jp-output-body"></div>`;
      outRow.querySelector(".jp-output-body").textContent = cell.output.text || "";
      block.appendChild(outRow);
    }

    box.appendChild(block);

    const ta = wrap.querySelector("textarea");
    if (ta) {
      autoGrow(ta);
      ta.addEventListener("input", () => {
        cell.source = ta.value;
        autoGrow(ta);
      });
      ta.addEventListener("focus", () => {
        nbSelected = i;
        nbMode = "edit";
        $$(".jp-cell").forEach((el) => {
          const idx = parseInt(el.dataset.index, 10);
          el.classList.toggle("selected", idx === i);
          el.classList.toggle("edit", idx === i);
        });
      });
      ta.addEventListener("keydown", (ev) => {
        if (ev.key === "Escape") {
          ev.preventDefault();
          ta.blur();
          selectCell(i, "command");
          $("#nb-cells").focus();
        }
        if (ev.key === "Enter" && ev.shiftKey) {
          ev.preventDefault();
          nbSelected = i;
          runSelectedCell(true);
        }
        if (ev.key === "Tab") {
          ev.preventDefault();
          const start = ta.selectionStart;
          ta.value =
            ta.value.slice(0, start) + "    " + ta.value.slice(ta.selectionEnd);
          ta.selectionStart = ta.selectionEnd = start + 4;
          cell.source = ta.value;
        }
      });
    }

    const mdRender = wrap.querySelector(".jp-md-render");
    if (mdRender) {
      mdRender.addEventListener("dblclick", (ev) => {
        ev.stopPropagation();
        selectCell(i, "edit");
        wrap.querySelector(".jp-md-edit")?.focus();
      });
    }
  });
  box.scrollTop = prevScroll;
}

function autoGrow(ta) {
  ta.style.height = "auto";
  ta.style.height = Math.max(48, ta.scrollHeight) + "px";
}

function selectCell(i, mode) {
  // leave edit on previous markdown: re-render
  if (nbMode === "edit" && nbCells[nbSelected]?.cell_type === "markdown") {
    syncSourcesFromDom();
  }
  nbSelected = Math.max(0, Math.min(i, nbCells.length - 1));
  nbMode = mode;
  renderJpNotebook();
  if (mode === "edit") {
    const cellEl = $(`.jp-cell[data-index="${nbSelected}"]`);
    const ta = cellEl?.querySelector("textarea");
    if (ta) {
      ta.focus();
      autoGrow(ta);
    }
  } else {
    $("#nb-cells").focus();
  }
}

function insertCell(type) {
  syncSourcesFromDom();
  const idx = nbSelected + 1;
  nbCells.splice(idx, 0, {
    index: idx,
    cell_type: type,
    source: type === "code" ? "" : "### New cell\n",
    exec: null,
    output: null,
  });
  nbCells.forEach((c, i) => (c.index = i));
  selectCell(idx, "edit");
}

async function saveNotebook() {
  if (!openNotebook) return;
  try {
    await api(`/api/notebooks/${openNotebook}`, {
      method: "PUT",
      body: JSON.stringify({ cells: collectCells() }),
    });
    setKernelStatus("Python 3 | saved", false);
  } catch (err) {
    alert(err.message);
  }
}

function showCellOutput(index, result) {
  const cell = nbCells[index];
  if (!cell || cell.cell_type !== "code") return;
  if (result.skipped) return;
  if (result.ok && cell.exec == null) {
    nbExecCounter += 1;
    cell.exec = nbExecCounter;
  }
  const text = [result.stdout, result.stderr].filter(Boolean).join("") ||
    (result.ok ? "" : "(failed)");
  cell.output = { ok: result.ok, text, running: false };
}

let nbAdvanceAfter = false;

function applyNotebookJobProgress(job) {
  const results = job.results || [];
  const progressKey = JSON.stringify([job.state, job.current_cell, results]);
  if (progressKey === lastNotebookProgressKey) return;
  lastNotebookProgressKey = progressKey;
  const seen = new Set(results.map((r) => r.index));
  for (const r of results) showCellOutput(r.index, r);
  const cur = job.current_cell;
  if (cur != null && nbCells[cur] && nbCells[cur].cell_type === "code" && !seen.has(cur)) {
    nbCells[cur].output = { ok: true, text: "Running in background…", running: true };
  }
  renderJpNotebook();
  setKernelStatus(
    job.state === "running" ? "Python 3 | busy (background)" : "Python 3 | idle",
    job.state === "running"
  );
}

function finishNotebookJob(job, advance) {
  lastNotebookProgressKey = "";
  const results = job.results || [];
  nbCells.forEach((c) => {
    if (c.cell_type === "code" && c.output?.running) c.output = null;
  });
  for (const r of results) showCellOutput(r.index, r);
  renderJpNotebook();
  if (advance && nbAdvanceAfter && results.length) {
    const last = results[results.length - 1].index;
    selectCell(Math.min(last + 1, nbCells.length - 1), "command");
  }
  nbAdvanceAfter = false;
}

async function runSelectedCell(advance = true) {
  if (!openNotebook || nbBusy) return;
  syncSourcesFromDom();
  const cell = nbCells[nbSelected];
  if (!cell) return;

  if (cell.cell_type === "markdown") {
    selectCell(nbSelected, "command");
    if (advance) {
      selectCell(Math.min(nbSelected + 1, nbCells.length - 1), "command");
    }
    return;
  }

  if (serverStatus.state !== "running") {
    toast("Model not ready", 'Serve a model first (pill should say "running")', "err");
    return;
  }

  nbBusy = true;
  nbAdvanceAfter = advance;
  setKernelStatus("Python 3 | busy (background)", true);
  cell.output = { ok: true, text: "Running in background…", running: true };
  renderJpNotebook();
  $("#nb-run-all").disabled = true;
  $("#nb-run").disabled = true;

  try {
    await api(`/api/notebooks/${openNotebook}/run`, {
      method: "POST",
      body: JSON.stringify({
        cell_index: nbSelected,
        code: cell.source,
      }),
    });
    toast("Cell running", `Cell ${nbSelected} — UI stays usable`, "warn");
    pollActivity();
  } catch (err) {
    cell.output = { ok: false, text: err.message, running: false };
    renderJpNotebook();
    nbBusy = false;
    setKernelStatus("Python 3 | idle", false);
    $("#nb-run-all").disabled = false;
    $("#nb-run").disabled = false;
    toast("Could not start cell", err.message, "err");
  }
}

async function runAllCells() {
  if (!openNotebook || nbBusy) return;
  if (serverStatus.state !== "running") {
    toast("Model not ready", 'Serve a model first (pill should say "running")', "err");
    return;
  }
  nbBusy = true;
  nbAdvanceAfter = false;
  setKernelStatus("Python 3 | busy (background)", true);
  nbExecCounter = 0;
  nbCells.forEach((c) => {
    if (c.cell_type === "code") {
      c.exec = null;
      c.output = { ok: true, text: "queued (background)…", running: true };
    }
  });
  renderJpNotebook();
  $("#nb-run-all").disabled = true;
  $("#nb-run").disabled = true;

  try {
    await api(`/api/notebooks/${openNotebook}/run`, {
      method: "POST",
      body: JSON.stringify({
        cell_index: null,
        cells: collectCells(),
      }),
    });
    toast("Notebook running", "All cells — switch tabs freely; watch the activity bar", "warn");
    pollActivity();
  } catch (err) {
    nbBusy = false;
    setKernelStatus("Python 3 | idle", false);
    $("#nb-run-all").disabled = false;
    $("#nb-run").disabled = false;
    toast("Could not start notebook", err.message, "err");
  }
}

// Notebook keyboard shortcuts (command mode)
$("#nb-cells").addEventListener("keydown", (ev) => {
  if (!openNotebook || nbMode === "edit") return;
  if (ev.target.tagName === "TEXTAREA") return;
  const key = ev.key;
  if (key === "Enter" && !ev.shiftKey) {
    ev.preventDefault();
    selectCell(nbSelected, "edit");
  } else if (key === "Enter" && ev.shiftKey) {
    ev.preventDefault();
    runSelectedCell(true);
  } else if (key === "ArrowDown" || key === "j") {
    ev.preventDefault();
    selectCell(Math.min(nbSelected + 1, nbCells.length - 1), "command");
  } else if (key === "ArrowUp" || key === "k") {
    ev.preventDefault();
    selectCell(Math.max(nbSelected - 1, 0), "command");
  } else if (key === "a") {
    ev.preventDefault();
    syncSourcesFromDom();
    nbCells.splice(nbSelected, 0, {
      index: nbSelected,
      cell_type: "code",
      source: "",
      exec: null,
      output: null,
    });
    nbCells.forEach((c, i) => (c.index = i));
    selectCell(nbSelected, "edit");
  } else if (key === "b") {
    ev.preventDefault();
    insertCell("code");
  } else if (key === "dd" || (key === "d" && ev.repeat)) {
    // single d handled below with simple delete on Delete/Backspace with confirm skip — use Delete key
  } else if (key === "Delete" || key === "Backspace") {
    if (nbCells.length <= 1) return;
    ev.preventDefault();
    syncSourcesFromDom();
    nbCells.splice(nbSelected, 1);
    nbCells.forEach((c, i) => (c.index = i));
    selectCell(Math.min(nbSelected, nbCells.length - 1), "command");
  } else if (key === "m") {
    ev.preventDefault();
    nbCells[nbSelected].cell_type = "markdown";
    renderJpNotebook();
  } else if (key === "y") {
    ev.preventDefault();
    nbCells[nbSelected].cell_type = "code";
    renderJpNotebook();
  }
});

/* ---------- benchmark tab ---------- */

function wireBenchInfo() {
  const btn = $("#bench-info-btn");
  const panel = $("#bench-info-panel");
  if (!btn || !panel || btn.dataset.wired) return;
  btn.dataset.wired = "1";
  const close = () => {
    panel.hidden = true;
    btn.setAttribute("aria-expanded", "false");
  };
  btn.addEventListener("click", (ev) => {
    ev.stopPropagation();
    const open = panel.hidden;
    panel.hidden = !open;
    btn.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) loadBenchReadiness();
  });
  document.addEventListener("click", (ev) => {
    if (!panel.hidden && !panel.contains(ev.target) && ev.target !== btn) close();
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") close();
  });
}

async function loadBenchReadiness() {
  wireBenchInfo();
  const el = $("#bench-prereqs");
  const pill = $("#bench-status-pill");
  if (!el) return;
  try {
    const ready = await api("/api/bench/readiness");
    const warns = (ready.warnings || [])
      .map((w) => `<li>${esc(w)}</li>`)
      .join("");
    const prereqs = (ready.prerequisites || [])
      .map((p) => `<li>${esc(p)}</li>`)
      .join("");
    const server = ready.server || {};
    const serving = server.serving
      ? `Serving ${esc(server.model_id || "?")} (${esc(server.state || "running")})`
      : `Not serving (state: ${esc(server.state || "stopped")})`;
    el.className = "bench-prereqs compact " + (ready.ok ? "ok" : "warn");
    el.innerHTML = `
      <div class="prereq-title">Readiness</div>
      <div>${serving}</div>
      ${prereqs ? `<ul>${prereqs}</ul>` : ""}
      ${warns ? `<ul class="warn-list">${warns}</ul>` : ""}`;
    if (pill) {
      pill.textContent = ready.ok ? "Ready" : (server.serving ? "Check info" : "Serve a model");
      pill.className = "bench-status-pill " + (ready.ok ? "ok" : "warn");
    }
  } catch (err) {
    el.className = "bench-prereqs compact warn";
    el.innerHTML = `<div class="prereq-title">Readiness</div>
      <p style="color:var(--err)">Could not load: ${esc(err.message)}</p>`;
    if (pill) {
      pill.textContent = "Unavailable";
      pill.className = "bench-status-pill warn";
    }
  }
}


let chartRunTok, chartRunTtft, chartHistory, chartEngines, chartVramFit;
let chartsResizeBound = false;
let lastBenchHistory = [];
let lastBenchJob = null;

async function loadHardware() {
  if (hardwareLoading) return;
  hardwareLoading = true;
  const strip = $("#hw-strip-body");
  try {
    const hw = await api("/api/hardware");
    lastHwSnapshot = hw;
    if (strip) {
      const bits = [];
      const gpus = hw.gpus || [];
      gpus.forEach((gpu, i) => {
        const used = (gpu.vram_used_mb / 1024).toFixed(1);
        const total = (gpu.vram_total_mb / 1024).toFixed(0);
        const short = String(gpu.name || "GPU")
          .replace(/^NVIDIA GeForce\s+/i, "")
          .replace(/^NVIDIA\s+/i, "");
        const label = gpus.length > 1 ? `GPU ${i} · ${short}` : short;
        const util = gpu.util_pct == null ? "util n/a" : `${gpu.util_pct}%`;
        const temp = gpu.temp_c == null ? "temp n/a" : `${gpu.temp_c}°C`;
        bits.push(
          `<div class="sidebar-hw-row"><strong>${esc(label)}</strong><span class="hw-meta">${used}/${total} GB · ${util} · ${temp}</span></div>`
        );
      });
      if (!gpus.length) {
        const probe = hw.gpu_probe || {};
        const status = probe.ok ? "none detected" : probe.error || "probe retrying";
        bits.push(`<div class="sidebar-hw-row"><strong>GPU</strong><span class="hw-meta" title="${esc(status)}">${esc(status)}</span></div>`);
      }
      bits.push(
        `<div class="sidebar-hw-row"><strong>RAM</strong><span class="hw-meta">${hw.memory?.available_gb ?? "?"} / ${hw.memory?.total_gb ?? "?"} GB free</span></div>`
      );
      bits.push(
        `<div class="sidebar-hw-row"><strong>Disk</strong><span class="hw-meta">${hw.disk?.free_gb ?? "?"} GB free${hw.wsl ? " · WSL2" : ""}</span></div>`
      );
      strip.innerHTML = bits.join("");
    }
  } catch (err) {
    if (strip) {
      strip.innerHTML = `<div class="sidebar-hw-row"><strong>Hardware</strong><span class="hw-meta" title="${esc(err.message)}">connection retrying…</span></div>`;
    }
  } finally {
    hardwareLoading = false;
  }
}

async function refreshBenchData() {
  await renderBenchHistory();
  try {
    const job = await api("/api/bench");
    lastBenchJob = job;
    if (job && job.state !== "idle") {
      renderBenchResult(job);
    } else {
      renderBenchDashboard(job, lastBenchHistory);
    }
  } catch {}
}

function chartTheme() {
  return {
    backgroundColor: "transparent",
    textStyle: { color: cssVar("--text-mid") },
    grid: { left: 48, right: 20, top: 36, bottom: 28 },
  };
}

function refreshChartsForTheme() {
  if (!window.echarts) return;
  [chartRunTok, chartRunTtft, chartHistory, chartEngines, chartVramFit].forEach((chart) => {
    try { chart?.dispose(); } catch {}
  });
  chartRunTok = chartRunTtft = chartHistory = chartEngines = chartVramFit = null;
  if (lastBenchJob || lastBenchHistory.length) {
    renderBenchDashboard(lastBenchJob, lastBenchHistory);
  }
}

/* Compact SVG path symbols for pictorialBar (ECharts path://) */
const SVG = {
  // tortoise silhouette
  tortoise:
    "path://M20,70 C10,70 5,60 12,52 C8,40 20,28 40,30 C55,18 85,22 95,40 C110,42 115,55 105,62 C108,72 95,78 80,75 C70,85 45,85 35,75 C28,80 22,78 20,70 Z",
  // hare / rabbit
  hare:
    "path://M30,85 L28,55 C20,40 25,20 35,25 C38,10 48,8 50,25 C58,12 70,20 62,35 C80,38 95,55 88,75 C95,90 70,95 55,88 C45,98 32,95 30,85 Z",
  // rocket
  rocket:
    "path://M50,5 L65,40 L90,45 L70,60 L75,90 L50,75 L25,90 L30,60 L10,45 L35,40 Z",
  // lightning / spark (TTFT)
  spark:
    "path://M58,5 L30,50 L48,50 L35,95 L75,40 L55,40 Z",
  // hourglass-ish
  hourglass:
    "path://M25,10 L75,10 L55,50 L75,90 L25,90 L45,50 Z",
  // GPU die
  gpu:
    "path://M15,30 H85 V70 H15 Z M25,20 V30 M40,20 V30 M55,20 V30 M70,20 V30 M25,70 V80 M40,70 V80 M55,70 V80 M70,70 V80 M10,40 H15 M10,55 H15 M85,40 H90 M85,55 H90",
  // token chip
  token:
    "path://M50,10 C70,10 85,30 85,50 C85,70 70,90 50,90 C30,90 15,70 15,50 C15,30 30,10 50,10 Z M35,50 H65",
  // flat coin (stacked in pictorialBar)
  coin:
    "path://M50,8 C74,8 92,26 92,50 C92,74 74,92 50,92 C26,92 8,74 8,50 C8,26 26,8 50,8 Z M50,16 C68,16 82,30 82,50 C82,70 68,84 50,84 C32,84 18,70 18,50 C18,30 32,16 50,16 Z M36,50 H64 M50,36 V64",
  // runner warming up
  stretch:
    "path://M45,15 A8,8 0 1,0 45.1,15 M40,30 L55,35 L70,55 L60,60 L50,45 L45,70 L30,85 L25,78 L40,60 L35,45 Z",
};

function speedPersona(tokPerS) {
  const v = Number(tokPerS) || 0;
  if (v >= 40) return { symbol: SVG.rocket, label: "rocket", vibe: "blazing" };
  if (v >= 15) return { symbol: SVG.hare, label: "hare", vibe: "zippy" };
  if (v >= 5) return { symbol: SVG.token, label: "steady", vibe: "cruising" };
  return { symbol: SVG.tortoise, label: "tortoise", vibe: "crawling" };
}

function ttftPersona(ttftS) {
  const v = Number(ttftS) || 0;
  if (v <= 0.15) return { symbol: SVG.spark, label: "spark", vibe: "instant" };
  if (v <= 0.5) return { symbol: SVG.hare, label: "quick", vibe: "snappy" };
  if (v <= 1.5) return { symbol: SVG.token, label: "ok", vibe: "warming up" };
  return { symbol: SVG.hourglass, label: "patient", vibe: "waiting…" };
}

function ensureCharts() {
  if (!window.echarts) return false;
  const bind = (id) => {
    const el = document.getElementById(id);
    if (!el) return null;
    return echarts.getInstanceByDom(el) || echarts.init(el);
  };
  chartRunTok = bind("chart-run-tok");
  chartRunTtft = bind("chart-run-ttft");
  chartHistory = bind("chart-history");
  chartEngines = bind("chart-engines");
  chartVramFit = bind("chart-vram-fit");
  if (!chartsResizeBound) {
    chartsResizeBound = true;
    window.addEventListener("resize", () => {
      chartRunTok?.resize();
      chartRunTtft?.resize();
      chartHistory?.resize();
      chartEngines?.resize();
      chartVramFit?.resize();
    });
  }
  return !!(chartRunTok && chartRunTtft && chartHistory && chartEngines && chartVramFit);
}

function setStory(id, html) {
  const el = document.getElementById(id);
  if (el) el.innerHTML = html;
}

function setBenchView(view) {
  const selected = ["tokens", "latency", "hardware", "engines", "history"].includes(view)
    ? view
    : "tokens";
  $$(".bench-metric-tab").forEach((btn) => {
    const active = btn.dataset.benchView === selected;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-selected", active ? "true" : "false");
  });
  $$("[data-bench-panel]").forEach((panel) => {
    const active = panel.dataset.benchPanel === selected;
    panel.hidden = !active;
    panel.classList.toggle("active", active);
  });
  sessionStorage.setItem("mxstudio.benchView", selected);
  requestAnimationFrame(() => {
    [chartRunTok, chartRunTtft, chartHistory, chartEngines, chartVramFit].forEach((chart) => {
      try { chart?.resize(); } catch {}
    });
  });
}

$$(".bench-metric-tab").forEach((btn) => {
  btn.addEventListener("click", () => setBenchView(btn.dataset.benchView));
});
setBenchView(sessionStorage.getItem("mxstudio.benchView") || "tokens");

function renderBenchDashboard(job, history) {
  const hist = history || lastBenchHistory || [];
  const kpis = $("#bench-kpis");
  const dash = $("#bench-dashboard");
  const hasData = !!(job && (job.runs?.length || job.summary) && job.state !== "idle" && job.state !== "error");
  const hasHist = hist.length > 0;

  if (kpis) {
    if (hasData && job.summary) {
      kpis.hidden = false;
      const s = job.summary;
      const backend = s.backend || job.backend || "—";
      const model = (job.model_id || "").split("/").pop() || "—";
      kpis.innerHTML = `
        <div class="kpi"><div class="k">Throughput</div><div class="v">${s.mean_tok_per_s ?? "—"}</div><div class="s">tok/s mean</div></div>
        <div class="kpi alt"><div class="k">TTFT</div><div class="v">${s.mean_ttft_s ?? "—"}s</div><div class="s">time to first token</div></div>
        <div class="kpi mint"><div class="k">Backend</div><div class="v kpi-sm">${esc(String(backend))}</div><div class="s">${esc(model)}</div></div>
        <div class="kpi violet"><div class="k">Prompts</div><div class="v">${job.num_prompts ?? "—"}</div><div class="s">concurrency ${job.max_concurrency ?? "—"}</div></div>`;
    } else if (!hasHist) {
      kpis.hidden = true;
      kpis.innerHTML = "";
    }
  }

  if (dash) dash.hidden = false;
  const tabs = $("#bench-metric-tabs");
  if (tabs) tabs.hidden = false;

  const paint = () => {
    if (!window.echarts) {
      setTimeout(paint, 50);
      return;
    }
    if (!ensureCharts()) return;

    const palette = {
      accent: cssVar("--accent"),
      accent2: cssVar("--accent-2"),
      text: cssVar("--text"),
      textMid: cssVar("--text-mid"),
      muted: cssVar("--muted"),
      border: cssVar("--border"),
      borderHi: cssVar("--border-hi"),
      ok: cssVar("--ok"),
      err: cssVar("--err"),
    };
    const anim = { animation: true, animationDuration: 700, animationEasing: "cubicOut" };
    const runs = job?.runs || [];
    const meanTok = job?.summary?.mean_tok_per_s;
    const meanTtft = job?.summary?.mean_ttft_s;

    if (hasData && runs.length) {
      const tokValues = runs.map((r) => r.tok_per_s ?? 0);
      const tokMax = Math.max(...tokValues, 1);
      chartRunTok.setOption({
        ...chartTheme(),
        ...anim,
        grid: { left: 48, right: 20, top: 28, bottom: 36 },
        tooltip: { trigger: "axis" },
        xAxis: {
          type: "category",
          data: runs.map((_, i) => `Run ${i + 1}`),
          axisLabel: { color: palette.muted },
          axisLine: { lineStyle: { color: palette.borderHi } },
        },
        yAxis: {
          type: "value",
          name: "tok/s",
          max: Math.ceil(tokMax * 1.25),
          axisLabel: { color: palette.muted },
          splitLine: { lineStyle: { color: palette.border } },
        },
        series: [{
          name: "tok/s",
          type: "bar",
          data: tokValues,
          barWidth: 28,
          itemStyle: {
            borderRadius: [2, 2, 0, 0],
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: palette.accent },
              { offset: 1, color: palette.accent2 || palette.accent },
            ]),
          },
          markLine: meanTok ? {
            silent: true,
            symbol: "none",
            lineStyle: { color: palette.muted, type: "dashed" },
            data: [{ yAxis: meanTok, label: { formatter: `mean ${meanTok}`, color: palette.muted } }],
          } : undefined,
        }],
      }, true);

      const ttftMax = Math.max(...runs.map((r) => r.ttft_s || 0), 0.05);
      chartRunTtft.setOption({
        ...chartTheme(),
        ...anim,
        grid: { left: 52, right: 20, top: 28, bottom: 36 },
        tooltip: { trigger: "axis", valueFormatter: (v) => `${v}s` },
        xAxis: {
          type: "category",
          data: runs.map((_, i) => `Run ${i + 1}`),
          axisLabel: { color: palette.muted },
          axisLine: { lineStyle: { color: palette.borderHi } },
        },
        yAxis: {
          type: "value",
          name: "s",
          max: Math.ceil(ttftMax * 1.35 * 100) / 100,
          axisLabel: { color: palette.muted, formatter: "{value}s" },
          splitLine: { lineStyle: { color: palette.border } },
        },
        series: [{
          name: "TTFT",
          type: "bar",
          data: runs.map((r) => r.ttft_s ?? 0),
          barWidth: 28,
          itemStyle: {
            borderRadius: [2, 2, 0, 0],
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: "#38bdf8" },
              { offset: 1, color: "rgba(56,189,248,0.25)" },
            ]),
          },
          markLine: meanTtft ? {
            silent: true,
            symbol: "none",
            lineStyle: { color: palette.muted, type: "dashed" },
            data: [{ yAxis: meanTtft, label: { formatter: `mean ${meanTtft}s`, color: palette.muted } }],
          } : undefined,
        }],
      }, true);
    } else {
      chartRunTok.clear();
      chartRunTtft.clear();
    }

    const gpus = lastHwSnapshot?.gpus || [];
    const vramGb = gpus.reduce((a, g) => a + (Number(g.vram_total_mb) || 0) / 1024, 0) || 0;
    const modelId = job?.model_id || serverStatus?.model_id;
    const entry = (typeof models !== "undefined" ? models : []).find((m) => m.id === modelId);
    const needGb = Number(entry?.approx_gb) || Number(job?.summary?.approx_gb) || 0;
    const axisMax = Math.max(vramGb, needGb, 1) * 1.15;
    const fits = vramGb > 0 && needGb > 0 ? needGb <= vramGb * 0.95 : null;
    chartVramFit.setOption({
      ...chartTheme(),
      ...anim,
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
      grid: { left: 88, right: 36, top: 24, bottom: 28 },
      xAxis: {
        type: "value",
        max: axisMax,
        axisLabel: { color: palette.muted, formatter: "{value} GB" },
        splitLine: { lineStyle: { color: palette.border } },
      },
      yAxis: {
        type: "category",
        data: ["Model", "GPU VRAM"],
        axisLabel: { color: palette.textMid },
        axisTick: { show: false },
        axisLine: { show: false },
      },
      series: [{
        type: "bar",
        barWidth: 22,
        data: [
          {
            value: needGb || null,
            itemStyle: { color: fits === false ? palette.err : palette.accent, borderRadius: [0, 8, 8, 0] },
            label: { show: true, position: "right", color: palette.textMid, formatter: needGb ? `~${needGb} GB` : "n/a" },
          },
          {
            value: vramGb || null,
            itemStyle: { color: fits === false ? "rgba(248,113,113,0.55)" : palette.ok, borderRadius: [0, 8, 8, 0] },
            label: { show: true, position: "right", color: palette.textMid, formatter: vramGb ? `${vramGb.toFixed(0)} GB` : "n/a" },
          },
        ],
      }],
    }, true);

    const chron = hist.slice();
    const latest = chron.length ? chron[chron.length - 1] : null;
    const maxTok = latest?.summary?.mean_tok_per_s ?? meanTok ?? 0;
    chartEngines.setOption({
      ...chartTheme(),
      ...anim,
      tooltip: { trigger: "axis" },
      grid: { left: 100, right: 48, top: 20, bottom: 24 },
      xAxis: {
        type: "value",
        max: Math.max(maxTok * 1.25, 10),
        axisLabel: { color: palette.muted },
        splitLine: { lineStyle: { color: palette.border } },
      },
      yAxis: {
        type: "category",
        data: ["llama.cpp", "vLLM", "Modular MAX"],
        axisLabel: { color: palette.textMid },
        axisLine: { show: false },
        axisTick: { show: false },
      },
      series: [{
        type: "bar",
        barWidth: 18,
        data: [
          { value: 0, itemStyle: { color: "rgba(107,114,128,0.35)", borderRadius: [0, 8, 8, 0] }, label: { show: true, position: "right", formatter: "soon", color: palette.muted } },
          { value: 0, itemStyle: { color: "rgba(167,139,250,0.35)", borderRadius: [0, 8, 8, 0] }, label: { show: true, position: "right", formatter: "soon", color: palette.muted } },
          {
            value: maxTok || 0,
            itemStyle: {
              borderRadius: [0, 8, 8, 0],
              color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [
                { offset: 0, color: palette.accent2 || palette.accent },
                { offset: 1, color: palette.accent },
              ]),
            },
            label: { show: true, position: "right", formatter: maxTok ? `${maxTok}` : "run bench", color: palette.textMid },
          },
        ],
      }],
    }, true);

    chartHistory.setOption({
      ...chartTheme(),
      ...anim,
      grid: { left: 52, right: 20, top: 28, bottom: 40 },
      tooltip: { trigger: "axis" },
      xAxis: {
        type: "category",
        data: chron.map((h) => (h.ts || "").replace("T", " ").slice(5, 16)),
        axisLabel: { color: palette.muted, hideOverlap: true, fontSize: 10 },
        axisLine: { lineStyle: { color: palette.borderHi } },
      },
      yAxis: {
        type: "value",
        name: "tok/s",
        axisLabel: { color: palette.muted },
        splitLine: { lineStyle: { color: palette.border } },
      },
      series: [{
        name: "tok/s",
        type: "line",
        smooth: true,
        data: chron.map((h) => h.summary?.mean_tok_per_s ?? null),
        showSymbol: true,
        symbolSize: 8,
        lineStyle: { width: 2.5, color: palette.accent },
        itemStyle: { color: palette.accent },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: "rgba(255,77,0,0.22)" },
            { offset: 1, color: "rgba(255,77,0,0)" },
          ]),
        },
      }],
    }, true);
  };
  paint();
}

function renderBenchCharts(job, history) {
  renderBenchDashboard(job, history);
}

function renderBenchResult(job) {
  const el = $("#bench-result");
  if (!el) return;
  lastBenchJob = job;
  if (job.state === "idle") {
    el.innerHTML = "";
    const kpis = $("#bench-kpis");
    if (kpis && !lastBenchHistory.length) kpis.hidden = true;
    renderBenchCharts(job, lastBenchHistory);
    return;
  }
  if (job.state === "error") {
    el.innerHTML = `<p style="color:var(--err)">Benchmark failed: ${esc(job.error || "")}</p>`;
    return;
  }
  const backend = job.summary?.backend || job.backend || "";
  const note = job.summary?.note || job.note || "";
  const fallback = backend === "openai_stream_fallback" || !!note;
  const pill = $("#bench-status-pill");
  if (pill) {
    if (job.state === "running") {
      pill.textContent = "Running…";
      pill.className = "bench-status-pill warn";
    } else if (job.summary) {
      pill.textContent = `${job.summary.mean_tok_per_s ?? "?"} tok/s`;
      pill.className = "bench-status-pill ok";
    }
  }
  const warnBanner = fallback
    ? `<div class="risk-banner warn">${esc(
        note ||
          "Fell back to OpenAI stream timing — max benchmark was unavailable."
      )}</div>`
    : "";
  const rows = (job.runs || [])
    .map(
      (r, i) => `<tr><td>${i + 1}</td><td>${r.ttft_s}s</td><td>${r.tokens}</td><td>${r.tok_per_s}</td></tr>`
    )
    .join("");
  el.innerHTML = `
    ${warnBanner}
    ${
      rows
        ? `<details class="bench-run-details"${job.state === "running" ? " open" : ""}>
            <summary>${job.state === "running" ? "Live runs" : "Run table"}</summary>
            <table class="bench-table">
              <tr><th>Run</th><th>TTFT</th><th>Tokens</th><th>tok/s</th></tr>
              ${rows}
            </table>
          </details>`
        : ""
    }`;
  if (job.runs?.length || job.summary || lastBenchHistory.length) {
    renderBenchCharts(job, lastBenchHistory);
  }
}

async function renderBenchHistory() {
  try {
    const history = await api("/api/bench/history");
    lastBenchHistory = history;
    const host = $("#bench-history");
    if (!host) return;
    host.innerHTML = history.length
      ? `<table class="bench-table">
          <tr><th>When</th><th>Model</th><th>Backend</th><th>tok/s</th><th>TTFT</th><th>GPU</th></tr>
          ${history
            .slice()
            .reverse()
            .map(
              (h) => `<tr><td>${esc(h.ts)}</td><td>${esc(h.model_id)}</td><td>${esc(h.backend || h.summary?.backend || "—")}</td><td>${h.summary.mean_tok_per_s}</td><td>${h.summary.mean_ttft_s}s</td><td>${esc(h.gpu)}</td></tr>`
            )
            .join("")}
        </table>`
      : '<p class="tab-hint">No benchmarks yet.</p>';
    renderBenchDashboard(lastBenchJob, history);
  } catch {}
}

async function startBench(force = false) {
  const btn = $("#bench-btn");
  const num_prompts = parseInt($("#bench-prompts")?.value || "12", 10);
  const max_concurrency = parseInt($("#bench-conc")?.value || "4", 10);
  const max_tokens = parseInt($("#bench-tokens")?.value || "128", 10);
  const payload = {
    runs: 3,
    num_prompts,
    max_concurrency,
    max_tokens,
    use_max_benchmark: true,
    force: !!force,
  };
  const res = await api("/api/bench", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  const jid = jobIdFrom(res);
  toast(
    "max benchmark queued",
    jid || "Modular CLI running in the background — you can leave this tab",
    "warn"
  );
  renderBenchResult({
    state: "running",
    runs: [],
    num_prompts,
    backend: "max_benchmark",
    model_id: serverStatus.model_id,
  });
  if (jid) {
    const job = await waitForJob(jid);
    const result = job.result || (await api("/api/bench").catch(() => null));
    if (result) {
      lastBenchJob = result;
      const degraded = !!result.summary?.degraded;
      toast(
        degraded ? "Benchmark completed with fallback" : "Benchmark finished",
        `${result.summary?.mean_tok_per_s ?? "?"} tok/s · TTFT ${result.summary?.mean_ttft_s ?? "?"}s`,
        degraded ? "warn" : "ok"
      );
      if (activeTab === "bench") {
        renderBenchResult(result);
        await renderBenchHistory();
      }
    } else {
      toast("Benchmark finished", job.detail || jid, "ok");
    }
  }
  pollActivity();
}

$("#bench-btn")?.addEventListener("click", async () => {
  const btn = $("#bench-btn");
  try {
    if (btn) btn.disabled = true;
    await startBench(false);
  } catch (err) {
    if (
      err.status === 409 &&
      err.detail &&
      typeof err.detail === "object" &&
      err.detail.readiness
    ) {
      const ok = confirm(
        (err.detail.message || "Hardware/prerequisites look unsafe for benchmarking") +
          "\n\nRun anyway with force?"
      );
      if (ok) {
        try {
          await startBench(true);
          return;
        } catch (err2) {
          toast("Benchmark could not start", err2.message, "err");
          return;
        }
      }
    }
    toast("Benchmark could not start", err.message, "err");
  } finally {
    if (btn) btn.disabled = false;
  }
});

/* ---------- settings ---------- */

async function loadSettings() {
  try {
    const s = await api("/api/settings");
    const status = $("#settings-token-status");
    if (status) {
      status.textContent = s.hf_token_set
        ? `HF token set · ${s.hf_token_masked || ""} · source ${s.hf_token_source || "?"}`
        : "HF token not set (ok for public models)";
      status.className = "env-pill " + (s.hf_token_set ? "ok" : "warn");
    }
    const hint = $("#settings-path-hint");
    if (hint) {
      hint.textContent = s.secrets_path
        ? `Stored at ${s.secrets_path}`
        : "";
    }
    const notes = $("#settings-notes");
    if (notes) {
      notes.innerHTML = (s.notes || []).map((n) => `<li>${esc(n)}</li>`).join("");
    }
  } catch (err) {
    const status = $("#settings-token-status");
    if (status) {
      status.textContent = "settings load failed: " + err.message;
      status.className = "env-pill warn";
    }
  }
}

$("#settings-save-token")?.addEventListener("click", async () => {
  const input = $("#settings-hf-token");
  const token = (input?.value || "").trim();
  if (!token) {
    toast("Token required", "Paste an hf_… token first", "warn");
    return;
  }
  try {
    await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify({ hf_token: token }),
    });
    if (input) input.value = "";
    toast("Token saved", "Stored under ~/.mxstudio", "ok");
    await loadSettings();
    await loadEnvPill();
  } catch (err) {
    toast("Save failed", err.message, "err");
  }
});

$("#settings-clear-token")?.addEventListener("click", async () => {
  try {
    await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify({ clear_hf_token: true }),
    });
    toast("Token cleared", "", "ok");
    await loadSettings();
    await loadEnvPill();
  } catch (err) {
    toast("Clear failed", err.message, "err");
  }
});

/* ---------- custom architecture wizard ---------- */

const ARCH_STEP_ORDER = [
  "inspect",
  "compare",
  "plan",
  "implement",
  "load",
  "compile",
];
let archStep = "inspect";
let archAnalysis = null;
let archWizardMeta = null;
let archDone = new Set();
let archJobPoll = null;
let archWizardReady = false;
let archPackageApproved = false;

function archDocLinks(docs) {
  docs = docs || {};
  return [
    ["Custom architectures", docs.custom_arch],
    ["Model bring-up", docs.bring_up],
    ["MAX bring-up skills", docs.bring_up_skills],
    ["Logit comparison", docs.logit_comparison],
    ["Handbook", docs.handbook],
    ["MAX models", docs.models],
  ]
    .filter(([, href]) => href)
    .map(
      ([label, href]) =>
        `<a href="${esc(href)}" target="_blank" rel="noopener">${esc(label)}</a>`
    )
    .join(" · ");
}

function renderGapsHtml(gaps) {
  const mark = (status) => {
    if (status === "missing") return "!";
    if (status === "stub") return "~";
    if (status === "scaffolded" || status === "builtin" || status === "done") return "✓";
    return "•";
  };
  return (gaps || [])
    .map(
      (g) => `
      <div class="gap-step ${esc(g.status || "missing")}">
        <div class="mark">${mark(g.status)}</div>
        <div>
          <div class="gtitle">${esc(g.title || g.id || "")}</div>
          <div class="gdetail">${esc(g.detail || "")}</div>
          ${g.file ? `<div class="gfile">${esc(g.file)}</div>` : ""}
          ${g.owner ? `<div class="gfile">owner: ${esc(g.owner)}</div>` : ""}
        </div>
      </div>`
    )
    .join("");
}

function renderArchStepButtons() {
  const host = $("#arch-steps");
  if (!host) return;
  const steps = archWizardMeta?.steps || ARCH_STEP_ORDER.map((id) => ({ id, title: id }));
  const currentIdx = ARCH_STEP_ORDER.indexOf(archStep);
  host.innerHTML = steps
    .map((s, idx) => {
      const id = s.id;
      const cls = [
        "arch-step",
        id === archStep ? "active" : "",
        archDone.has(id) ? "done" : "",
        idx < currentIdx ? "past" : "",
      ]
        .filter(Boolean)
        .join(" ");
      const reachable =
        id === archStep ||
        archDone.has(id) ||
        ARCH_STEP_ORDER.indexOf(id) <= currentIdx;
      return `
        ${idx ? '<span class="arch-step-rule" aria-hidden="true"></span>' : ""}
        <button type="button" class="${cls}" data-arch-step="${esc(id)}" ${
          reachable ? "" : "disabled"
        }>
          <span class="arch-step-num">${String(idx + 1).padStart(2, "0")}</span>
          <span class="arch-step-label">${esc(s.title || id)}</span>
        </button>`;
    })
    .join("");
}

function setArchStep(id) {
  if (!ARCH_STEP_ORDER.includes(id)) return;
  archStep = id;
  $$("#tab-architectures .wizard-pane").forEach((pane) => {
    pane.hidden = pane.dataset.step !== id;
  });
  const meta = (archWizardMeta?.steps || []).find((s) => s.id === id);
  const idx = ARCH_STEP_ORDER.indexOf(id);
  const kicker = $("#arch-stage-kicker");
  const title = $("#arch-stage-title");
  const blurb = $("#arch-step-blurb");
  if (kicker) kicker.textContent = `Step ${String(idx + 1).padStart(2, "0")} / ${String(ARCH_STEP_ORDER.length).padStart(2, "0")}`;
  if (title) title.textContent = meta?.title || id;
  if (blurb) blurb.textContent = meta?.blurb || "";
  renderArchStepButtons();
}

function setArchJobStatus(text, kind = "warn") {
  const el = $("#arch-job-status");
  if (!el) return;
  if (!text) {
    el.hidden = true;
    el.textContent = "";
    el.className = "arch-job-status";
    el.removeAttribute("title");
    return;
  }
  const full = String(text);
  // Keep the chip short; full detail lives in the tooltip.
  let short = full;
  if (full.length > 28) {
    const cut = full.slice(0, 28);
    const sp = cut.lastIndexOf(" ");
    short = (sp > 12 ? cut.slice(0, sp) : cut).trimEnd() + "…";
  }
  el.hidden = false;
  el.className = "arch-job-status " + kind;
  el.title = full;
  el.innerHTML = `<span class="arch-job-dot"></span><span class="arch-job-text">${esc(short)}</span>`;
}

function fillArchLimits(meta) {
  const can = $("#arch-can");
  const cannot = $("#arch-cannot");
  if (can) {
    can.innerHTML = (meta?.limits?.can || []).map((t) => `<li>${esc(t)}</li>`).join("");
  }
  if (cannot) {
    cannot.innerHTML = (meta?.limits?.cannot || [])
      .map((t) => `<li>${esc(t)}</li>`)
      .join("");
  }
}

function fillArchLocalModels(items) {
  const select = $("#arch-local-model");
  if (!select) return;
  const installed = (items || []).filter((model) => model.installed);
  select.innerHTML = [
    '<option value="">Select a downloaded model...</option>',
    ...installed.map((model) => {
      const parked = model.serve_enabled === false;
      const suffix = parked ? " — parked" : model.modular_compatible ? " — MAX native" : "";
      return `<option value="${esc(model.id)}" data-parked="${parked ? "true" : "false"}" data-reason="${esc(model.serve_disabled_reason || "")}">${esc(model.name || model.id)}${suffix}</option>`;
    }),
  ].join("");
  if (!installed.length) {
    select.innerHTML = '<option value="">No downloaded models found</option>';
    select.disabled = true;
  } else {
    select.disabled = false;
  }
}

function fillInspectPane(data) {
  const box = $("#arch-inspect-out");
  if (!box) return;
  if (!data?.ok) {
    box.hidden = false;
    box.innerHTML = `<div class="arch-verdict bad">
        <div class="arch-verdict-k">Inspect failed</div>
        <h3>Could not read this model</h3>
        <p>${esc(data?.error || "analyze failed")}</p>
      </div>
      <p class="tab-hint">${esc(data?.hint || "")}</p>`;
    return;
  }
  const hw = data.hardware || {};
  const ex = data.explain || {};
  const verdict = ex.verdict || (data.modular_compatible ? "supported" : "unsupported");
  const tone =
    verdict === "supported" ? "ok" : verdict === "parked" ? "warn" : "bad";
  const guidance = (ex.guidance || [])
    .map((g) => `<li>${esc(g)}</li>`)
    .join("");
  box.hidden = false;
  box.innerHTML = `
    <div class="arch-verdict ${tone}">
      <div class="arch-verdict-k">Inspect result</div>
      <h3>${esc(ex.verdict_title || (data.modular_compatible ? "Built into MAX" : "Not built into MAX"))}</h3>
      <p>${esc(data.summary || "")}</p>
    </div>
    ${renderArchStageEvidence(data.stage_evidence)}
    <details class="arch-explain" open>
      <summary>What Inspect checked</summary>
      <p>${esc(
        ex.what_inspect_means ||
          "Inspect compares the Hugging Face architecture class to Modular MAX built-ins."
      )}</p>
    </details>
    <details class="arch-explain" open>
      <summary>Why ${data.modular_compatible ? "it is supported" : "it is not supported"}</summary>
      <p>${esc(ex.why || data.summary || "")}</p>
      <div class="row"><span class="k">HF architecture class</span><span><code>${esc(
        data.architecture || "?"
      )}</code></span></div>
      <div class="row"><span class="k">In MAX built-ins?</span><span>${
        data.modular_compatible ? "yes" : "no — that is the blocker"
      }</span></div>
      ${
        ex.similar_hint || data.similar_hint
          ? `<div class="row"><span class="k">Nearby study target</span><span>${esc(
              ex.similar_hint || data.similar_hint
            )}</span></div>`
          : ""
      }
    </details>
    <details class="arch-explain">
      <summary>Facts</summary>
      <div class="row"><span class="k">Model</span><span>${esc(data.model_id || "")}</span></div>
      <div class="row"><span class="k">Approx size</span><span>${
        data.approx_gb != null ? esc(String(data.approx_gb)) + " GB" : "?"
      }</span></div>
      <div class="row"><span class="k">Hardware</span><span>${esc(
        hw.message || hw.level || "?"
      )}</span></div>
      <div class="row"><span class="k">Package path</span><span><code>${esc(
        data.package_path || "ports/…"
      )}</code>${data.scaffold_exists ? " (exists)" : " (not created yet)"}</span></div>
    </details>
    <div class="arch-guidance">
      <div class="fx-kicker">What you should do next</div>
      <ol>${guidance || "<li>Continue to the Gap map for the package checklist.</li>"}</ol>
    </div>`;
}

function renderArchStageEvidence(evidence) {
  if (!evidence) return "";
  const output = String(evidence.stdout || evidence.stderr || "").trim();
  return `<details class="arch-explain" open>
    <summary>Automated evidence</summary>
    <div class="row"><span class="k">Tool</span><span><code>${esc(evidence.tool || "modular/skills/import-model")}</code></span></div>
    <div class="row"><span class="k">Exit</span><span><code>${esc(String(evidence.exit_code ?? "?"))}</code></span></div>
    <div class="row"><span class="k">Artifact</span><span><code>${esc(evidence.artifact || "")}</code></span></div>
    <div class="row"><span class="k">Command</span><span><code>${esc(evidence.command || "")}</code></span></div>
    ${output ? `<pre>${esc(output.slice(0, 4000))}</pre>` : ""}
  </details>`;
}

function fillPlanPane(data) {
  const box = $("#arch-plan-out");
  if (!box) return;
  if (!data?.ok) {
    box.innerHTML = `<div style="color:var(--err)">${esc(data?.error || "no analysis")}</div>`;
    return;
  }
  const plan = data.plan || {};
  const readiness = plan.readiness || {};
  const skills = readiness.skills || {};
  const checks = [
    ["Pixi environment", readiness.pixi?.ready, readiness.pixi?.command || "pixi run"],
    ["MAX command", readiness.max?.ready, readiness.max?.command || "max"],
    ...Object.entries(skills).map(([name, item]) => [name, item.ready, item.path || "optional skill not found"]),
  ];
  const status = plan.approved ? "Approved" : "Draft — approval required";
  box.innerHTML = `
    <div class="arch-verdict ${plan.approved ? "ok" : "warn"}">
      <div class="arch-verdict-k">Bring-up plan · ${esc(status)}</div>
      <h3>${esc(data.architecture || "Custom architecture")}</h3>
      <p>Inspect saved a reviewable plan before any package files are created.</p>
    </div>
    <div class="row"><span class="k">Plan artifact</span><span><code>${esc(plan.path || "results/bringup/…")}</code></span></div>
    <div class="row"><span class="k">Donor to study</span><span>${esc(plan.donor || data.similar_hint || "Choose the nearest MAX architecture")}</span></div>
    <div class="fx-kicker arch-plan-kicker">Environment and agent skills</div>
    <div class="arch-readiness">${checks.map(([label, ready, detail]) => `
      <div class="arch-ready ${ready ? "ok" : "warn"}">
        <span>${ready ? "Ready" : "Missing"}</span><strong>${esc(label)}</strong><small>${esc(detail)}</small>
      </div>`).join("")}</div>
    <div class="fx-kicker arch-plan-kicker">Implementation deltas</div>
    <div class="gap-visual">${renderGapsHtml(plan.deltas || data.gaps)}</div>
    <div class="risk-banner warn">Approval unlocks starter files only. It does not authorize autonomous implementation, prove output correctness, or run expensive evaluation/profile jobs.</div>`;
  const approve = $("#arch-approve-plan");
  const next = $("#arch-continue-implement");
  const scaffold = $("#arch-run-scaffold");
  if (approve) {
    approve.disabled = !!plan.approved;
    approve.textContent = plan.approved ? "Plan approved" : "Approve plan";
  }
  if (next) next.disabled = !plan.approved;
  if (scaffold) scaffold.disabled = !plan.approved;
}

function fillComparePane(data) {
  const box = $("#arch-compare-out");
  if (!box) return;
  if (!data?.ok) {
    box.innerHTML = `<p class="tab-hint">Run Inspect first.</p>`;
    return;
  }
  if (data.modular_compatible) {
    box.innerHTML = `<div class="arch-verdict ok"><div class="arch-verdict-k">Native architecture found</div><h3>No donor port is needed</h3><p>MAX already registers this architecture. Compile the native model instead of creating a custom package.</p></div>`;
    return;
  }
  const plan = data.plan || {};
  const deltas = plan.deltas || data.gaps || [];
  box.innerHTML = `
    <div class="arch-verdict warn">
      <div class="arch-verdict-k">Donor hypothesis</div>
      <h3>${esc(plan.donor || data.similar_hint || "Choose the nearest MAX architecture")}</h3>
      <p>Confirm this donor against Hugging Face modeling code. Similar model names are not enough; graph semantics decide the donor.</p>
    </div>
    <div class="fx-kicker arch-plan-kicker">Semantic delta ledger</div>
    <div class="gap-visual">${renderGapsHtml(deltas)}</div>
    <div class="risk-banner warn">Do not approve an empty or generic delta list. It must cover attention, normalization and residual order, MLP or routing, KV cache, final head, and checkpoint names.</div>`;
}

function renderValidationGate(data, gateId) {
  const gate = (data?.plan?.validation_gates || []).find((item) => item.id === gateId);
  if (!gate) return `<p class="tab-hint">Re-run Inspect to create this evidence gate.</p>`;
  const passed = gate.status === "passed";
  const artifacts = (gate.artifacts || [])
    .map((artifact) => `<li><code>${esc(String(artifact))}</code></li>`)
    .join("");
  return `
    <div class="arch-verdict ${passed ? "ok" : "warn"}">
      <div class="arch-verdict-k">${passed ? "Evidence accepted" : "Evidence required"}</div>
      <h3>${esc(gate.title || gate.id)}</h3>
      <p>${esc(gate.evidence || "")}</p>
    </div>
    ${gate.notes ? `<p>${esc(gate.notes)}</p>` : ""}
    ${artifacts ? `<ul class="arch-checklist">${artifacts}</ul>` : ""}`;
}

async function persistArchGate(gateId, status, artifacts, notes) {
  const result = await api("/api/architectures/gate", {
    method: "PUT",
    body: JSON.stringify({
      model_id: archAnalysis.model_id,
      architecture: archAnalysis.architecture,
      gate_id: gateId,
      status,
      artifacts,
      notes,
    }),
  });
  archAnalysis = { ...archAnalysis, plan: result.plan };
  applyAnalysisPanes(archAnalysis);
  return result.gate;
}

function fillLoadPane(data) {
  const box = $("#arch-load-out");
  if (!box) return;
  if (!data?.ok) {
    box.innerHTML = `<p class="tab-hint">Run Inspect first.</p>`;
    return;
  }
  box.innerHTML = `${renderValidationGate(data, "strict_load")}
    <ol class="arch-runbook">
      <li><div><strong>Inventory checkpoint keys first</strong><span>Record names, shapes, and dtypes without downloading full tensors when possible.</span></div></li>
      <li><div><strong>Diff both directions</strong><span>Checkpoint → graph catches unused weights; graph → checkpoint catches uninitialized slots.</span></div></li>
      <li><div><strong>Explain every exception</strong><span>Tied embeddings and intentionally synthesized values need explicit notes. Silent <code>strict=False</code> does not pass.</span></div></li>
    </ol>
    ${data.plan?.approved ? `<div class="risk-banner warn">This package calls <code>load_state_dict(..., strict=True)</code>. The gate passes only when managed cold compile executes that path without a load error.</div>
      <div class="arch-actions"><button class="btn primary" type="button" data-arch-compile>Run strict load + cold compile</button></div>` : ""}`;
}

function fillImplementPane(data) {
  const box = $("#arch-implement-out");
  if (!box) return;
  if (!data?.ok) {
    box.innerHTML = `<p class="tab-hint">Run Inspect first.</p>`;
    return;
  }
  if (data.modular_compatible) {
    box.innerHTML = `<div class="summary">Built-in MAX support — no custom ports/ edit needed. Serve from the Models tab.</div>`;
    return;
  }
  const implementationDeltas = data.plan?.deltas || data.semantic_deltas ||
    (data.gaps || []).filter((gap) => gap.owner === "human");
  const links = archDocLinks(data.docs);
  box.innerHTML = `
    <div class="arch-plain-intro">
      <strong>The whole idea:</strong> MAX imports your package, reads
      <code>ARCHITECTURES</code>, and chooses the entry whose <code>name</code> exactly
      matches <code>config.json → architectures[0]</code>.
    </div>
    <ol class="arch-file-lessons">
      <li>
        <code>__init__.py</code>
        <strong>Show MAX what to register.</strong>
        <span>Export <code>ARCHITECTURES = [custom_arch]</code>. Keep this file small.</span>
      </li>
      <li>
        <code>arch.py</code>
        <strong>Connect the pieces.</strong>
        <span>Set <code>name="${esc(data.architecture || "ArchitectureForCausalLM")}"</code>, then provide the model class, config, tokenizer, encodings, and weight adapters.</span>
      </li>
      <li>
        <code>model_config.py</code>
        <strong>Translate the checkpoint settings.</strong>
        <span>Read Hugging Face <code>config.json</code> and map every field used by your layers: dimensions, heads, rope, experts, and cache settings.</span>
      </li>
      <li>
        <code>model.py</code>
        <strong>Build the real MAX graph.</strong>
        <span>Implement the layers, KV-cache shapes, and weight-name adapters. Compare outputs with the Hugging Face reference model.</span>
      </li>
    </ol>
    <div class="risk-banner warn">The manifest names the expected implementation targets. Verify implementation checks those exact files and symbols; Strict load and Compile remain separate executable gates.</div>
    ${data.scaffold_exists ? `<div class="arch-actions"><button class="btn primary" type="button" data-open-arch>Open package in Explorer</button></div>` : ""}
    <div class="gap-visual">${renderGapsHtml(implementationDeltas)}</div>
    ${
      data.donor || data.plan?.donor || data.similar_hint
        ? `<div class="row"><span class="k">Study nearby</span><span>${esc(
            data.donor || data.plan?.donor || data.similar_hint
          )}</span></div>`
        : ""
    }
    ${
      data.hf_url
        ? `<div class="row"><span class="k">HF model</span><span><a href="${esc(
            data.hf_url
          )}" target="_blank" rel="noopener">${esc(data.hf_url)}</a></span></div>`
        : ""
    }
    ${links ? `<div class="links-row">${links}</div>` : ""}`;
}

function fillCompilePane(data) {
  const box = $("#arch-compile-out");
  if (!box) return;
  if (!data?.ok) {
    box.innerHTML = `<p class="tab-hint">Run Inspect first.</p>`;
    return;
  }
  const links = archDocLinks(data.docs);
  const ex = data.explain || {};
  if (data.modular_compatible) {
    box.innerHTML = `
      <div class="arch-verdict ok">
        <div class="arch-verdict-k">Compile</div>
        <h3>Ready for Models → Serve</h3>
        <p>${esc(data.summary || "")}</p>
      </div>
      <p class="tab-hint">No custom <code>ports/</code> package needed. Use the Active dropdown or Models tab, then Benchmark when running.</p>
      ${links ? `<div class="links-row">${links}</div>` : ""}`;
    return;
  }
  const parked = data.parked || ex.verdict === "parked";
  box.innerHTML = `
    <div class="arch-verdict ${parked ? "warn" : "bad"}">
      <div class="arch-verdict-k">Compile evidence pending</div>
      <h3>${esc(ex.verdict_title || "Custom architecture still required")}</h3>
      <p>${esc(ex.why || data.summary || "")}</p>
    </div>
    <ol class="arch-runbook">
      <li>
        <div><strong>Start in the mxstudio project folder</strong><span>Run this only after the implementation TODOs are gone.</span></div>
        <pre class="arch-console">${esc(data.serve_cmd || "")}</pre>
        <button class="btn" type="button" data-copy-serve>Copy serve command</button>
      </li>
      <li>
        <div><strong>Wait for MAX</strong><span>Do not test while the graph is still compiling. Continue when the terminal says <code>Server ready on http://0.0.0.0:8000</code>.</span></div>
      </li>
      <li>
        <div><strong>Send one real request</strong><span>The request's <code>model</code> must be the same model id used in the serve command.</span></div>
        <pre class="arch-console">${esc(data.request_cmd || "")}</pre>
        <button class="btn" type="button" data-copy-request>Copy test request</button>
      </li>
      <li>
        <div><strong>Read the first useful error</strong><span>Fix one mismatch, restart MAX, and test again.</span></div>
        <ul class="arch-checklist">
          <li><strong>Architecture not found:</strong> make <code>SupportedArchitecture.name</code> exactly match <code>config.json</code> <code>architectures[0]</code>.</li>
          <li><strong>Unsupported encoding:</strong> add the checkpoint encoding to <code>supported_encodings</code>.</li>
          <li><strong>Weights do not load:</strong> add a weight adapter for the checkpoint format, such as <code>WeightsFormat.safetensors</code>.</li>
          <li><strong>trust_remote_code:</strong> add <code>--trust-remote-code</code> only after reviewing and trusting the repository's Python code.</li>
        </ul>
      </li>
    </ol>
    <div class="arch-actions">
      ${data.scaffold_exists ? `<button class="btn primary" type="button" data-arch-compile>Compile with managed MAX</button><button class="btn" type="button" data-open-arch>Open package</button>` : `<button class="btn" type="button" data-arch-goto="implement">Prepare package</button>`}
    </div>
    ${renderValidationGate(data, "compile")}
    ${renderValidationGate(data, "parity")}
    <details class="arch-explain">
      <summary>Ready to share or upstream?</summary>
      <ul class="arch-checklist">
        <li>Cite the HF repo + paper in your PR / README</li>
        <li>Keep <code>SupportedArchitecture.name</code> == <code>config.json</code> <code>architectures[0]</code></li>
        <li>Prove <code>max serve</code> loads and returns a response, then run Benchmark</li>
        <li>Upstream a working port to Modular when ready</li>
      </ul>
    </details>
    ${links ? `<div class="links-row">${links}</div>` : ""}`;
}

function applyAnalysisPanes(data) {
  renderSimpleArchState(data);
  fillInspectPane(data);
  fillComparePane(data);
  fillPlanPane(data);
  fillImplementPane(data);
  fillLoadPane(data);
  fillCompilePane(data);
}

function archGate(data, gateId) {
  return (data?.plan?.validation_gates || []).find((gate) => gate.id === gateId);
}

function renderSimpleArchChecks(data) {
  const box = $("#arch-simple-checks");
  if (!box) return;
  const report = data.report || {};
  const hardware = data.hardware || report.hardware || {};
  const skill = data.stage_evidence;
  const checks = [
    ["Hugging Face access", "Read repository metadata", true],
    ["Model config", report.config_ok ? `Read ${data.architecture || "architecture"}` : "Could not read config.json", !!report.config_ok],
    ["Checkpoint metadata", report.has_safetensors ? "Safetensors files found" : "No safetensors files found", !!report.has_safetensors],
    ["MAX support", data.modular_compatible ? "Built into MAX" : "Custom package required", !!data.modular_compatible],
    ["Hardware fit", hardware.message || "Hardware estimate unavailable", !["insufficient", "slow"].includes(hardware.level)],
    ["Local package", data.scaffold_exists ? data.package_path : "No package found", !!data.scaffold_exists],
  ];
  if (skill) {
    checks.push([
      "Official Modular Inspect",
      skill.exit_code === 0 ? `Evidence saved to ${skill.artifact}` : `Exited with code ${skill.exit_code}`,
      skill.exit_code === 0,
    ]);
  }
  box.innerHTML = checks.map(([label, detail, positive]) => `
    <div class="arch-simple-check">
      <span class="arch-simple-check-mark ${positive ? "ok" : "note"}">${positive ? "Done" : "Result"}</span>
      <div><strong>${esc(label)}</strong><small>${esc(detail)}</small></div>
    </div>`).join("");
}

function renderSimpleArchAdvanced(data) {
  const box = $("#arch-simple-advanced-content");
  if (!box) return;
  if (data.modular_compatible) {
    box.innerHTML = `<p class="arch-advanced-intro">MAX already provides this graph. No custom package or graph replacement is needed.</p>`;
    return;
  }

  const deltas = data.semantic_deltas || data.plan?.deltas || [];
  const implementationPassed = archGate(data, "implementation")?.status === "passed";
  const targets = [...new Set([
    ...(data.gaps || []).filter((gap) => gap.status !== "missing").map((gap) => gap.file),
    ...deltas.map((delta) => delta.file),
    data.bringup_manifest ? "mxstudio_bringup.json" : null,
  ].filter(Boolean))];
  const targetLabel = implementationPassed ? "Verified source target" : "Manifest target";

  box.innerHTML = `
    <p class="arch-advanced-intro"><strong>MAX itself was not patched.</strong> It loads this package through <code>--custom-architectures</code>. The package builds an OLMoE graph while reusing compatible serving and logits plumbing.</p>
    <div class="arch-advanced-donor">
      <span>Starting point</span>
      <strong>${esc(data.donor || data.similar_hint || "No donor recorded")}</strong>
    </div>
    <div class="fx-kicker arch-advanced-kicker">Graph changes</div>
    <div class="arch-advanced-deltas">
      ${deltas.length ? deltas.map((delta) => `
        <article class="arch-advanced-delta">
          <div>
            <strong>${esc(delta.title || delta.id || "Graph change")}</strong>
            <p>${esc(delta.detail || "")}</p>
          </div>
          <div class="arch-advanced-target">
            <span>${targetLabel}</span>
            <code>${esc(delta.file || "unknown")}${delta.symbol ? `::${esc(delta.symbol)}` : ""}</code>
          </div>
        </article>`).join("") : `<p class="tab-hint">No semantic graph ledger exists yet. Prepare the package, then record each change in <code>mxstudio_bringup.json</code>.</p>`}
    </div>
    <div class="fx-kicker arch-advanced-kicker">Files involved</div>
    <p class="arch-advanced-note">These files are present or named by the package manifest. Inspect does not claim who created them or when.</p>
    <div class="arch-advanced-files">${targets.length ? targets.map((file) => `<code>${esc(file)}</code>`).join("") : "No package files yet."}</div>
    <p class="arch-advanced-boundary">${implementationPassed
      ? "The implementation gate found every manifest file and symbol. Strict load, compile, and numerical parity are separate checks."
      : "These are expected targets, not proof of a working graph. Approve to run source verification; strict load and compile remain separate checks."}</p>`;
}

function renderSimpleArchState(data) {
  const result = $("#arch-simple-result");
  if (!result) return;
  if (!data?.ok) {
    result.hidden = true;
    return;
  }

  result.hidden = false;
  const title = $("#arch-simple-title");
  const summary = $("#arch-simple-summary");
  const state = $("#arch-simple-state");
  const facts = $("#arch-simple-facts");
  const next = $("#arch-simple-next");
  const approve = $("#arch-approve-package");
  const prepare = $("#arch-run-scaffold");
  const serve = $("#arch-serve-package");
  const implementationPassed = archGate(data, "implementation")?.status === "passed";
  const compilePassed = archGate(data, "compile")?.status === "passed";
  const packagePath = data.scaffold_exists ? data.package_path : "No local package";
  const native = !!data.modular_compatible;

  if (title) title.textContent = native ? "Supported by MAX" : data.scaffold_exists ? "Package found" : "A package is needed";
  if (summary) {
    summary.textContent = native
      ? "This model can use MAX directly."
      : data.scaffold_exists
        ? "mxstudio found a local implementation for this unsupported architecture."
        : "MAX does not support this architecture yet, and no local implementation was found.";
  }
  if (state) {
    state.textContent = compilePassed ? "Served" : implementationPassed || native ? "Ready" : data.scaffold_exists ? "Review" : "Not ready";
    state.className = `arch-simple-state ${compilePassed || implementationPassed || native ? "ok" : "warn"}`;
  }
  if (facts) {
    facts.innerHTML = `
      <div><span>Model</span><strong>${esc(data.model_id)}</strong></div>
      <div><span>Architecture</span><strong>${esc(data.architecture || "Unknown")}</strong></div>
      <div><span>Package</span><strong><code>${esc(packagePath)}</code></strong></div>`;
  }
  renderSimpleArchChecks(data);
  renderSimpleArchAdvanced(data);

  if (approve) approve.hidden = native || !data.scaffold_exists || archPackageApproved;
  if (prepare) prepare.hidden = native || data.scaffold_exists;
  if (serve) {
    serve.hidden = !(native || (implementationPassed && archPackageApproved));
    serve.disabled = compilePassed;
    serve.textContent = compilePassed ? "Serving" : "Serve model";
  }
  if (next) {
    next.textContent = compilePassed
      ? "MAX compiled the model, passed health, and returned a generated response."
      : native
        ? "No package review is needed. Serve when you are ready."
        : implementationPassed && archPackageApproved
          ? "Package checks passed. Serving will run strict load, compile, health, and one generated response."
          : data.scaffold_exists
            ? implementationPassed
              ? "Package checks are current. Approve once to unlock Serve."
              : "Approve once. mxstudio will run the official implementation checks, then unlock Serve."
            : "Prepare creates a starter package. The implementation must still be completed before it can be served.";
  }
}

async function initArchWizard() {
  if (!archWizardReady) setArchStep(archStep);
  try {
    const [meta, localModels] = await Promise.all([
      api("/api/architectures/wizard"),
      api("/api/models"),
    ]);
    archWizardMeta = meta;
    fillArchLimits(meta);
    fillArchLocalModels(localModels);
    if (!archWizardReady) {
      archWizardReady = true;
      setArchStep(archStep);
    } else {
      renderArchStepButtons();
      fillArchLimits(meta);
    }
  } catch (err) {
    const blurb = $("#arch-step-blurb");
    if (blurb) blurb.textContent = "The architecture service is unavailable. You can still enter a model; retry when the backend is ready.";
    setArchJobStatus("Service unavailable", "err");
    toast("Wizard meta failed", err.message, "err");
  }
}

async function runArchInspect() {
  const id = ($("#arch-id")?.value || "").trim();
  if (!id) {
    toast("Model id required", "Use org/name format", "warn");
    return;
  }
  const btn = $("#arch-run-inspect");
  try {
    archPackageApproved = false;
    if (btn) btn.disabled = true;
    setArchJobStatus(`Inspecting ${id}…`, "warn");
    const res = await api("/api/architectures/job", {
      method: "POST",
      body: JSON.stringify({ model_id: id, kind: "inspect" }),
    });
    const jid = jobIdFrom(res);
    let data = res.analysis || (!res.queued ? res : null);
    if (res.queued && jid) {
      const job = await waitForJob(jid, {
        onTick: (j) => {
          lastArchState = j.state || "processing";
          setArchJobStatus(`Inspect · ${j.detail || j.state || "working"}`, "warn");
        },
      });
      data = job.result?.analysis || job.result;
      if (data && job.result) data.stage_evidence = job.result;
    }
    archAnalysis = data;
    applyAnalysisPanes(data);
    setArchJobStatus(data?.explain?.verdict_title || (data?.ok ? "Inspect complete" : "Inspect failed"), data?.ok && data?.modular_compatible ? "ok" : data?.ok ? "warn" : "err");
    archDone.add("inspect");
    if (!data?.ok) {
      toast("Analyze failed", data?.error || id, "err");
    }
  } catch (err) {
    toast("Analyze failed", err.message, "err");
  } finally {
    if (btn) btn.disabled = false;
  }
}

function renderScaffoldOut(result) {
  const box = $("#arch-scaffold-out");
  if (!box) return;
  box.hidden = false;
  const files = (result?.files || [])
    .map((f) => `<li><code>${esc(f)}</code></li>`)
    .join("");
  box.innerHTML = `
    <div class="summary">${esc(result?.message || "Package prepared")}</div>
    ${
      result?.package_path
        ? `<div class="row"><span class="k">Package</span><span>${esc(
            result.package_path
          )}</span></div>`
        : ""
    }
    ${files ? `<ul>${files}</ul>` : ""}
    <div class="arch-actions">
      <button class="btn primary" type="button" data-open-arch>Open package in Explorer</button>
    </div>`;
}

function renderSmokeOut(result) {
  const box = $("#arch-smoke-out");
  if (!box) return;
  box.hidden = false;
  const smoke = result?.smoke || {};
  const checks = (smoke.checks || [])
    .map(
      (c) => `
      <div class="gap-step ${c.ok ? "done" : "missing"}">
        <div class="mark">${c.ok ? "✓" : "!"}</div>
        <div>
          <div class="gtitle">${esc(c.id || "check")}</div>
          <div class="gdetail">${esc(c.detail || "")}</div>
        </div>
      </div>`
    )
    .join("");
  box.innerHTML = `
    <div class="summary">${esc(smoke.message || result?.message || "Smoke finished")}</div>
    <div class="risk-banner ${smoke.serve_ready ? "ok" : "warn"}">${
      smoke.serve_ready
        ? "Implementation preflight passed. Strict-load evidence is still required."
        : "Not load-ready yet. Preflight validates files/imports only; checkpoint coverage and MAX compilation remain separate gates."
    }</div>
    ${renderArchStageEvidence(result?.stage_evidence)}
    <div class="gap-visual">${checks || "<p class='tab-hint'>No checks returned.</p>"}</div>`;
}

function applyArchJobResult(job) {
  if (!job || job.state === "error" || job.state === "failed") return;
  const result = job.result;
  const kind = String(job.kind || "").replace(/^arch_/, "");
  if (kind === "scaffold" && result) {
    renderScaffoldOut(result);
    archAnalysis = result;
    applyAnalysisPanes(result);
    renderArchStepButtons();
    setArchJobStatus("Package prepared", "ok");
  } else if (kind === "smoke" && result) {
    renderSmokeOut(result);
    if (result.ok !== false) {
      archAnalysis = { ...archAnalysis, ...result };
      applyAnalysisPanes(archAnalysis);
    }
    renderArchStepButtons();
  } else if (kind === "implement" && result) {
    const checks = (result.implementation_checks || []).map((check) => ({
      id: check.title || check.id,
      ok: check.ok,
      detail: `${check.file}::${check.symbol}`,
    }));
    renderSmokeOut({
      smoke: {
        ok: result.ok,
        serve_ready: result.ok,
        message: result.ok ? "Implementation evidence verified" : "Implementation evidence failed",
        checks,
      },
      stage_evidence: result,
    });
    archAnalysis = result.analysis || archAnalysis;
    applyAnalysisPanes(archAnalysis);
    renderArchStepButtons();
  }
}

async function runArchJob(kind) {
  const id = ($("#arch-id")?.value || "").trim();
  if (!id) {
    toast("Model id required", "Use org/name format", "warn");
    return;
  }
  if (!archAnalysis?.ok || archAnalysis.model_id !== id) {
    toast("Inspect first", "Run Inspect so we know why MAX does or does not support this model.", "warn");
    setArchStep("inspect");
    return;
  }
  if (kind === "scaffold" && archAnalysis.modular_compatible) {
    toast("Custom package not needed", "MAX already supports this architecture.", "ok");
    setArchStep("compile");
    return;
  }
  if ((kind === "smoke" || kind === "implement") && !archAnalysis.scaffold_exists) {
    toast("Package required", "Prepare the package before running implementation preflight.", "warn");
    setArchStep("implement");
    return;
  }
  const btn = kind === "scaffold" ? $("#arch-run-scaffold") : $("#arch-run-smoke");
  if (archJobPoll) {
    clearInterval(archJobPoll);
    archJobPoll = null;
  }
  try {
    if (btn) btn.disabled = true;
    const res = await api("/api/architectures/job", {
      method: "POST",
      body: JSON.stringify({ model_id: id, kind }),
    });
    const jid = jobIdFrom(res);
    setArchJobStatus(
      kind === "scaffold" ? `Preparing ${id}…` : `Verifying implementation · ${id}…`,
      "warn"
    );
    pollActivity();

    if (jid) {
      const job = await waitForJob(jid, {
        onTick: (j) => {
          lastArchState = j.state || "processing";
          const detail = j.detail || j.state || "working";
          setArchJobStatus(
            `${kind === "scaffold" ? "Prepare" : "Verify"} · ${detail}`,
            "warn"
          );
        },
      });
      const okTone = job.result?.ok === false ? "warn" : "ok";
      const doneTitle =
        kind === "scaffold"
          ? job.result?.scaffolded
            ? "Package prepared"
            : "Package preserved"
          : "Implementation verification finished";
      setArchJobStatus(
        job.result?.message || job.result?.smoke?.message || doneTitle,
        okTone
      );
      applyArchJobResult({ ...job, kind });
      if (kind === "scaffold") {
        try {
          const refreshed = await api("/api/architectures/analyze", {
            method: "POST",
            body: JSON.stringify({ model_id: id }),
          });
          const ajid = jobIdFrom(refreshed);
          let analysis = refreshed.analysis || (!refreshed.queued ? refreshed : null);
          if (refreshed.queued && ajid) {
            const ajob = await waitForJob(ajid);
            analysis = ajob.result?.analysis || ajob.result;
          }
          if (analysis) {
            archAnalysis = analysis;
            applyAnalysisPanes(analysis);
          }
        } catch {
          /* keep job result panes */
        }
      }
    }
  } catch (err) {
    toast(
      kind === "scaffold" ? "Package preparation failed" : "Preflight failed",
      err.message,
      "err"
    );
  } finally {
    if (btn) btn.disabled = false;
  }
}

$("#arch-local-model")?.addEventListener("change", (event) => {
  const option = event.target.selectedOptions[0];
  const input = $("#arch-id");
  const note = $("#arch-local-model-note");
  if (input && event.target.value) input.value = event.target.value;
  if (!note) return;
  if (option?.dataset.parked === "true") {
    note.textContent = option.dataset.reason || "This downloaded model is parked and needs custom architecture work.";
  } else if (event.target.value) {
    note.textContent = "Selected from the local Hugging Face cache. Continue, then run Inspect.";
  } else {
    note.textContent = "Choose a local model, or enter any Hugging Face model below.";
  }
});

enhanceSelect($("#model-switcher"));
enhanceSelect($("#arch-local-model"));

$("#arch-run-inspect")?.addEventListener("click", () => {
  runArchInspect();
});

$("#arch-approve-package")?.addEventListener("click", async () => {
  const button = $("#arch-approve-package");
  const id = archAnalysis?.model_id;
  if (!id || !button) return;
  try {
    button.disabled = true;
    if (archGate(archAnalysis, "implementation")?.status === "passed") {
      archPackageApproved = true;
      renderSimpleArchState(archAnalysis);
      setArchJobStatus("Package approved", "ok");
      return;
    }
    setArchJobStatus("Approving package…", "warn");
    if (!archAnalysis?.plan?.approved) {
      const approved = await api("/api/architectures/approve", {
        method: "POST",
        body: JSON.stringify({ model_id: id }),
      });
      archAnalysis = { ...archAnalysis, plan: approved.plan };
    }
    await runArchJob("implement");
    archPackageApproved = archGate(archAnalysis, "implementation")?.status === "passed";
    renderSimpleArchState(archAnalysis);
  } catch (err) {
    toast("Package approval failed", err.message, "err");
  } finally {
    button.disabled = false;
  }
});

$("#arch-approve-plan")?.addEventListener("click", async () => {
  const id = ($("#arch-id")?.value || "").trim();
  const button = $("#arch-approve-plan");
  try {
    if (button) button.disabled = true;
    const result = await api("/api/architectures/approve", {
      method: "POST",
      body: JSON.stringify({ model_id: id }),
    });
    archAnalysis = { ...archAnalysis, plan: result.plan };
    archDone.add("plan");
    fillPlanPane(archAnalysis);
    fillLoadPane(archAnalysis);
    fillCompilePane(archAnalysis);
    renderArchStepButtons();
    setArchJobStatus("Plan approved", "ok");
  } catch (err) {
    if (button) button.disabled = false;
    toast("Approval failed", err.message, "err");
  }
});

$("#arch-run-scaffold")?.addEventListener("click", () => {
  const id = archAnalysis?.model_id;
  if (!id) return;
  const prepare = async () => {
    if (!archAnalysis?.plan?.approved) {
      const approved = await api("/api/architectures/approve", {
        method: "POST",
        body: JSON.stringify({ model_id: id }),
      });
      archAnalysis = { ...archAnalysis, plan: approved.plan };
    }
    await runArchJob("scaffold");
    renderSimpleArchState(archAnalysis);
  };
  prepare().catch((err) => toast("Package preparation failed", err.message, "err"));
});

$("#arch-run-smoke")?.addEventListener("click", () => {
  runArchJob("implement");
});

function canEnterArchStep(id) {
  if (ARCH_STEP_ORDER.indexOf(id) <= ARCH_STEP_ORDER.indexOf(archStep)) return true;
  if (id === "inspect") return true;
  if (id === "compare" || id === "plan") return !!archAnalysis?.ok;
  if (id === "implement") return !!archAnalysis?.plan?.approved;
  if (id === "load" || id === "compile") {
    return !!archAnalysis?.modular_compatible ||
      (!!archAnalysis?.plan?.approved && !!archAnalysis?.scaffold_exists);
  }
  return archDone.has(id);
}

document.addEventListener("click", (ev) => {
  const resetArch = ev.target.closest?.("[data-arch-reset]");
  if (resetArch) {
    archAnalysis = null;
    archPackageApproved = false;
    archDone = new Set();
    applyAnalysisPanes(null);
    setArchJobStatus("");
    const input = $("#arch-id");
    if (input) input.value = "";
    return;
  }
  const compileArch = ev.target.closest?.("[data-arch-compile]");
  if (compileArch) {
    const modelId = archAnalysis?.model_id;
    if (!modelId) return;
    compileArch.disabled = true;
    setArchJobStatus("MAX compile starting…", "warn");
    persistArchGate("compile", "running", [archAnalysis.serve_cmd], "Managed MAX startup is in progress.").catch(() => {});
    startServerWithProgress(modelId, { name: modelId.split("/").pop() })
      .then(async (status) => {
        const artifacts = [
          archAnalysis.serve_cmd,
          `health=passed port=${status.port || 8100}`,
          status.log ? `log=${status.log}` : "",
        ].filter(Boolean);
        await persistArchGate(
          "strict_load",
          "passed",
          [
            "ports/" + archAnalysis.package_name + "/model.py::load_state_dict(strict=True)",
            `managed compile completed on port ${status.port || 8100}`,
            status.log ? `log=${status.log}` : "",
          ].filter(Boolean),
          "Managed cold compile executed strict state-dict loading without checkpoint or graph-weight coverage errors."
        );
        setArchJobStatus("MAX healthy · generating probe…", "warn");
        const generated = await requestGeneratedProbe();
        artifacts.push(`response=${generated.slice(0, 200)}`);
        await persistArchGate(
          "compile",
          "passed",
          artifacts,
          "Cold startup, health, and a real generated response passed. Numerical parity remains a separate gate."
        );
        setArchJobStatus("Compile + first response passed", "ok");
        const box = $("#arch-compile-out");
        if (box) {
          const notice = document.createElement("div");
          notice.className = "risk-banner ok";
          notice.textContent = `Compile, health, and generation passed on port ${status.port || 8100}: ${generated.slice(0, 120)}`;
          box.prepend(notice);
        }
        renderArchStepButtons();
      })
      .catch(async (err) => {
        await persistArchGate("compile", "failed", [archAnalysis.serve_cmd], err.message).catch(() => {});
        setArchJobStatus("MAX compile failed", "err");
        toast("Compile failed", err.message, "err", { persist: true });
      })
      .finally(() => { compileArch.disabled = false; });
    return;
  }
  const openArch = ev.target.closest?.("[data-open-arch]");
  if (openArch) {
    openArchFolder();
    return;
  }
  const copyServe = ev.target.closest?.("[data-copy-serve]");
  if (copyServe) {
    const command = archAnalysis?.serve_cmd || "";
    if (command) {
      if (navigator.clipboard?.writeText) {
        navigator.clipboard
        .writeText(command)
        .then(() => toast("Serve command copied", command, "ok"))
        .catch(() => toast("Copy failed", command, "warn", { persist: true }));
      } else {
        toast("Copy manually", command, "warn", { persist: true });
      }
    }
    return;
  }
  const copyRequest = ev.target.closest?.("[data-copy-request]");
  if (copyRequest) {
    const command = archAnalysis?.request_cmd || "";
    if (command && navigator.clipboard?.writeText) {
      navigator.clipboard
        .writeText(command)
        .then(() => toast("Test request copied", "Run it after MAX reports that the server is ready.", "ok"))
        .catch(() => toast("Copy failed", command, "warn", { persist: true }));
    } else if (command) {
      toast("Copy manually", command, "warn", { persist: true });
    }
    return;
  }
  const gotoArch = ev.target.closest?.("[data-arch-goto]");
  if (gotoArch) {
    const id = gotoArch.getAttribute("data-arch-goto");
    if (id && canEnterArchStep(id)) {
      setArchStep(id);
    } else if (id) {
      toast("Complete the current step", "The next wizard step is not ready yet.", "warn");
    }
    return;
  }
  const stepBtn = ev.target.closest?.("[data-arch-step]");
  if (stepBtn && !stepBtn.disabled) {
    const id = stepBtn.getAttribute("data-arch-step");
    if (id === archStep || archDone.has(id) ||
        ARCH_STEP_ORDER.indexOf(id) <= ARCH_STEP_ORDER.indexOf(archStep)) {
      setArchStep(id);
    }
    return;
  }
  const gotoTab = ev.target.closest?.("[data-goto]");
  if (gotoTab) {
    const tab = gotoTab.getAttribute("data-goto");
    const nav = $(`.nav-btn[data-tab="${tab}"]`);
    if (nav) nav.click();
    return;
  }
  const vizCard = ev.target.closest?.("[data-viz]");
  if (vizCard) {
    const file = vizCard.getAttribute("data-viz");
    if (file) {
      if (vizCard.closest?.("#tab-learn")) {
        drawerForViz(file, vizCard.closest("tr")?.dataset?.vizTitle || file);
      } else {
        openVisualizer(file, vizCard.closest("tr")?.dataset?.vizTitle || vizCard.querySelector(".learn-v")?.textContent || file);
      }
    }
  }
});



/* ---------- Learn tables + detail drawer ---------- */

const REPO = "https://github.com/abhisheksreesaila/mojo-gpu-tutorials";
const REPO_BLOB = REPO + "/blob/main/";

const MODULAR_DOCS = [
  {
    id: "handbook",
    num: "01",
    topic: "The MAX Handbook",
    href: "https://handbook.modular.com/",
    blurb: "Modular's full guide to models, serving and inference.",
  },
  {
    id: "basics",
    num: "02",
    topic: "LLM inference basics",
    href: "https://handbook.modular.com/llm-inference-basics/",
    blurb: "Prefill vs decode, TTFT, tokens — what Chat is really doing.",
  },
  {
    id: "opt",
    num: "03",
    topic: "Inference optimization",
    href: "https://handbook.modular.com/inference-optimization/",
    blurb: "Batching, KV caching, PagedAttention — why tok/s goes up.",
  },
  {
    id: "bench",
    num: "04",
    topic: "max benchmark",
    href: "https://docs.modular.com/serve/benchmark/",
    blurb: "The official benchmark tool behind the Benchmark tab.",
  },
  {
    id: "arch-docs",
    num: "05",
    topic: "Custom architectures docs",
    href: "https://docs.modular.com/develop/serve-custom-model-architectures/",
    blurb: "Official Modular guide for serving your own architecture.",
  },
  {
    id: "arch-wizard",
    num: "06",
    topic: "Architectures wizard",
    action: "architectures",
    blurb: "Step-by-step bring-up in mxstudio: inspect, compare, implement, load, and compile.",
  },
];

const MOJO_TUTORIALS = [
  {
    id: "001",
    topic: "GPU Introduction & Basics",
    video: "https://youtu.be/1ucrzda5TvU",
    viz: "gpu_algo_visualizer.html",
    blurb: "Start here — GPU model of execution and Mojo basics.",
  },
  {
    id: "002",
    topic: "Memory Layout & LayoutTensor",
    video: "https://youtu.be/K89aUepjcig",
    viz: "gpu_layout_visualizer.html",
    blurb: "How tensors sit in memory and why layout matters.",
  },
  {
    id: "003",
    topic: "Vector Addition",
    video: "https://youtu.be/ryD56kuQ5F8",
    blurb: "First real kernel: map threads to elements.",
  },
  {
    id: "004",
    topic: "Thread Management",
    video: "https://youtu.be/RNnsdTHdkSc",
    viz: "gpu_exec_visualizer.html",
    blurb: "Grids, blocks, and indexing without footguns.",
  },
  {
    id: "005",
    topic: "Shared Memory",
    video: "https://youtu.be/SVEnuNtLqjQ",
    blurb: "On-chip collaboration inside a block.",
  },
  {
    id: "006",
    topic: "Pooling Operations",
    video: "https://youtu.be/51OI9vaB5Qo",
    blurb: "Pooling patterns on the GPU.",
  },
  {
    id: "007",
    topic: "Convolution",
    video: "https://youtu.be/7ylxdKHeZg0",
    blurb: "Convolution kernels with practical layouts.",
  },
  {
    id: "008",
    topic: "MAX Graph Introduction",
    video: "https://youtu.be/Az5cKbgUBZ4",
    blurb: "Bridge from Mojo GPU into the MAX graph stack.",
  },
  {
    id: "009",
    topic: "Matrix Multiplication",
    video: "https://youtu.be/W19HynWZMSw",
    blurb: "Matmul as the workhorse GPU kernel.",
  },
  {
    id: "010",
    topic: "Functional Programming in Mojo",
    video: "https://youtu.be/IKFvAQCFMwo",
    blurb: "Functional patterns that stay fast in Mojo.",
  },
  {
    id: "011",
    topic: "Warp-Level Programming",
    video: "https://youtu.be/DS_8Ff0f-0Q",
    viz: "gpu_warp_visualizer.html",
    blurb: "Warp-level primitives without the mysticism.",
  },
  {
    id: "012",
    topic: "MAX Ecosystem",
    video: "https://youtu.be/OwHz-cK2kcI",
    blurb: "How Mojo, MAX, and serving fit together — context for mxstudio.",
  },
  {
    id: "013",
    topic: "Block Level Programming",
    video: "https://youtu.be/mx6F_C3-nLc",
    viz: "gpu_block_sum.html",
    blurb: "Block-level reductions, broadcast, and prefix patterns.",
  },
  {
    id: "016",
    topic: "MXFrame",
    video: null,
    viz: "mxframe_pipeline.html",
    blurb: "MXFrame pipeline sketch — video coming soon.",
  },
];

function chip(kind, href, label) {
  if (!href) return `<span class="learn-chip na">—</span>`;
  // Stay in-app: chips open the side preview (click handler). Pop-out lives in the preview header.
  return `<button type="button" class="learn-chip ${kind}" data-chip-kind="${kind}" data-href="${esc(href)}">${esc(label)}</button>`;
}


/* ---------- Learn in-app preview (split like notebooks) ---------- */

let learnPreviewItem = null;
let learnPreviewMode = null;

function youtubeEmbed(url) {
  if (!url) return null;
  try {
    const u = new URL(url);
    if (u.hostname.includes("youtu.be")) return `https://www.youtube.com/embed/${u.pathname.slice(1)}`;
    const id = u.searchParams.get("v");
    if (id) return `https://www.youtube.com/embed/${id}`;
  } catch {}
  return null;
}

function showLearnPreviewShell(item) {
  const tab = $("#tab-learn");
  const list = $("#learn-list-view");
  const reader = $("#learn-reader");
  if (!reader) return;
  learnPreviewItem = item;
  tab?.classList.add("reading");
  document.body.classList.add("learn-immersive");
  if (list) list.hidden = true;
  reader.hidden = false;
  const title = $("#learn-preview-title");
  const kicker = $("#learn-preview-kicker");
  if (title) title.textContent = item.title || "—";
  if (kicker) kicker.textContent = item.kicker || "Reader";
  $$(".learn-table tbody tr").forEach((tr) => {
    tr.classList.toggle("is-open", tr.dataset.drawerId === item.id);
  });
}

function hideLearnPreview() {
  const tab = $("#tab-learn");
  const list = $("#learn-list-view");
  const reader = $("#learn-reader");
  const frame = $("#learn-preview-frame");
  const fb = $("#learn-preview-fallback");
  learnPreviewItem = null;
  learnPreviewMode = null;
  tab?.classList.remove("reading");
  document.body.classList.remove("learn-immersive");
  if (list) list.hidden = false;
  if (reader) reader.hidden = true;
  if (frame) frame.src = "about:blank";
  if (fb) { fb.hidden = true; fb.innerHTML = ""; }
  $$(".learn-table tbody tr.is-open").forEach((tr) => tr.classList.remove("is-open"));
}

function showLearnFallback(message, href, label) {
  const frame = $("#learn-preview-frame");
  const fb = $("#learn-preview-fallback");
  if (frame) frame.hidden = true;
  if (!fb) return;
  fb.hidden = false;
  fb.innerHTML = `<p>${message}</p>
    <a class="btn primary" href="${esc(href)}" target="_blank" rel="noopener">${esc(label || "Open")}</a>`;
}

function setLearnPreviewMode(mode) {
  const item = learnPreviewItem;
  if (!item) return;
  learnPreviewMode = mode;
  const frame = $("#learn-preview-frame");
  const fb = $("#learn-preview-fallback");
  const pop = $("#learn-preview-pop");
  if (fb) { fb.hidden = true; fb.innerHTML = ""; }
  if (frame) {
    frame.hidden = false;
    frame.src = "about:blank";
  }

  const modes = item.modes || {};
  const entry = modes[mode];
  if (!entry) return;
  if (pop) {
    pop.href = entry.pop || entry.src || "#";
    pop.hidden = !(entry.pop || entry.src);
  }

  if (entry.kind === "iframe") {
    const url = entry.src;
    if (frame) frame.src = url;
  }
}

function buildLearnModes(item) {
  const modes = {};
  if (item.video) {
    const embed = youtubeEmbed(item.video);
    if (embed) modes.youtube = { kind: "iframe", src: embed, pop: item.video };
  }
  if (item.href) {
    modes.docs = { kind: "iframe", src: item.href, pop: item.href };
  }
  if (item.viz) {
    modes.viz = { kind: "iframe", src: "visualize/" + item.viz, pop: "visualize/" + item.viz };
  }
  return modes;
}

function primaryLearnMode(modes, preferred) {
  if (preferred && modes[preferred]) return preferred;
  const order = ["docs", "youtube", "viz"];
  return order.find((k) => modes[k]) || null;
}

function openLearnPreview(item, preferredMode) {
  const modes = buildLearnModes(item);
  item.modes = modes;
  if (item.action && !item.href && !item.video && !item.viz) {
    const nav = $(`.nav-btn[data-tab="${item.action}"]`);
    if (nav) nav.click();
    hideLearnPreview();
    return;
  }
  showLearnPreviewShell(item);
  const initial = primaryLearnMode(modes, preferredMode);
  if (initial) setLearnPreviewMode(initial);
}

function openLearnDrawer(payload) {
  openLearnPreview({
    id: payload.id,
    title: payload.title,
    kicker: payload.kicker,
    ...payload,
  });
}
function closeLearnDrawer() { hideLearnPreview(); }

function drawerForModular(d, preferredMode) {
  openLearnPreview({
    id: d.id,
    title: d.topic,
    kicker: "Modular · docs",
    href: d.href,
    action: d.action,
    blurb: d.blurb,
  }, preferredMode || "docs");
}

function drawerForMine(t, preferredMode) {
  openLearnPreview({
    id: t.id,
    title: t.topic,
    kicker: `mojo-gpu-tutorials · ${t.id}`,
    video: t.video,
    viz: t.viz,
    blurb: t.blurb,
  }, preferredMode || null);
}

function drawerForViz(file, title) {
  const item = (typeof INTERACTIVES !== "undefined" ? INTERACTIVES : []).find((x) => x.file === file);
  openLearnPreview({
    id: "viz-" + file,
    title: title || item?.title || file,
    kicker: "Interactive · HTML",
    viz: file,
    blurb: item?.blurb,
  }, "viz");
}

function renderModularDocsTable() {
  const tb = document.querySelector("#modular-docs-table tbody");
  if (!tb) return;
  tb.innerHTML = MODULAR_DOCS.map((d) => {
    const open = d.href
      ? chip("docs", d.href, "Open")
      : `<button type="button" class="learn-chip open-viz" data-goto="${esc(d.action || "")}">Open</button>`;
    return `<tr data-drawer-id="${esc(d.id)}" data-kind="modular">
      <td class="col-num">${esc(d.num)}</td>
      <td class="col-name"><div class="name-cell"><span class="nm">${esc(d.topic)}</span></div></td>
      <td class="col-status">${open}</td>
    </tr>`;
  }).join("");
}

function renderMineTutorialsTable() {
  const tb = document.querySelector("#mine-tutorials-table tbody");
  if (!tb) return;
  tb.innerHTML = MOJO_TUTORIALS.map((t) => {
    const yt = chip("yt", t.video, "YouTube");
    return `<tr data-drawer-id="${esc(t.id)}" data-kind="mine">
      <td class="col-num">${esc(t.id)}</td>
      <td class="col-name"><div class="name-cell"><span class="nm">${esc(t.topic)}</span></div></td>
      <td class="col-status">${yt}</td>
    </tr>`;
  }).join("");
}

function renderVizTable() {
  const tb = document.querySelector("#viz-grid tbody");
  if (!tb) return;
  const items = (typeof INTERACTIVES !== "undefined" ? INTERACTIVES : []).filter((x) => x.kind === "html");
  tb.innerHTML = items
    .map((item, i) => {
      const num = String(i + 1).padStart(2, "0");
      return `<tr data-drawer-id="viz-${esc(item.file)}" data-kind="viz" data-viz-file="${esc(item.file)}" data-viz-title="${esc(item.title)}">
        <td class="col-num">${num}</td>
        <td class="col-name"><div class="name-cell"><span class="nm">${esc(item.title)}</span></div></td>
        <td class="col-ctx"><span class="ix-kind html">html</span></td>
        <td class="col-status"><button type="button" class="learn-chip open-viz" data-viz="${esc(item.file)}">Open</button></td>
      </tr>`;
    })
    .join("");
}

function setupLearnTables() {
  renderModularDocsTable();
  renderMineTutorialsTable();
  renderVizTable();

  if (document.body.dataset.learnWired === "1") return;
  document.body.dataset.learnWired = "1";

  document.addEventListener("click", (ev) => {
    const dock = ev.target.closest?.("[data-viz-panel]");
    if (dock) {
      const file = dock.getAttribute("data-viz-panel");
      if (file && typeof openVisualizer === "function") {
        openVisualizer(file, learnPreviewItem?.title || file);
      }
      return;
    }
    const modeBtn = ev.target.closest?.("[data-learn-mode]");
    if (modeBtn) {
      setLearnPreviewMode(modeBtn.getAttribute("data-learn-mode"));
      return;
    }
    const chip = ev.target.closest?.("#tab-learn a.learn-chip, #tab-learn button.learn-chip");
    const tr = ev.target.closest?.("#tab-learn tbody tr[data-drawer-id]");
    if (!tr) return;

    const id = tr.dataset.drawerId;
    const kind = tr.dataset.kind;
    let preferred = null;
    if (chip) {
      ev.preventDefault();
      if (chip.classList.contains("yt")) preferred = "youtube";
      else if (chip.classList.contains("docs")) preferred = "docs";
      else if (chip.classList.contains("open-viz") || chip.getAttribute("data-viz")) preferred = "viz";
    }

    const goto = chip?.getAttribute?.("data-goto") || ev.target.closest?.("[data-goto]")?.getAttribute("data-goto");
    if (goto) {
      const nav = document.querySelector(`.nav-btn[data-tab="${goto}"]`);
      if (nav) nav.click();
      return;
    }

    if (kind === "modular") {
      const d = MODULAR_DOCS.find((x) => x.id === id);
      if (d) drawerForModular(d, preferred);
    } else if (kind === "mine") {
      const t = MOJO_TUTORIALS.find((x) => x.id === id);
      if (t) drawerForMine(t, preferred);
    } else if (kind === "viz") {
      drawerForViz(tr.dataset.vizFile, tr.dataset.vizTitle);
    }
  });

  $("#learn-preview-close")?.addEventListener("click", hideLearnPreview);
  $("#learn-reader-back")?.addEventListener("click", hideLearnPreview);
  addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") hideLearnPreview();
  });

}


/* ---------- interactive explainers catalog ---------- */

const INTERACTIVES = [
  {
    kind: "html",
    title: "GPU algorithms",
    file: "gpu_algo_visualizer.html",
    blurb: "Walk common GPU algorithm shapes — threads, tiles, and work distribution.",
  },
  {
    kind: "html",
    title: "GPU execution",
    file: "gpu_exec_visualizer.html",
    blurb: "How warps and blocks actually march through a kernel.",
  },
  {
    kind: "html",
    title: "Memory layout",
    file: "gpu_layout_visualizer.html",
    blurb: "Layouts and LayoutTensor intuition for Mojo GPU code.",
  },
  {
    kind: "html",
    title: "Warps",
    file: "gpu_warp_visualizer.html",
    blurb: "Warp-level cooperation without the mysticism.",
  },
  {
    kind: "html",
    title: "Block sum",
    file: "gpu_block_sum.html",
    blurb: "Reduction patterns inside a block.",
  },
  {
    kind: "html",
    title: "Block broadcast",
    file: "gpu_block_broadcast.html",
    blurb: "Sharing a value across a block cleanly.",
  },
  {
    kind: "html",
    title: "Block prefix",
    file: "gpu_block_prefix.html",
    blurb: "Prefix / scan building blocks on-GPU.",
  },
  {
    kind: "html",
    title: "MXFrame pipeline",
    file: "mxframe_pipeline.html",
    blurb: "Pipeline sketch tying frames to MAX-shaped serving.",
  },
  {
    kind: "html",
    title: "OLMoE → MAX bring-up",
    file: "olmoe_bringup.html",
    blurb: "See what an unsupported checkpoint lacks, what the port adds, and what MAX provides.",
  },
  {
    kind: "reveal",
    title: "Notebook Reveal labs",
    action: "notebooks",
    blurb: "Generated labs ship with Reveal.js story slides — persona, fit, and code walkthrough beside the notebook.",
  },
  {
    kind: "studio",
    title: "Architectures wizard",
    action: "architectures",
    blurb: "Inspect, compare, implement, strict-load, and compile with explicit evidence gates.",
  },
];

function renderInteractives(host, { htmlOnly = false } = {}) {
  if (!host) return;
  const items = htmlOnly ? INTERACTIVES.filter((x) => x.kind === "html") : INTERACTIVES;
  host.innerHTML = items
    .map((item) => {
      const kind = esc(item.kind);
      const title = esc(item.title);
      const blurb = esc(item.blurb || "");
      let row;
      if (item.kind === "html" && item.file) {
        row = `<button type="button" class="learn-row learn-row-btn viz-card" data-viz="${esc(item.file)}">
            <span class="ix-kind html">${kind}</span>
            <span class="learn-v">${title}</span>
            <span class="learn-file">open</span>
          </button>`;
      } else {
        row = `<button type="button" class="learn-row learn-row-btn" data-goto="${esc(item.action || "")}">
            <span class="ix-kind ${kind}">${kind}</span>
            <span class="learn-v">${title}</span>
            <span class="learn-file">go</span>
          </button>`;
      }
      return `<div class="learn-item" role="listitem">${row}
          <details class="learn-more"><summary>Details</summary><p>${blurb}</p></details>
        </div>`;
    })
    .join("");
}

function mountInteractives() {
  setupLearnTables();
}

mountInteractives();

/* ---------- Learn · Mojo GPU visualizers ---------- */

const VIZ_LOCAL = "visualize/";
const VIZ_GH =
  "https://github.com/abhisheksreesaila/mojo-gpu-tutorials/blob/main/visualize/";

function openVisualizer(file, title) {
  const panel = $("#viz-panel");
  const frame = $("#viz-frame");
  if (!panel || !frame) return;
  const url = VIZ_LOCAL + encodeURIComponent(file).replace(/%2F/g, "/");
  frame.src = url;
  frame.title = `${title || file} interactive visualizer`;
  const titleEl = $("#viz-panel-title");
  if (titleEl) titleEl.textContent = title || file;
  const pop = $("#viz-popout");
  if (pop) pop.href = url;
  const gh = $("#viz-github");
  if (gh) gh.href = VIZ_GH + file;
  panel.hidden = false;
  $$("[data-viz]").forEach((card) => {
    const active = card.getAttribute("data-viz") === file;
    card.setAttribute("aria-expanded", active ? "true" : "false");
    card.setAttribute("aria-controls", "viz-panel");
  });
  $("#tab-learn")?.classList.add("tab-wide");
  panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function closeVisualizer() {
  const panel = $("#viz-panel");
  const frame = $("#viz-frame");
  if (frame) frame.src = "about:blank";
  if (panel) panel.hidden = true;
  $$("[data-viz]").forEach((card) => card.setAttribute("aria-expanded", "false"));
  $("#tab-learn")?.classList.remove("tab-wide");
}

$("#viz-close")?.addEventListener("click", () => closeVisualizer());
