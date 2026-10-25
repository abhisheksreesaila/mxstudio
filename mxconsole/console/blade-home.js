// mxconsole, My models: the home page, a library (F-055). A full-width search, a groups column (Show · Kind · Your labels)
// and the models as a table you can tick and label. The search, groups and labelling live in library.js (F-056's switcher reuses them).
// A List or Cards toggle (remembered) shows the same models either way. Data, all polled by core.js / system-data.js:
//   Size /admin/models size_bytes · Memory /admin/system crew vram_gb · tok/s /admin/series (live) else /admin/metrics decode_tps_p50
//   First token /admin/metrics ttft_p50_ms · Requests /admin/metrics requests (last 5 min) · Access /admin/info lan_endpoint.
import { h, icon, store, onData, fmt, dur, colorOf, api, refresh, toast, copyButton, reducedMotion } from "./core.js";
import { register, push, setHead } from "./blades.js";
import { syncKeyed } from "./keyed.js";
import { hardwareView } from "./hardware.js";
import { system } from "./system-data.js";
import { createLibrary, groupName, tagsEl } from "./library.js";
import { sizeLabel } from "./library-logic.js";
import { removeModels } from "./remove.js";
import { readView, writeView, safeStorage } from "./prefs.js";

const STATE = { ready: ["running", "Running"], starting: ["starting", "Starting"], cold: ["idle", "Idle"], crashed: ["crashed", "Crashed"] };
const PHONE = 760;  // the shell's phone width: the nav becomes a tab bar and the groups a chip row

/** One model row as the page shows it. ctx: {metrics, series, info, system(crew), endpoint}. Pure. */
export function modelView(m, ctx) {
  const [stateKey, stateLabel] = STATE[m.state] || ["idle", "Idle"];
  const by = (ctx.metrics && ctx.metrics.by_model && ctx.metrics.by_model[m.name]) || {};
  const buckets = (ctx.series && ctx.series.models && ctx.series.models[m.name]) || [];
  const points = buckets.map((b) => b.decode_tps_p50).filter((v) => v !== null && v !== undefined);
  const running = stateKey === "running";
  const live = points.length ? points[points.length - 1] : by.decode_tps_p50;
  const secure = !!(ctx.info && ctx.info.lan_endpoint);
  const crew = (ctx.system || []).find((c) => c.id === `model:${m.name}`);
  let note = "";
  if (stateKey === "starting") note = m.starting_seconds !== null && m.starting_seconds !== undefined ? `compiling · ${dur(m.starting_seconds)}` : "starting";
  else if (m.pinned) note = "pinned";
  else if (running && m.idle_seconds !== null && m.idle_seconds !== undefined && m.keep_alive) note = `idle ${dur(m.idle_seconds)} of ${dur(m.keep_alive)}`;
  return {
    name: m.name, repo: m.repo_id || "", stateKey, stateLabel, crashed: stateKey === "crashed", note,
    arch: String(m.architecture || "").replace(/(ForCausalLM|ForConditionalGeneration|LMHeadModel)$/, "") || "—",
    access: secure ? "secure" : "local", endpoint: secure ? ctx.info.lan_endpoint : ctx.endpoint,
    tps: running && live !== null && live !== undefined ? Math.round(live) : null, points: running ? points : [],
    ttft: running && by.ttft_p50_ms !== undefined ? by.ttft_p50_ms : null, requests: by.requests ?? (running ? 0 : null),
    sizeBytes: m.size_bytes ?? null,
    vramGb: crew && crew.vram_gb > 0 ? Math.round(crew.vram_gb * 10) / 10 : null,
    kind: m.kind, labels: m.labels || [],
  };
}
/** What each number column shows for a model: text, with "–" where the daemon has no value. Pure. */
export function cellsFor(v) {
  const dash = (x, f) => (x === null || x === undefined ? "–" : f(x));
  return {
    size: sizeLabel(v.sizeBytes), memory: dash(v.vramGb, (x) => `${x} GB`), tps: dash(v.tps, (x) => fmt(x)),
    ttft: dash(v.ttft, (x) => `${fmt(x)} ms`), requests: dash(v.requests, (x) => fmt(x)),
    access: v.access === "secure" ? "Secure · LAN" : "This machine",
  };
}
/** Names present now that were not in the previous poll. `prev` is null before the first load: nothing is new then. Pure. */
export function newNames(prev, names) {
  if (prev === null || prev === undefined) return [];
  const had = new Set(prev);
  return names.filter((n) => !had.has(n));
}

// -- small pieces ----------------------------------------------------------------------------------
const stateEl = (v) => h("span", { class: `state ${v.stateKey}` }, h("span", { class: "sd" }), v.stateLabel);
/** A laptop for Local (a lock would read as secure), a lock for a model served over HTTPS on the LAN. */
export const accessIcon = (access) => (access === "secure" ? "lock" : "laptop");
const mdot = (name) => h("span", { class: "mdot", style: { "--mc": colorOf(name) } });
const shortEndpoint = (url) => String(url || "").replace(/^https?:\/\//, "");
const cliHint = (text) => h("span", { class: "cli mono" }, text);

/** List | Cards, beside the search. Remembers the choice; onPick(view) redraws the page. */
export function viewToggle(view, onPick) {
  const el = h("div", { class: "seg view", role: "group", "aria-label": "View" });
  const buttons = [["list", "List", "list"], ["cards", "Cards", "layout-grid"]].map(([key, label, ic]) => h("button", { type: "button", "data-view": key, onClick: () => pick(key) }, icon(ic, 15), label));
  const paint = (now) => buttons.forEach((b) => { const on = b.getAttribute("data-view") === now; b.classList.toggle("on", on); b.setAttribute("aria-pressed", String(on)); });
  function pick(key) {
    paint(key);
    const storage = safeStorage();
    if (storage) writeView(storage, key);
    onPick(key);
  }
  el.append(...buttons);
  paint(view);
  return el;
}

/** One model as a card: tick, name, state, tok/s, memory, requests, kind and labels. `lib` supplies ticked/tick/draggable. */
export function makeCard(v, lib, open) {
  const check = h("button", { class: "cb", type: "button", "data-act": "tick", onClick: (e) => { e.stopPropagation(); lib.tick(v.name); } }, icon("check", 14, "currentColor", 3));
  const nameBtn = h("button", { class: "namebtn", type: "button", "data-id": v.name, onClick: (e) => { e.stopPropagation(); open(v.name); } });
  const state = h("span", {});
  const stat = (label) => { const val = h("span", {}); return [h("div", {}, h("small", {}, label), val), val]; };
  const [tpsBox, tps] = stat("tok/s"), [memBox, mem] = stat("Memory"), [reqBox, req] = stat("Requests");
  const tags = h("div", { class: "tg3" });
  const card = h("div", { class: "mc", "data-id": v.name, onClick: () => open(v.name) }, h("div", { class: "top" }, check, nameBtn, state), h("div", { class: "k3" }, tpsBox, memBox, reqBox), tags);
  Object.assign(card, { _check: check, _nameBtn: nameBtn, _state: state, _vals: [tps, mem, req], _tags: tags });
  lib.draggable(card, v.name);
  return card;
}
export function updateCard(card, v, lib) {
  const ticked = lib.ticked.has(v.name);
  card.classList.toggle("sel", ticked);
  card._check.classList.toggle("on", ticked);
  card._check.setAttribute("aria-pressed", String(ticked));
  card._check.setAttribute("aria-label", `Select ${v.name}`);
  card._nameBtn.replaceChildren(mdot(v.name), h("span", { class: "trunc" }, v.name));
  card._state.replaceChildren(stateEl(v));
  const c = cellsFor(v);
  [c.tps, c.memory, c.requests].forEach((text, i) => { card._vals[i].textContent = text; });
  card._tags.replaceChildren(tagsEl(v));
}
export function cardEl(v, lib, open) {
  const card = makeCard(v, lib, open);
  updateCard(card, v, lib);
  return card;
}

export function openModel(name) {
  push("model", { id: name }, 0);
}
export function openAdd() {
  push("add", {}, 0);
}
async function restart(name) {
  try {
    await api("POST", `/admin/models/${encodeURIComponent(name)}/load`);
    toast(`Starting ${name} · $ mxc serve ${name}`);
  } catch (e) {
    toast(`Could not start ${name}: ${e.message}`);
  }
  refresh();
}
function viewLogs(name) {
  push("logs", { id: name }, 0);
}

register("home", (blade) => ({
  title: "My models", crumb: "My models", icon: "layout-grid", hue: "var(--hue-models)", side: 300, min: 0,
  mount(body) {
    // The poll redraws every 3 s. Nothing focusable is rebuilt by it: rows keep their element per model name (keyed.js),
    // the search and the groups column are built once (library.js), so focus, hover, typing and scroll survive.
    let shape = "", refs = null, crashSig = "", toolsMode = "", seenNames = null, selAll = null, view = readView(safeStorage());
    const flashTimers = new Set();
    const ctxNow = () => ({ metrics: store.metrics, series: store.series, info: store.info, system: system.crew, endpoint: (store.info && store.info.endpoint) || `${location.origin}/v1` });
    const lib = createLibrary({ onChange: () => draw(), placeholder: "Search by name, kind or #label", onRemove: (names) => removeModels(names) });
    const stopKeys = lib.listen();

    function addButton(compact) {
      return h("button", { class: compact ? "ib" : "btn fill", type: "button", "data-act": "add", title: "Add a model", "aria-label": "Add a model", onClick: (e) => { e.stopPropagation(); openAdd(); } },
        icon("plus", compact ? 15 : 14, "currentColor", 2), compact ? null : "Add a model");
    }
    function heading(views) {
      const running = views.filter((v) => v.stateKey === "running").length;
      const hw = hardwareView(store.metrics && store.metrics.gpus, system.crew);
      setHead(blade, {
        title: h("span", { class: "t-row" }, "My models", h("span", { class: "count mono" }, String(views.length))),
        sub: `${views.length} model${views.length === 1 ? "" : "s"} · ${running} running${hw.present ? ` · ${hw.usedGb} of ${hw.totalGb} GB GPU` : ""} · ${shortEndpoint(ctxNow().endpoint)}`,
      });
      const want = window.innerWidth > PHONE ? "full" : "compact";
      if (want !== toolsMode) {  // the Add button is focusable: only rebuild it when the width class changes
        toolsMode = want;
        blade.toolsEl.replaceChildren(...(want === "full" ? [cliHint("mxc ls"), addButton(false)] : [addButton(true)]));
      }
    }

    // -- one row per model, updated in place ---------------------------------------------------------
    function makeRow(v) {
      const check = h("button", { class: "cb", type: "button", "data-act": "tick", onClick: (e) => { e.stopPropagation(); lib.tick(v.name); } }, icon("check", 14, "currentColor", 3));
      const nameBtn = h("button", { class: "namebtn", type: "button", "data-id": v.name, onClick: (e) => { e.stopPropagation(); openModel(v.name); } });
      const rep = h("span", { class: "rep" });
      const cells = ["", "tg hide-p", "num c3 hide-p", "num c3 hide-p", "num", "num c2 hide-p", "num c2 hide-p", "axs c2 hide-p"].map((c) => h("td", { class: c || null }));
      const tr = h("tr", { class: "row", "data-id": v.name, onClick: () => openModel(v.name) }, h("td", { class: "ck" }, check), h("td", { class: "nm" }, nameBtn, rep), ...cells);
      Object.assign(tr, { _check: check, _nameBtn: nameBtn, _rep: rep, _cells: cells });
      lib.draggable(tr, v.name);
      return tr;
    }
    function updateRow(tr, v) {
      const ticked = lib.ticked.has(v.name);
      tr.classList.toggle("sel", ticked);
      tr._check.classList.toggle("on", ticked);
      tr._check.setAttribute("aria-pressed", String(ticked));
      tr._check.setAttribute("aria-label", `Select ${v.name}`);
      tr._nameBtn.replaceChildren(mdot(v.name), h("span", { class: "trunc" }, v.name));
      tr._rep.textContent = v.repo;
      const [state, tags, ...numbers] = tr._cells;
      state.replaceChildren(...[stateEl(v), v.note ? h("span", { class: "rep" }, v.note) : null].filter(Boolean));
      tags.replaceChildren(tagsEl(v));
      const c = cellsFor(v);
      [c.size, c.memory, c.tps, c.ttft, c.requests, c.access].forEach((text, i) => { numbers[i].textContent = text; });
    }

    // -- skeletons: built once per shape ---------------------------------------------------------------
    function buildFull() {
      selAll = h("button", { class: "cb", type: "button", "data-act": "tick-all", "aria-label": "Select all shown", onClick: () => lib.tickAll(refs.shownNames) }, icon("check", 14, "currentColor", 3));
      const tbody = h("tbody", {});
      refs = { stale: h("div", { class: "stale mono", role: "status", hidden: true }, "daemon not answering: showing the last numbers"), callouts: h("div", {}), empty: h("div", {}),
        cnt: h("span", {}), rows: new Map(), rowsParent: tbody, none: h("div", { class: "none", hidden: true }), shownNames: [] };
      crashSig = "";
      const head = [["", "ck"], ["Model", ""], ["State", ""], ["Kind · labels", "tg hide-p"], ["Size", "num c3 hide-p"], ["Memory", "num c3 hide-p"], ["tok/s", "num"], ["First token", "num c2 hide-p"], ["Requests", "num c2 hide-p", "last 5 minutes"], ["Access", "c2 hide-p"]];
      const table = h("table", { class: "tbl cx-tbl" }, h("thead", {}, h("tr", {}, head.map(([label, c, hint], i) => h("th", { class: c || null, title: hint || null }, i === 0 ? selAll : label)))), tbody);
      const grid = h("div", { class: "cgrid" });
      refs.cards = new Map();
      refs.grid = grid;
      refs.table = table;
      refs.lib = h("div", { class: "lib" }, lib.groups,
        h("div", { class: "res" }, h("div", { class: "cnt" }, refs.cnt, h("span", { class: "ht" }, "tick models to label many at once, or drag them onto a label")),
          h("div", { class: "body2" }, table, grid, refs.none)));
      refs.toggle = viewToggle(view, (next) => { view = next; draw(); });
      body.replaceChildren(h("div", { class: "home" }, h("div", { class: "notes" }, refs.stale, refs.callouts, refs.empty), h("div", { class: "hbar" }, lib.search, refs.toggle), refs.lib, lib.bar));
    }
    function downPanel() {
      return h("div", { class: "hbody" }, h("div", { class: "notice down", role: "alert" },
        h("div", { class: "lab mono" }, icon("triangle-alert", 14), "the daemon is not answering"),
        h("h4", {}, "omxconsole cannot reach the daemon"),
        h("p", {}, "Start it in a terminal, then this page fills in on its own. Nothing here is lost."),
        h("div", { class: "cmd-row" }, h("code", { class: "mono cmd" }, "mxc daemon"), copyButton(() => "mxc daemon", "Copy", "btn tonal sm"),
          h("button", { class: "btn fill sm", type: "button", onClick: () => refresh() }, icon("refresh-cw", 13), "Try again"))));
    }
    const setShape = (next, build) => { if (shape !== next) { shape = next; build(); } };

    function callouts(views) {
      const crashed = views.filter((v) => v.crashed);
      const sig = crashed.map((v) => v.name).join("|");
      if (sig === crashSig) return;  // its buttons are focusable: rebuild only when the crashed set changes
      crashSig = sig;
      refs.callouts.replaceChildren(...crashed.map((v) => h("div", { class: "attn", role: "alert" },
        icon("triangle-alert", 16), h("span", {}, h("b", { class: "mono" }, v.name), " crashed. Its log says why."), h("span", { class: "grow" }),
        h("button", { class: "btn tonal sm", type: "button", onClick: () => viewLogs(v.name) }, icon("scroll-text", 13), "View logs"),
        h("button", { class: "btn fill sm", type: "button", onClick: () => restart(v.name) }, icon("refresh-cw", 13), "Restart"))));
    }
    function emptyNote(views) {
      const sig = views.length === 0 ? "none" : "";
      if (refs.emptySig !== sig) {
        refs.emptySig = sig;
        refs.empty.replaceChildren(...(views.length === 0 ? [h("div", { class: "empty-state" }, h("h4", {}, "No models yet"), h("p", {}, "Add one: search the MAX catalog or Hugging Face, and it is serving on localhost in a few steps."),
          h("div", { class: "cmd-row" }, addButton(false), cliHint("mxc serve qwen2.5-0.5b")))] : []));
      }
      refs.lib.hidden = views.length === 0;
      lib.search.hidden = views.length === 0;
      lib.bar.hidden = views.length === 0;
    }

    function draw() {
      if (!blade.alive) return;
      if (store.error && !store.models) {
        heading([]);
        setShape("down", () => body.replaceChildren(downPanel()));
        return;
      }
      if (store.models === null) { heading([]); setShape("loading", () => body.replaceChildren(h("div", { class: "empty" }, "Loading…"))); return; }
      const ctx = ctxNow();
      const all = store.models.map((m) => modelView(m, ctx));
      heading(all);
      setShape("full", buildFull);
      lib.update();
      const byName = new Map(all.map((v) => [v.name, v]));
      const shown = lib.visible().map((m) => byName.get(m.name));
      refs.shownNames = shown.map((v) => v.name);
      refs.stale.hidden = !store.error;
      callouts(all);
      emptyNote(all);
      const allTicked = shown.length > 0 && shown.every((v) => lib.ticked.has(v.name));
      selAll.classList.toggle("on", allTicked);
      selAll.setAttribute("aria-pressed", String(allTicked));
      refs.cnt.textContent = `${groupName(lib.group)} · ${shown.length}${lib.q.trim() ? ` matching “${lib.q.trim()}”` : ""}`;
      refs.none.hidden = shown.length > 0;
      refs.none.textContent = shown.length ? "" : "Nothing matches. Try another group or clear the search.";
      lib.search.querySelector("input").placeholder = `Search ${all.length} model${all.length === 1 ? "" : "s"} by name, kind or #label`;
      refs.table.hidden = view === "cards";
      refs.grid.hidden = view !== "cards";
      if (view === "cards") syncKeyed(refs.grid, refs.cards, shown, (v) => makeCard(v, lib, openModel), (el, v) => updateCard(el, v, lib));
      else syncKeyed(refs.rowsParent, refs.rows, shown, makeRow, updateRow);
      flashNew(store.models.map((m) => m.name));
    }
    function flashNew(names) {
      const fresh = newNames(seenNames, names);
      seenNames = names;
      if (reducedMotion()) return;
      for (const name of fresh) {
        const el = refs && refs.rows && refs.rows.get(name);
        if (!el) continue;
        el.classList.add("flash");
        const t = setTimeout(() => { flashTimers.delete(t); el.classList.remove("flash"); }, 1700);
        flashTimers.add(t);
      }
    }
    blade.redraw = draw;
    window.addEventListener("resize", draw);  // a phone turned sideways, or a window dragged across the phone width
    const poll = () => { draw(); lib.loadLabels(); };
    const off = onData(poll);
    blade.onClose = () => { off(); stopKeys(); window.removeEventListener("resize", draw); flashTimers.forEach(clearTimeout); flashTimers.clear(); };
    draw();
  },
}));
