// Models: a table with a mini trace per model, and a side sheet (Overview, Settings, Logs, Requests).
import { $, h, icon, api, store, fmt, dur, colorOf, lifecycle, STATE_LABEL, clock, terminalBox, reducedMotion } from "./core.js";
import { sparkline, traceSvg } from "./charts.js";
import { loadModel, stopModel, setPinned, saveSettings, cli } from "./actions.js";
import { openPalette } from "./palette.js";

const KEEP_ALIVE = [[90, "90 seconds"], [300, "5 minutes"], [600, "10 minutes"], [1800, "30 minutes"], [3600, "1 hour"]];
const cliDur = (sec) => (sec % 3600 === 0 ? `${sec / 3600}h` : sec % 60 === 0 ? `${sec / 60}m` : `${sec}s`);
const TABS = [["overview", "Overview"], ["settings", "Settings"], ["logs", "Logs"], ["requests", "Requests"]];
const LOG_ENDPOINT = (name) => `/admin/models/${encodeURIComponent(name)}/log`;

const dotFor = (m) => h("span", { class: `stat-dot ${m.state === "ready" ? "live" : m.state}`, style: { "--c": colorOf(m.name) } });
const byModel = (name) => (store.metrics && store.metrics.by_model[name]) || {};
function pointsFor(name) {
  const step = (store.series && store.series.bucket_seconds) || 10;
  return ((store.series && store.series.models[name]) || []).map((b) => ({ t: b.t + step / 2, v: b.decode_tps_p50 }));
}

export function modelsPage(ctx) {
  ctx.actions.append(
    h("button", { class: "btn", type: "button", onClick: () => openPalette("pull") }, icon("download", 15), "Pull"),
    h("button", { class: "btn primary", type: "button", onClick: () => openPalette("serve") }, icon("plus", 15), "Serve model"));

  const ui = { filter: "", state: "all", source: "all" };
  const search = h("input", { class: "input", type: "search", placeholder: "Filter models", "aria-label": "Filter models", onInput: (e) => { ui.filter = e.target.value; drawTable(); } });
  const stateSel = h("select", { class: "select", "aria-label": "State", onChange: (e) => { ui.state = e.target.value; drawTable(); } },
    [["all", "State: all"], ["ready", "State: running"], ["starting", "State: starting"], ["cold", "State: idle"], ["crashed", "State: crashed"]].map(([v, l]) => h("option", { value: v }, l)));
  const sourceSel = h("select", { class: "select", "aria-label": "Source", onChange: (e) => { ui.source = e.target.value; drawTable(); } }, h("option", { value: "all" }, "Source: all"));
  const tbody = h("tbody", {});
  const emptyHost = h("div", {});
  const tablePanel = h("section", { class: "panel scroll-x" },
    h("table", { class: "grid" },
      h("thead", {}, h("tr", {}, ["Model", "State", "Last 5 min", "tok/s", "Lifecycle"].map((c, i) => h("th", { class: i === 3 ? "r" : "" }, c)))), tbody));
  ctx.body.replaceChildren(
    h("div", { class: "filters" }, h("label", { class: "search" }, h("span", { class: "sr-only" }, "Filter"), h("span", { class: "ic" }, icon("search", 14)), search), stateSel, sourceSel),
    tablePanel, emptyHost);

  const sheetState = { name: null, tab: "overview", draft: null, note: "", busy: false };
  let sheet = null;  // {el, destroy, update}
  const main = $("main");

  function drawTable() {
    const models = store.models;
    if (models === null) { tbody.replaceChildren(h("tr", {}, h("td", { colspan: 5, class: "muted" }, "Loading…"))); return; }
    const sources = [...new Set(models.map((m) => m.source))];
    if (sourceSel.options.length !== sources.length + 1) sourceSel.replaceChildren(h("option", { value: "all" }, "Source: all"), ...sources.map((v) => h("option", { value: v }, `Source: ${v}`)));
    sourceSel.value = ui.source; stateSel.value = ui.state;
    tablePanel.hidden = models.length === 0;
    emptyHost.replaceChildren(...(models.length ? [] : [h("div", { class: "panel empty" }, h("h2", {}, "No models yet"),
      h("p", {}, "Serve your first model from a terminal. It shows up here as soon as it is registered."),
      h("div", { class: "code-box" }, h("div", { class: "ln mono" }, h("span", { class: "p" }, "$ "), "hotfire serve qwen2.5-0.5b")))]));
    const q = ui.filter.toLowerCase();
    const rows = models.filter((m) => (ui.state === "all" || m.state === ui.state) && (ui.source === "all" || m.source === ui.source)
      && (!q || [m.name, m.repo_id, m.source, m.architecture].some((v) => v.toLowerCase().includes(q))));
    const t1 = Date.now() / 1000;
    const keep = document.activeElement && document.activeElement.closest && document.activeElement.closest("tr[data-name]");
    const keepName = keep && tbody.contains(keep) ? keep.dataset.name : null; // a poll redraws rows: keep keyboard focus on the same one
    tbody.replaceChildren(...rows.map((m) => {
      const live = m.state === "ready";
      const by = byModel(m.name);
      const pts = pointsFor(m.name);
      const spark = live && pts.some((p) => p.v !== null) ? sparkline(pts, t1 - 300, t1, colorOf(m.name)) : h("span", { class: "muted" }, "—");
      const go = () => { location.hash = `models/${encodeURIComponent(m.name)}`; };
      return h("tr", { class: "pick", "data-name": m.name, "aria-selected": m.name === sheetState.name ? "true" : "false", tabindex: "0", onClick: go,
        onKeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } } },
      h("td", {}, h("span", { class: "model-name mono trunc", title: m.name }, m.name)),
      h("td", {}, h("span", { style: "display:inline-flex;align-items:center;gap:8px;color:var(--text-2)" }, dotFor(m), store.error ? "Unknown" : STATE_LABEL[m.state] || m.state)),
      h("td", {}, spark),
      h("td", { class: "r num" }, live && by.decode_tps_p50 !== undefined && by.decode_tps_p50 !== null ? fmt(by.decode_tps_p50) : "—"),
      h("td", { class: "muted" }, lifecycle(m)));
    }));
    if (keepName) { const again = tbody.querySelector(`tr[data-name="${CSS.escape(keepName)}"]`); if (again) again.focus(); }
    if (!rows.length && models.length) tbody.replaceChildren(h("tr", {}, h("td", { colspan: 5, class: "muted" }, "No model matches these filters.")));
  }

  // ---- the sheet
  const selected = () => (store.models || []).find((m) => m.name === sheetState.name) || null;
  const effective = (m) => ({ pinned: m.pinned, keep_alive: m.keep_alive, ...(sheetState.draft || {}) });

  function openSheet(name, tab) {
    const m = (store.models || []).find((x) => x.name === name);
    const want = tab && TABS.some(([id]) => id === tab) ? tab : (m && m.state === "crashed" ? "logs" : "overview");
    if (sheet && name && sheetState.name === name && sheetState.tab === want) return;
    if (sheet) { sheet.destroy(); sheet = null; }
    if (!name) { sheetState.name = null; main.classList.remove("sheet-open"); drawTable(); return; }
    if (sheetState.name !== name) { sheetState.draft = null; sheetState.note = ""; }
    sheetState.name = name;
    sheetState.tab = want;
    sheet = buildSheet();
    ctx.body.append(sheet.el);
    main.classList.add("sheet-open");
    drawTable();
    sheet.update();
  }

  function buildSheet() {
    const name = sheetState.name;
    const timers = [];
    const titleName = h("span", { class: "mono trunc", title: name }, name);
    const dotHost = h("span", {});
    const sub = h("div", { class: "sheet-sub mono" });
    const actions = h("div", { class: "sheet-actions" });
    const tabs = h("div", { class: "tabs", role: "tablist" }, TABS.map(([id, label]) => h("button", { class: "tab", type: "button", role: "tab", "data-tab": id,
      onClick: () => { location.hash = `models/${encodeURIComponent(name)}/${id}`; } }, label)));
    const body = h("div", { class: "sheet-body" });
    const foot = h("div", { class: "sheet-foot" });
    const el = h("aside", { class: "sheet", "aria-label": `Details of ${name}` },
      h("div", { class: "sheet-top" },
        h("div", { class: "sheet-title" }, h("div", { class: "grow" }, h("div", { class: "sheet-name" }, dotHost, titleName), sub),
          h("button", { class: "ghost", type: "button", "aria-label": "Close details", onClick: () => { location.hash = "models"; } }, icon("x", 16))),
        actions, tabs), body, foot);

    let tabView = null, actionSig = "", footSig = "";
    const tab = sheetState.tab;
    tabs.querySelectorAll(".tab").forEach((b) => b.setAttribute("aria-selected", b.dataset.tab === tab ? "true" : "false"));
    tabView = { overview: overviewTab, settings: settingsTab, logs: logsTab, requests: requestsTab }[tab](name, timers);
    body.replaceChildren(...tabView.nodes);

    function paintActions(m) {
      const sig = `${m.state}|${m.pinned}|${sheetState.busy}`;
      if (sig !== actionSig) {
        actionSig = sig;
        const busy = sheetState.busy || !!store.error;
        const run = (fn) => async () => { sheetState.busy = true; paintActions(selected()); await fn(); sheetState.busy = false; };
        actions.replaceChildren(
          m.state === "cold" || m.state === "crashed"
            ? h("button", { class: "btn", type: "button", disabled: busy, onClick: run(() => loadModel(name)) }, icon("play", 14), "Load")
            : h("button", { class: "btn", type: "button", disabled: busy, onClick: run(() => stopModel(name)) }, icon("square", 14), "Stop"),
          h("button", { class: "btn", type: "button", disabled: busy, onClick: run(() => setPinned(name, !m.pinned)) }, icon("pin", 14), m.pinned ? "Unpin" : "Pin"));
      }
      foot.hidden = tab !== "settings" && !sheetState.note;
      const fsig = `${tab}|${!!sheetState.draft}|${sheetState.busy}|${sheetState.note}`;
      if (fsig !== footSig) {
        footSig = fsig;
        foot.replaceChildren(...(tab === "settings" ? [
          h("button", { class: "btn primary", type: "button", disabled: !sheetState.draft || sheetState.busy, onClick: apply }, "Apply"),
          h("button", { class: "btn", type: "button", disabled: !sheetState.draft, onClick: () => { sheetState.draft = null; sheetState.note = ""; refreshSheet(); } }, "Discard")] : []),
        h("span", { class: "note", role: "status" }, sheetState.note));
      }
    }
    async function apply() {
      sheetState.busy = true; refreshSheet();
      const err = await saveSettings(name, sheetState.draft);
      sheetState.busy = false;
      if (err) sheetState.note = `Not saved: ${err}`; else { sheetState.draft = null; sheetState.note = "Saved."; }
      refreshSheet();
    }
    const refreshSheet = () => { if (sheet && sheet.update) sheet.update(true); };

    return {
      el, destroy: () => { timers.forEach(clearInterval); if (tabView.destroy) tabView.destroy(); el.remove(); },
      update() {
        const m = selected();
        if (!m) { sub.textContent = store.models === null ? "Loading…" : "This model is not registered (any more)."; return; }
        dotHost.replaceChildren(dotFor(m));
        sub.textContent = `${m.repo_id} @ ${m.revision.slice(0, 8)} · ${m.architecture}`;
        paintActions(m);
        tabView.update(m);
      },
    };
  }

  // -- tabs: each returns {nodes, update(m), destroy?}
  function overviewTab(name) {
    const big = h("span", { class: "n num" }, "—"), unit = h("span", { class: "u" }, "tok/s decode"), meta = h("span", { class: "m mono" });
    const traceHost = h("div", {});
    const card = h("div", { class: "trace-card" }, h("div", { class: "hd" }, big, unit, meta), traceHost);
    const life = h("dd", {});
    const facts = h("dl", { class: "facts" });
    return {
      nodes: [card, facts, terminalBox([cli.keepAlive(name, "5m"), cli.logs(name)])],
      update(m) {
        const by = byModel(name);
        big.textContent = m.state === "ready" && by.decode_tps_p50 !== undefined && by.decode_tps_p50 !== null ? fmt(by.decode_tps_p50) : "—";
        meta.textContent = `ttft ${by.ttft_p50_ms === undefined || by.ttft_p50_ms === null ? "—" : fmt(by.ttft_p50_ms) + " ms"}${m.port ? ` · :${m.port}` : ""}`;
        const pts = pointsFor(name), t1 = Date.now() / 1000;
        traceHost.replaceChildren(pts.some((p) => p.v !== null)
          ? traceSvg({ points: pts, t0: t1 - 300, t1, w: Math.max(200, traceHost.clientWidth || 470), h: 90, color: colorOf(name), step: 15, cls: "t" })
          : h("div", { class: "lane-note", style: "height:90px" }, h("div", { class: "bar idle" }), h("span", { class: "mono" }, m.state === "ready" ? "no streamed requests in 5 min" : "not running")));
        life.textContent = lifecycle(m);
        facts.replaceChildren(...[["State", `${STATE_LABEL[m.state] || m.state}${m.in_flight ? ` · ${m.in_flight} in flight` : ""}`], ["Repository", `${m.repo_id} @ ${m.revision}`],
          ["Architecture", m.architecture], ["Source", m.source], ["Served as", m.name], ["Lifecycle", lifecycle(m)]].flatMap(([k, v]) => [h("dt", {}, k), h("dd", { class: k === "State" || k === "Lifecycle" ? "" : "mono" }, v)]));
      },
    };
  }

  function settingsTab(name) {
    const sw = h("button", { class: "switch", type: "button", role: "switch", "aria-label": "Keep loaded" });
    const idleText = h("div", { class: "b" });
    const select = h("select", { class: "select", "aria-label": "Unload after idle", onChange: (e) => { sheetState.draft = { ...(sheetState.draft || {}), keep_alive: Number(e.target.value) }; sheetState.note = ""; sheet.update(); } });
    const term = h("div", {});
    sw.addEventListener("click", () => { const e = effective(selected()); sheetState.draft = { ...(sheetState.draft || {}), pinned: !e.pinned }; sheetState.note = ""; sheet.update(); });
    const nodes = [
      h("div", {},
        h("div", { class: "set-row" }, h("div", {}, h("div", { class: "a" }, "Keep loaded"), h("div", { class: "b" }, "Never unload when idle")), sw),
        h("div", { class: "set-row" }, h("div", {}, h("div", { class: "a" }, "Unload after idle"), idleText), select)),
      term];
    return {
      nodes,
      update(m) {
        const e = effective(m);
        sw.setAttribute("aria-checked", e.pinned ? "true" : "false");
        const options = KEEP_ALIVE.some(([sec]) => sec === e.keep_alive) ? KEEP_ALIVE : [...KEEP_ALIVE, [e.keep_alive, dur(e.keep_alive)]];
        if (select.options.length !== options.length) select.replaceChildren(...options.map(([sec, label]) => h("option", { value: sec }, label)));
        select.value = String(e.keep_alive);
        select.disabled = e.pinned;
        idleText.textContent = e.pinned ? "Pinned: it stays loaded" : m.state === "ready" && m.idle_seconds !== null ? `Idle for ${dur(m.idle_seconds)} now` : "The next request after an unload waits for a cold start";
        term.replaceChildren(terminalBox([e.pinned ? cli.pin(name) : cli.keepAlive(name, cliDur(e.keep_alive)), cli.logs(name)]));
      },
    };
  }

  function logsTab(name, timers) {
    const pre = h("pre", { class: "log mono" }, "Loading…");
    const hint = h("div", { class: "hint mono" }, `~/.hotfire/logs/${name}.log · last 8 KB`);
    const load = async () => {
      try {
        const text = await api("GET", LOG_ENDPOINT(name));
        const atEnd = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 24;
        if (pre.dataset.text !== text) { pre.dataset.text = text; pre.textContent = text || "The log is empty so far. Lines appear here as the model writes them."; if (atEnd || !pre.dataset.seen) pre.scrollTop = pre.scrollHeight; pre.dataset.seen = "1"; }
      } catch (e) { pre.textContent = `Could not read the log: ${e.message}`; }
    };
    load();
    timers.push(setInterval(load, 3000));
    return { nodes: [pre, hint, terminalBox([cli.logs(name)])], update() {} };
  }

  function requestsTab(name, timers) {
    const host = h("div", {});
    const load = async () => {
      try {
        const rows = await api("GET", `/admin/requests?model=${encodeURIComponent(name)}&limit=30`);
        host.replaceChildren(rows.length ? h("table", { class: "grid mini-table" },
          h("thead", {}, h("tr", {}, ["Time", "App", "Status", "Tokens", "TTFT"].map((c) => h("th", {}, c)))),
          h("tbody", {}, rows.map((r) => h("tr", {}, h("td", { class: "mono" }, clock(r.ts)), h("td", {}, r.app || h("span", { class: "muted" }, "no key")),
            h("td", { class: `mono ${r.status < 400 ? "ok-text" : "bad-text"}` }, String(r.status)),
            h("td", { class: "mono" }, r.completion_tokens === null ? "—" : `${fmt(r.prompt_tokens)} → ${r.estimated ? "~" : ""}${fmt(r.completion_tokens)}`),
            h("td", { class: "mono" }, r.ttft_ms === null ? "—" : `${fmt(r.ttft_ms)} ms`)))))
          : h("div", { class: "hint" }, "No requests to this model yet."));
      } catch (e) { host.textContent = `Could not load requests: ${e.message}`; }
    };
    load();
    timers.push(setInterval(load, 3000));
    return { nodes: [host, terminalBox(["hotfire requests --limit 50"])], update() {} };
  }

  // ---- wiring
  const onKey = (e) => { if (e.key === "Escape" && sheetState.name && !document.querySelector("dialog[open], .overlay")) location.hash = "models"; };
  document.addEventListener("keydown", onKey);
  const route = (args) => openSheet(args[0] || null, args[1]);
  route(ctx.args);

  return {
    route,
    update() {
      const models = store.models || [];
      ctx.setSub(`hotfire@${(store.info && store.info.host) || "local"} · ${models.length} registered · ${models.filter((m) => m.state === "ready").length} running`);
      drawTable();
      if (sheet) sheet.update();
    },
    destroy() { document.removeEventListener("keydown", onKey); if (sheet) sheet.destroy(); main.classList.remove("sheet-open"); },
  };
}
