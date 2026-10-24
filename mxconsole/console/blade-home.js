// mxconsole, the My models blade: the home of the console. List (default) or cards, remembered per browser;
// a compact list when a model is open beside it, a strip of dots when it is folded into a rail.
// Data: /admin/models + /admin/metrics + /admin/series (polled by core.js) and /admin/system (system-data.js).
import { h, s, icon, store, onData, fmt, dur, colorOf, api, refresh, toast, copyButton, reducedMotion } from "./core.js";
import { register, push, truncate, stack, setHead } from "./blades.js";
import { readView, writeView, safeStorage } from "./prefs.js";
import { syncKeyed } from "./keyed.js";
import { hardwareView } from "./hardware.js";
import { system } from "./system-data.js";
import { PHONE_W } from "./blade-layout.js";

const STATE = { ready: ["running", "Running"], starting: ["starting", "Starting"], cold: ["idle", "Idle"], crashed: ["crashed", "Crashed"] };

/** One model row as the blade shows it. ctx: {metrics, series, info, system(crew), endpoint}. Pure. */
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
    ttft: running && by.ttft_p50_ms !== undefined ? by.ttft_p50_ms : null, requests: by.requests ?? 0,
    vramGb: crew && crew.vram_gb > 0 ? Math.round(crew.vram_gb * 10) / 10 : null,
  };
}
/** Names present now that were not in the previous poll. `prev` is null before the first load: nothing is new then. Pure. */
export function newNames(prev, names) {
  if (prev === null || prev === undefined) return [];
  const had = new Set(prev);
  return names.filter((n) => !had.has(n));
}
export function filterModels(rows, query) {
  const q = String(query || "").trim().toLowerCase();
  return q ? rows.filter((r) => `${r.name} ${r.repo_id || ""}`.toLowerCase().includes(q)) : rows;
}

// -- small pieces ----------------------------------------------------------------------------------
function sparkline(points, w, hgt, color) {
  const pts = points.length > 1 ? points : [0, 0];
  const hi = Math.max(...pts), lo = Math.min(...pts) * 0.92;
  const xy = pts.map((v, i) => `${((i / (pts.length - 1)) * w).toFixed(1)},${(hgt - 2 - ((v - lo) / (hi - lo || 1)) * (hgt - 4)).toFixed(1)}`).join(" ");
  return s("svg", { class: "spark", width: w, height: hgt, viewBox: `0 0 ${w} ${hgt}`, "aria-hidden": "true" },
    s("polyline", { points: xy, fill: "none", stroke: points.length > 1 ? color : "var(--faint)", "stroke-width": "1.6", "stroke-linejoin": "round", "stroke-linecap": "round", "vector-effect": "non-scaling-stroke" }));
}
const stateEl = (v) => h("span", { class: `state ${v.stateKey}` }, h("span", { class: "sd" }), v.stateLabel);
/** A laptop for Local (a lock would read as secure), a lock for a model served over HTTPS on the LAN. */
export const accessIcon = (access) => (access === "secure" ? "lock" : "laptop");
const accessEl = (v) => h("span", { class: `acc ${v.access}` }, icon(accessIcon(v.access), 12, "currentColor", 1.75), v.access === "secure" ? "Secure" : "Local");
const mdot = (name) => h("span", { class: "mdot", style: { "--mc": colorOf(name) } });
const shortEndpoint = (url) => String(url || "").replace(/^https?:\/\//, "");
const cliHint = (text) => h("span", { class: "cli mono" }, text);

export function openModel(name) {
  const cur = stack[1];
  if (cur && cur.name === "model" && cur.opts.id === name) return truncate(2);
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
    // The poll redraws every 3 s. Nothing focusable is rebuilt by it: rows, cards, dots and the filter keep their
    // element per model name (keyed.js), so keyboard focus, hover and scroll survive. Only a change of mode
    // (full / side / rail), of the daemon's reachability or of the crashed set rebuilds anything.
    const storage = safeStorage();
    let view = readView(storage), query = "", shape = "", refs = null, toolsMode = "", crashSig = "";
    let seenNames = null;  // model names of the previous poll, to flash a model that just appeared (not on first load)
    const flashTimers = new Set();
    const ctxNow = () => ({ metrics: store.metrics, series: store.series, info: store.info, system: system.crew, endpoint: (store.info && store.info.endpoint) || `${location.origin}/v1` });
    const selected = () => (stack[1] && stack[1].name === "model" ? stack[1].opts.id : null);

    function addButton(compact) {
      return h("button", { class: compact ? "ib" : "btn fill", type: "button", "data-act": "add", title: "Add a model", "aria-label": "Add a model", onClick: (e) => { e.stopPropagation(); openAdd(); } },
        icon("plus", compact ? 15 : 14, "currentColor", 2), compact ? null : "Add a model");
    }
    function heading(views, mode) {
      const running = views.filter((v) => v.stateKey === "running").length;
      setHead(blade, {
        title: h("span", { class: "t-row" }, "My models", h("span", { class: "count mono" }, String(views.length))),
        sub: mode === "full" ? `Everything this machine serves, behind one OpenAI-compatible endpoint: ${shortEndpoint(ctxNow().endpoint)}` : `${running} running`,
      });
      const want = (mode === "full" || mode === "narrow") && window.innerWidth > PHONE_W ? "full" : "compact";
      if (want !== toolsMode) {  // the Add button is focusable: only rebuild it when the mode changes
        toolsMode = want;
        blade.toolsEl.replaceChildren(...(want === "full" ? [cliHint("mxc serve <model>"), addButton(false)] : [addButton(true)]));
      }
    }

    // -- one element per model, updated in place -----------------------------------------------------
    const markSel = (el) => el.classList.toggle(el._sel, el.dataset.id === selected());
    function makeRow(v) {
      const nameBtn = h("button", { class: "namebtn", type: "button", "data-id": v.name, onClick: (e) => { e.stopPropagation(); openModel(v.name); } });
      const rep = h("span", { class: "rep" });
      const cells = ["", "hide-n", "num", "num hide-n", "num hide-n", "num hide-n", "hide-n arch"].map((c) => h("td", { class: c || null }));
      const tr = h("tr", { class: "row", "data-id": v.name, onClick: () => openModel(v.name) }, h("td", { class: "nm" }, nameBtn, rep), ...cells);
      Object.assign(tr, { _sel: "on", _nameBtn: nameBtn, _rep: rep, _cells: cells });
      return tr;
    }
    function updateRow(tr, v) {
      tr._nameBtn.replaceChildren(mdot(v.name), h("span", { class: "trunc" }, v.name));
      tr._rep.textContent = v.repo;
      const [state, access, tps, ttft, req, vram, arch] = tr._cells;
      state.replaceChildren(...[stateEl(v), v.note ? h("span", { class: "rep" }, v.note) : null].filter(Boolean));
      access.replaceChildren(accessEl(v));
      tps.replaceChildren(h("span", { class: "tpsrow" }, v.points.length > 1 ? sparkline(v.points, 64, 18, colorOf(v.name)) : null, h("span", { class: "tpsn" }, v.tps === null ? "—" : fmt(v.tps))));
      ttft.textContent = v.ttft === null || v.ttft === undefined ? "—" : `${fmt(v.ttft)} ms`;
      req.textContent = v.stateKey === "running" ? fmt(v.requests) : "—";
      vram.textContent = v.vramGb === null ? "—" : `${v.vramGb} GB`;
      arch.textContent = v.arch;
      markSel(tr);
    }
    function makeCard(v) {
      const card = h("button", { class: "card", type: "button", "data-id": v.name, onClick: () => openModel(v.name) });
      card._sel = "sel";
      return card;
    }
    function updateCard(card, v) {
      card.style.setProperty("--mc", colorOf(v.name));
      card.setAttribute("aria-label", `${v.name}, ${v.stateLabel}`);
      card.replaceChildren(
        h("span", { class: "r" }, mdot(v.name), h("span", { class: "nm" }, v.name), stateEl(v)),
        h("span", { class: "r" }, accessEl(v), h("span", { class: "ep" }, shortEndpoint(v.endpoint))),
        v.crashed ? h("span", { class: "crashnote" }, "crashed · open it for the log")
          : h("span", { class: "tps" }, h("span", { class: "n" }, h("span", {}, v.tps === null ? "—" : fmt(v.tps)), h("small", {}, " tok/s")), v.points.length > 1 ? sparkline(v.points, 110, 28, colorOf(v.name)) : null),
        h("span", { class: "foot" }, h("span", { class: "mono" }, v.note || v.repo), v.vramGb === null ? null : h("span", { class: "mono" }, `${v.vramGb} GB`)));
      markSel(card);
    }
    function makeMrow(v) {
      const btn = h("button", { class: "mrow", type: "button", "data-id": v.name, onClick: (e) => { e.stopPropagation(); openModel(v.name); } });
      btn._sel = "on";
      return btn;
    }
    function updateMrow(btn, v) {
      btn.style.setProperty("--mc", colorOf(v.name));
      btn.replaceChildren(mdot(v.name), h("span", { class: "mrow-main" }, h("span", { class: "nm trunc" }, v.name),
        h("span", { class: "sub" }, stateEl(v), v.access === "secure" ? h("span", { class: "acc secure" }, icon("lock", 11, "currentColor", 2), "Secure") : null)),
        h("span", { class: "tp mono" }, v.tps === null ? "—" : fmt(v.tps), h("small", {}, v.tps === null ? "" : "tok/s")));
      markSel(btn);
    }
    function makeDot(v) {
      const dot = h("button", { class: "rdot", type: "button", "data-id": v.name, onClick: (e) => { e.stopPropagation(); openModel(v.name); } }, h("i", {}));
      dot._sel = "on";
      return dot;
    }
    function updateDot(dot, v) {
      dot.title = `${v.name} · ${v.stateLabel}`;
      dot.setAttribute("aria-label", dot.title);
      dot.firstChild.style.setProperty("--c", colorOf(v.name));
      markSel(dot);
    }

    // -- skeletons: built once per shape -----------------------------------------------------------
    function buildContent(host) {  // the list or the card grid; rebuilt when the choice changes
      refs.rows = new Map();
      if (view === "list") {
        const tbody = h("tbody", {});
        refs.rowsParent = tbody; refs.make = makeRow; refs.update = updateRow;
        const head = [["Model", ""], ["State", ""], ["Access", "hide-n"], ["Tokens/s", "num"], ["First token", "num hide-n"], ["Req · 5 min", "num hide-n"], ["VRAM", "num hide-n"], ["Architecture", "hide-n"]];
        host.replaceChildren(h("section", { class: "card models" }, h("div", { class: "card-b flush tablewrap" },
          h("table", { class: "tbl" }, h("thead", {}, h("tr", {}, head.map(([label, c]) => h("th", { class: c || null }, label)))), tbody)),
          h("div", { class: "src note" }, "click a model to open it")));
      } else {
        const grid = h("div", { class: "grid" }, h("button", { class: "card add", type: "button", onClick: openAdd }, icon("plus", 18, "currentColor", 2), h("span", {}, "Add a model"), h("span", { class: "mono small" }, "mxc serve <model>")));
        refs.rowsParent = grid; refs.make = makeCard; refs.update = updateCard;
        host.replaceChildren(grid);
      }
    }
    /** The filter field (omxqua's floating label) and, on a wide blade, the list/cards toggle. */
    function filterBar(withToggle) {
      const input = h("input", { placeholder: " ", "aria-label": "Filter models", value: query, onInput: (e) => { query = e.target.value; draw(); } });
      const field = h("label", { class: "mo has-lead" }, h("span", { class: "lead" }, icon("search", 16)), input, h("span", { class: "fl" }, "Filter models"));
      const bar = h("div", { class: "homebar" }, field, h("span", { class: "grow" }));
      if (withToggle) {
        const seg = (id, label) => h("button", { type: "button", "data-view": id, onClick: () => { view = id; writeView(storage, id); buildContent(refs.content); draw(); } }, label);
        refs.segs = h("div", { class: "seg2", role: "group", "aria-label": "View" }, h("i", {}), seg("list", "List"), seg("cards", "Cards"));
        bar.append(refs.segs);
      }
      return bar;
    }
    function buildFull() {
      refs = { kpis: h("div", { class: "kpis-host" }), callouts: h("div", {}), stale: h("div", { class: "stale mono", role: "status", hidden: true }, "daemon not answering: showing the last numbers"),
        empty: h("div", {}), content: h("div", {}) };
      crashSig = "";
      buildContent(refs.content);
      body.replaceChildren(h("div", { class: "hbody" }, refs.stale, refs.kpis, refs.callouts, filterBar(true), refs.empty, refs.content));
    }
    function buildCompact(kind) {
      const list = kind === "side" || kind === "phone";
      const parent = h("div", { class: list ? "compact" : "rdots" });
      refs = { rows: new Map(), rowsParent: parent, make: list ? makeMrow : makeDot, update: list ? updateMrow : updateDot };
      parent.append(list
        ? h("button", { class: "addrow", type: "button", onClick: openAdd }, icon("plus", 14, "currentColor", 2), "Add a model")
        : h("button", { class: "rdot plus", type: "button", title: "Add a model", "aria-label": "Add a model", onClick: (e) => { e.stopPropagation(); openAdd(); } }, icon("plus", 14, "currentColor", 2)));
      if (kind === "phone") {  // a phone: the numbers, the filter and the compact list; no wide table
        Object.assign(refs, { kpis: h("div", { class: "kpis-host" }), callouts: h("div", {}), stale: h("div", { class: "stale mono", role: "status", hidden: true }, "daemon not answering: showing the last numbers"), empty: h("div", {}), content: parent });
        crashSig = "";
        body.replaceChildren(h("div", { class: "hbody" }, refs.stale, refs.kpis, refs.callouts, filterBar(false), refs.empty), parent);
      } else body.replaceChildren(parent);
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

    // -- the parts that change every poll ----------------------------------------------------------
    function kpis(views) {
      const running = views.filter((v) => v.stateKey === "running");
      const tps = running.reduce((a, v) => a + (v.tps || 0), 0);
      const hw = hardwareView(store.metrics && store.metrics.gpus, system.crew);
      const counts = [["starting", views.filter((v) => v.stateKey === "starting").length], ["idle", views.filter((v) => v.stateKey === "idle").length], ["crashed", views.filter((v) => v.crashed).length]]
        .filter(([, n]) => n).map(([k, n]) => `${n} ${k}`).join(" · ");
      const tile = (value, unit, label, extra) => h("div", {}, h("b", {}, value, unit ? h("small", {}, ` ${unit}`) : null), h("span", {}, label), extra);
      refs.kpis.replaceChildren(h("section", { class: "card" }, h("div", { class: "card-b" }, h("div", { class: "stats" },
        tile(String(running.length), `of ${views.length}`, counts ? `serving · ${counts}` : "serving · all running"),
        tile(fmt(tps), "", `tok/s across ${running.length} running model${running.length === 1 ? "" : "s"}`),
        tile(fmt(store.metrics ? store.metrics.requests : null), "", "requests · 5 min"),
        tile(hw.present ? hw.label.split("/")[0] : "—", hw.present ? `/ ${hw.totalGb} GB` : "", hw.present ? `GPU memory · ${hw.short}` : "GPU memory · no GPU found",
          hw.present ? h("div", { class: "vbar" }, hw.segments.map((seg) => h("span", { title: `${seg.name} · ${seg.gb} GB`, style: { width: `${seg.pct}%`, background: seg.name === "other" || seg.name === "in use" ? "var(--faint)" : colorOf(seg.name) } }))) : null))),
        h("div", { class: "src" }, "mxc ls --json")));
    }
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
    function emptyNote(views, shown) {
      let note = null;
      if (views.length === 0) note = h("div", { class: "empty-state" }, h("h4", {}, "No models yet"), h("p", {}, "Add one: search the MAX catalog or Hugging Face, and it is serving on localhost in a few steps."),
        h("div", { class: "cmd-row" }, addButton(false), cliHint("mxc serve qwen2.5-0.5b")));
      else if (shown.length === 0) note = h("div", { class: "empty" }, `No model matches "${query}".`);
      const sig = views.length === 0 ? "none" : shown.length === 0 ? `nomatch:${query}` : "";
      if (refs.emptySig !== sig) { refs.emptySig = sig; refs.empty.replaceChildren(...(note ? [note] : [])); }
      refs.content.hidden = !!note;
    }

    function draw() {
      if (!blade.alive) return;
      const mode = blade.mode || "full";
      const phone = window.innerWidth <= PHONE_W && mode === "full";
      const wide = (mode === "full" || mode === "narrow") && !phone;
      const numbers = wide || phone;  // the stats and the crashed callout show on a wide blade and on a phone
      if (store.error && !store.models) {
        heading([], mode);
        setShape(`down|${numbers}`, () => body.replaceChildren(numbers ? downPanel() : h("div", { class: "empty" }, "Daemon not answering")));
        return;
      }
      if (store.models === null) { heading([], mode); setShape("loading", () => body.replaceChildren(h("div", { class: "empty" }, "Loading…"))); return; }
      const ctx = ctxNow();
      const views = store.models.map((m) => modelView(m, ctx));
      heading(views, mode);
      setShape(wide ? "full" : phone ? "phone" : mode, () => (wide ? buildFull() : buildCompact(phone ? "phone" : mode === "rail" ? "rail" : "side")));
      let shown = views;
      if (numbers) {
        const keep = new Set(filterModels(store.models, query).map((m) => m.name));
        shown = views.filter((v) => keep.has(v.name));
        kpis(views);
        callouts(views);
        refs.stale.hidden = !store.error;
        emptyNote(views, shown);
      }
      if (wide) {
        refs.segs.querySelectorAll("button").forEach((b) => { b.classList.toggle("on", b.dataset.view === view); b.setAttribute("aria-pressed", String(b.dataset.view === view)); });
        if (window.Omxqua) window.Omxqua.placeThumb(refs.segs);
      }
      syncKeyed(refs.rowsParent, refs.rows, shown, refs.make, refs.update);
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
    blade.onMode = draw;
    window.addEventListener("resize", draw);  // a phone turned sideways, or a window dragged across the phone width
    blade.sync = () => { if (refs && refs.rows) refs.rows.forEach(markSel); };
    const off = onData(draw);
    blade.onClose = () => { off(); window.removeEventListener("resize", draw); flashTimers.forEach(clearTimeout); flashTimers.clear(); };
    draw();
  },
}));
