// MX Studio console. Plain JS; talks to the daemon's /admin routes on the same origin.
// Every piece of data (model names, logs) goes in as text nodes, never as HTML.
(() => {
  "use strict";
  const ADMIN = { "X-Mst-Admin": "1" };
  const POLL_MS = 3000;
  const KEEP_ALIVE = [[90, "90 seconds"], [300, "5 minutes (default)"], [600, "10 minutes"], [1800, "30 minutes"], [3600, "1 hour"]];
  const STATES = { ready: "Ready", starting: "Starting", cold: "Cold", crashed: "Crashed" };
  const MENU = [
    { label: "", items: [["overview", "Overview"], ["activity", "Activity log"]] },
    { label: "Models", items: [["models", "Models"], ["pull", "Pull a model"], ["ports", "Ports"]] },
    { label: "Access", items: [["keys", "API keys"], ["network", "Network (expose)"]] },
    { label: "Monitoring", items: [["metrics", "Metrics"], ["benchmarks", "Benchmarks"], ["logs", "Logs"]] },
    { label: "Settings", items: [["gateway", "Gateway (LiteLLM)"], ["daemon", "Daemon"]] },
  ];
  const SOON = {
    overview: ["Overview", "Gauges for throughput, time to first token, GPU memory and requests are coming next (F-013).", "mst ls"],
    activity: ["Activity log", "Every request with its app, model, tokens and latency is coming with F-013.", "tail -f ~/.mst/logs/daemon.log"],
    pull: ["Pull a model", "Pulling from the console is coming. For now, from a terminal:", "mst pull qwen2.5-0.5b"],
    ports: ["Ports", "Porting an unsupported architecture starts from a terminal:", "mst port <hugging-face-repo>"],
    keys: ["API keys", "Managing keys here is coming with F-014. For now:", "mst keys ls · mst keys new <app> · mst keys revoke <app>"],
    network: ["Network (expose)", "The LAN endpoint page is coming with F-014. For now:", "mst expose · mst expose --stop"],
    metrics: ["Metrics", "Charts per model are coming after the Overview gauges.", "mst ls"],
    benchmarks: ["Benchmarks", "Prefill, decode and time-to-first-token comparisons are planned for v2.", ""],
    logs: ["Logs", "Each model's log is on its Logs tab (Models › a model › Logs). Files live in:", "~/.mst/logs/"],
    gateway: ["Gateway (LiteLLM)", "LiteLLM routes and health will show here. Its own UI runs on this machine only:", "http://127.0.0.1:4000/ui"],
    daemon: ["Daemon", "Daemon settings will show here. Restart it from a terminal:", "mst stop --all && mst serve <model>"],
  };

  const state = {
    page: (location.hash || "#models").slice(1),
    models: null, // null = still loading
    error: null,
    filter: "",
    menuFilter: "",
    stateFilter: "all",
    selected: null,
    tab: "overview",
    draft: null, // unsaved settings for the selected model
    busy: false,
    note: "",
    log: "",
  };

  // -- tiny DOM helper ------------------------------------------------------------------------
  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === false || v === null || v === undefined) continue;
      if (k.startsWith("on")) el.addEventListener(k.slice(2).toLowerCase(), v);
      else if (k === "class") el.className = v;
      else if (k === "style") el.style.cssText = v; // CSSOM, which the console's CSP allows (style="" attributes it blocks)
      else el.setAttribute(k, v === true ? "" : String(v));
    }
    for (const c of children.flat()) {
      if (c === null || c === undefined || c === false) continue;
      el.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return el;
  }
  const $ = (id) => document.getElementById(id);

  // -- daemon calls ---------------------------------------------------------------------------
  async function api(method, path, body) {
    const res = await fetch(path, {
      method, headers: { ...ADMIN, ...(body ? { "Content-Type": "application/json" } : {}) },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      let message = `HTTP ${res.status}`;
      try { message = (await res.json()).error.message; } catch (_) { /* keep the status */ }
      throw new Error(message);
    }
    const type = res.headers.get("content-type") || "";
    return type.includes("json") ? res.json() : res.text();
  }

  async function refresh(fromPoll) {
    try {
      state.models = await api("GET", "/admin/models");
      state.error = null;
    } catch (e) {
      state.error = e;
    }
    render(fromPoll === true);
  }

  async function loadInfo() {
    try {
      const info = await api("GET", "/admin/info");
      $("host").textContent = `mst@${info.host}`;
      $("crumb-host").textContent = `mst@${info.host}`;
      $("title-host").textContent = `mst@${info.host}`;
    } catch (_) { /* the banner covers a dead daemon */ }
  }

  async function loadLog(name) {
    try { state.log = await api("GET", `/admin/models/${encodeURIComponent(name)}/log`); }
    catch (e) { state.log = `Could not read the log: ${e.message}`; }
    render();
  }

  async function act(name, verb) {
    state.busy = true;
    state.note = verb === "load" ? `Loading ${name}… the first load compiles the model.` : `Stopping ${name}…`;
    render();
    try {
      await api("POST", `/admin/models/${encodeURIComponent(name)}/${verb}`);
      state.note = verb === "load" ? `${name} is ready.` : `${name} is unloaded.`;
    } catch (e) {
      state.note = `${verb === "load" ? "Load" : "Stop"} failed: ${e.message}`;
      if (verb === "load") { state.tab = "logs"; loadLog(name); }
    }
    state.busy = false;
    await refresh();
  }

  async function applySettings() {
    const m = selectedModel();
    if (!m || !state.draft) return;
    state.busy = true; render();
    try {
      await api("PATCH", `/admin/models/${encodeURIComponent(m.name)}`, state.draft);
      state.draft = null;
      state.note = "Saved.";
    } catch (e) {
      state.note = `Not saved: ${e.message}`;
    }
    state.busy = false;
    await refresh();
  }

  // -- view helpers ---------------------------------------------------------------------------
  const selectedModel = () => (state.models || []).find((m) => m.name === state.selected) || null;
  const effective = (m) => ({ pinned: m.pinned, keep_alive: m.keep_alive, ...(state.draft || {}) });

  function lifecycle(m) {
    if (m.state === "starting" && m.starting_seconds !== null) return `Starting for ${duration(Math.round(m.starting_seconds))}`;
    if (m.pinned) return "Pinned";
    const ka = duration(m.keep_alive);
    if (m.state === "ready" && m.idle_seconds !== null) return `Idle ${duration(Math.round(m.idle_seconds))} of ${ka}`;
    return `Unload after ${ka}`;
  }
  function duration(s) {
    if (s % 3600 === 0 && s) return `${s / 3600}h`;
    if (s % 60 === 0 && s) return `${s / 60}m`;
    if (s >= 60) return `${Math.floor(s / 60)}m ${s % 60}s`;
    return `${s}s`;
  }
  // While the daemon is down the last known states would be stale: say Unknown instead.
  function badge(st) {
    if (state.error) return h("span", { class: "badge" }, h("span", { class: "dot cold" }), "Unknown");
    return h("span", { class: "badge" }, h("span", { class: `dot ${st}` }), STATES[st] || st);
  }
  function cliFor(m) {
    const e = effective(m);
    return e.pinned ? `mst serve ${m.name} --pin` : `mst serve ${m.name} --keep-alive ${duration(e.keep_alive)}`;
  }

  function select(name) {
    if (state.selected !== name) { state.draft = null; state.note = ""; state.log = ""; }
    const changed = state.selected !== name;
    state.selected = name;
    const m = selectedModel();
    if (m && m.state === "crashed") state.tab = "logs";
    else if (changed) state.tab = "overview";
    if (state.tab === "logs") loadLog(name);
    render();
  }
  function closeBlade() { state.selected = null; state.draft = null; state.note = ""; render(); }

  // -- render ---------------------------------------------------------------------------------
  function renderMenu() {
    const menu = $("menu");
    const focused = document.activeElement && document.activeElement.id === "menu-filter";
    const q = state.menuFilter.toLowerCase();
    const groups = MENU.map((g) => ({ ...g, items: g.items.filter(([, label]) => !q || label.toLowerCase().includes(q)) }))
      .filter((g) => g.items.length);
    menu.replaceChildren(
      h("label", { class: "menu-filter" }, h("span", { class: "sr-only" }, "Filter menu"),
        h("input", { id: "menu-filter", type: "search", placeholder: "Search (Ctrl+/)", value: state.menuFilter,
          onInput: (e) => { state.menuFilter = e.target.value; renderMenu(); } })),
      ...groups.map((g) => h("div", { class: "menu-group" },
        g.label ? h("div", { class: "menu-label" }, g.label) : null,
        g.items.map(([id, label]) => h("button", {
          class: "menu-item", type: "button", "aria-current": state.page === id ? "page" : false,
          onClick: () => { location.hash = id; closeMenu(); },
        }, label)))));
    if (focused) { const input = $("menu-filter"); input.focus(); input.setSelectionRange(input.value.length, input.value.length); }
  }
  function closeMenu() {
    $("menu").classList.remove("open");
    document.querySelector("[data-action=toggle-menu]").setAttribute("aria-expanded", "false");
  }

  function renderModels() {
    const q = state.filter.toLowerCase();
    const rows = (state.models || []).filter((m) =>
      (state.stateFilter === "all" || m.state === state.stateFilter) &&
      (!q || [m.name, m.repo_id, m.source, m.architecture].some((v) => v.toLowerCase().includes(q))));
    const sel = selectedModel();
    const commands = h("div", { class: "commands", role: "toolbar", "aria-label": "Model commands" },
      h("button", { class: "cmd primary", type: "button", onClick: () => { location.hash = "pull"; } }, "+ Serve model"),
      h("span", { class: "cmd-sep" }),
      h("button", { class: "cmd", type: "button", disabled: !sel || state.busy || !!state.error, onClick: () => act(sel.name, "load") }, "Load"),
      h("button", { class: "cmd", type: "button", disabled: !sel || state.busy || !!state.error || sel.state === "cold", onClick: () => act(sel.name, "stop") }, "Stop"),
      h("button", { class: "cmd", type: "button", onClick: refresh }, "Refresh"));

    if (state.models === null) {
      return [commands, h("table", { class: "grid" }, h("tbody", {},
        [1, 2, 3].map(() => h("tr", { class: "skeleton" }, [1, 2, 3, 4].map(() => h("td", {}, h("span")))))))];
    }
    if (state.models.length === 0) {
      return [commands, h("div", { class: "empty" },
        h("h2", {}, "No models yet"),
        h("p", {}, "Serve your first model from a terminal. It shows up here as soon as it is registered."),
        h("div", { class: "code" }, "mst serve qwen2.5-0.5b"))];
    }
    const nextState = { all: "ready", ready: "cold", cold: "starting", starting: "crashed", crashed: "all" };
    const filters = h("div", { class: "filters" },
      h("label", {}, h("span", { class: "sr-only" }, "Filter models"),
        h("input", { type: "search", placeholder: "Filter for any field...", value: state.filter,
          onInput: (e) => { state.filter = e.target.value; renderView(true); } })),
      h("button", { class: "chip", type: "button", onClick: () => { state.stateFilter = nextState[state.stateFilter]; render(); } },
        `State == ${state.stateFilter}`),
      h("span", { class: "count" }, `Showing ${rows.length} of ${state.models.length} models`));
    const table = h("table", { class: state.error ? "grid stale" : "grid" },
      h("thead", {}, h("tr", {}, ["Name", "State", "Source", "Lifecycle"].map((t) => h("th", {}, t)),
        h("th", { class: "col-wide" }, "Architecture"), h("th", { class: "col-wide" }, "Revision"))),
      h("tbody", {}, rows.map((m) => h("tr", { "aria-selected": m.name === state.selected ? "true" : "false", onClick: () => select(m.name) },
        h("td", {}, h("button", { class: "name", type: "button", title: m.name }, m.name)),
        h("td", {}, badge(m.state)),
        h("td", { class: "mono" }, m.source),
        h("td", {}, lifecycle(m)),
        h("td", { class: "mono col-wide" }, m.architecture),
        h("td", { class: "mono col-wide" }, m.revision.slice(0, 8))))));
    return [commands, filters, table];
  }

  function renderSoon(id) {
    const [title, text, cli] = SOON[id] || ["Not found", "This page doesn't exist.", ""];
    $("title").textContent = title;
    return [h("div", { class: "soon" }, h("h2", {}, "Coming soon"), h("p", {}, text), cli ? h("div", { class: "code" }, cli) : null)];
  }

  function renderView(keepFocus) {
    const view = $("view");
    const focused = keepFocus && document.activeElement && document.activeElement.type === "search" && view.contains(document.activeElement);
    if (state.page === "models") $("title").textContent = "Models";
    view.replaceChildren(...(state.page === "models" ? renderModels() : renderSoon(state.page)));
    if (focused) { const input = view.querySelector("input[type=search]"); input.focus(); input.setSelectionRange(input.value.length, input.value.length); }
  }

  function renderBanner() {
    const banner = $("banner");
    banner.hidden = !state.error;
    if (state.error) banner.replaceChildren(
      h("strong", {}, "The mst daemon isn't answering. "),
      "Start it with ", h("span", { class: "mono" }, "mst serve <model>"), " or ", h("span", { class: "mono" }, "mst daemon"),
      ". This page retries every few seconds.");
  }

  function renderBlade() {
    const blade = $("blade");
    const m = state.page === "models" ? selectedModel() : null;
    blade.hidden = !m;
    document.querySelector(".layout").classList.toggle("blade-open", !!m);
    if (!m) { blade.replaceChildren(); return; }
    const e = effective(m);
    const tabs = [["overview", "Overview"], ["settings", "Settings"], ["logs", "Logs"]];
    let body;
    if (state.tab === "settings") {
      body = h("form", { class: "form", onSubmit: (ev) => { ev.preventDefault(); applySettings(); } },
        h("div", { class: "switch-row" },
          h("button", { class: "switch", type: "button", role: "switch", "aria-checked": e.pinned ? "true" : "false", "aria-labelledby": "pin-label",
            onClick: () => { state.draft = { ...(state.draft || {}), pinned: !e.pinned }; render(); } }),
          h("div", {}, h("div", { id: "pin-label", style: "font-weight:600" }, "Keep loaded (pin)"),
            h("div", { class: "help", style: "margin-top:0" }, "A pinned model never unloads when idle."))),
        h("div", { class: "field" },
          h("label", { for: "keep-alive" }, "Unload after idle"),
          h("select", { id: "keep-alive", disabled: e.pinned, onChange: (ev) => { state.draft = { ...(state.draft || {}), keep_alive: Number(ev.target.value) }; render(); } },
            (KEEP_ALIVE.some(([s]) => s === e.keep_alive) ? KEEP_ALIVE : [...KEEP_ALIVE, [e.keep_alive, duration(e.keep_alive)]])
              .map(([s, label]) => h("option", { value: s, selected: s === e.keep_alive }, label))),
          h("div", { class: "help" }, "Like Ollama's keep_alive. The next request after an unload waits for a cold start.")),
        h("div", { class: "field" }, h("label", {}, "The same from a terminal"), h("div", { class: "code" }, cliFor(m))));
    } else if (state.tab === "logs") {
      body = [h("pre", { class: "log" }, state.log || "No log yet. It appears once the model has been started."),
        h("p", { class: "help" }, `~/.mst/logs/${m.name}.log (last 8 KB)`)];
    } else {
      body = h("dl", { class: "facts" },
        h("dt", {}, "State"), h("dd", {}, badge(m.state)),
        h("dt", {}, "Repository"), h("dd", { class: "mono" }, m.repo_id),
        h("dt", {}, "Revision"), h("dd", { class: "mono" }, m.revision),
        h("dt", {}, "Architecture"), h("dd", { class: "mono" }, m.architecture),
        h("dt", {}, "Source"), h("dd", { class: "mono" }, m.source),
        h("dt", {}, "Served as"), h("dd", { class: "mono" }, m.name),
        h("dt", {}, "Lifecycle"), h("dd", {}, lifecycle(m)),
        h("dt", {}, "Requests now"), h("dd", { class: "mono" }, String(m.in_flight)));
    }
    blade.replaceChildren(
      h("div", { class: "blade-head" },
        h("div", { class: "blade-title" },
          h("div", {}, h("h2", {}, m.name), h("div", { class: "sub" }, m.repo_id)),
          h("button", { class: "close", type: "button", "aria-label": "Close details", onClick: closeBlade },
            svg("M3 3 L13 13 M13 3 L3 13"))),
        h("div", { class: "commands" },
          h("button", { class: "cmd", type: "button", disabled: state.busy || !!state.error, onClick: () => act(m.name, "load") }, "Load"),
          h("button", { class: "cmd", type: "button", disabled: state.busy || !!state.error || m.state === "cold", onClick: () => act(m.name, "stop") }, "Stop")),
        h("div", { class: "tabs", role: "tablist" }, tabs.map(([id, label]) => h("button", {
          class: "tab", type: "button", role: "tab", "aria-selected": state.tab === id ? "true" : "false",
          onClick: () => { state.tab = id; if (id === "logs") loadLog(m.name); render(); } }, label)))),
      h("div", { class: "blade-body" }, body),
      h("div", { class: "blade-foot" },
        state.tab === "settings" ? [
          h("button", { class: "btn primary", type: "button", disabled: !state.draft || state.busy, onClick: applySettings }, "Apply"),
          h("button", { class: "btn secondary", type: "button", disabled: !state.draft, onClick: () => { state.draft = null; render(); } }, "Discard"),
        ] : null,
        h("span", { class: "status-note", role: "status" }, state.note)));
  }

  function svg(d) {
    const ns = "http://www.w3.org/2000/svg";
    const s = document.createElementNS(ns, "svg");
    s.setAttribute("viewBox", "0 0 16 16"); s.setAttribute("width", "14"); s.setAttribute("height", "14"); s.setAttribute("aria-hidden", "true");
    const p = document.createElementNS(ns, "path");
    p.setAttribute("d", d); p.setAttribute("stroke", "currentColor"); p.setAttribute("stroke-width", "1.5");
    s.append(p);
    return s;
  }

  // A poll never redraws the blade while one of its controls has focus (an open dropdown would close).
  function render(fromPoll) {
    renderMenu(); renderBanner(); renderView(true);
    const active = document.activeElement;
    const editing = fromPoll && $("blade").contains(active) && ["SELECT", "INPUT"].includes(active.tagName);
    if (!editing) renderBlade();
  }

  // -- wiring ---------------------------------------------------------------------------------
  window.addEventListener("hashchange", () => { state.page = location.hash.slice(1) || "models"; render(); });
  document.querySelector("[data-action=toggle-menu]").addEventListener("click", (e) => {
    const open = $("menu").classList.toggle("open");
    e.currentTarget.setAttribute("aria-expanded", open ? "true" : "false");
  });
  $("search").addEventListener("input", (e) => {
    state.filter = e.target.value;
    if (state.page !== "models") location.hash = "models";
    render();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && state.selected) closeBlade();
    if (e.key === "/" && e.ctrlKey) { e.preventDefault(); $("menu-filter").focus(); return; }
    if (e.key === "/" && document.activeElement.tagName !== "INPUT") { e.preventDefault(); $("search").focus(); }
  });

  render();
  loadInfo();
  refresh();
  setInterval(() => refresh(true), POLL_MS); // keeps polling during a Load: busy only disables buttons
})();
