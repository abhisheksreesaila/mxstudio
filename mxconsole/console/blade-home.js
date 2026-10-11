// mxconsole, the My models blade: the home of the console. List (default) or cards, remembered per browser;
// a compact list when a model is open beside it, a strip of dots when it is folded into a rail.
// Data: /admin/models + /admin/metrics + /admin/series (polled by core.js) and /admin/system (system-data.js).
import { h, s, icon, store, onData, fmt, dur, colorOf, api, refresh, toast, copyButton } from "./core.js";
import { register, push, truncate, stack, setHead } from "./blades.js";
import { readView, writeView } from "./prefs.js";
import { hardwareView } from "./hardware.js";
import { system } from "./system-data.js";

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
    vramGb: crew && crew.gpu_mb ? Math.round((crew.gpu_mb / 1024) * 10) / 10 : null,
  };
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
const accessEl = (v) => h("span", { class: `acc ${v.access}` }, icon(v.access === "secure" ? "lock" : "plug", 11, "currentColor", 2), v.access === "secure" ? "Secure" : "Local");
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
  push("model", { id: name }, 0);
  push("logs", { id: name }, 1);
}

register("home", (blade) => ({
  title: "My models", crumb: "My models", icon: "layout-grid", hue: "var(--hue-models)", side: 300, min: 0,
  mount(body) {
    let view = readView(localStorage), query = "";
    const rows = () => (store.models || []);
    const ctxNow = () => ({ metrics: store.metrics, series: store.series, info: store.info, system: system.crew, endpoint: (store.info && store.info.endpoint) || `${location.origin}/v1` });
    const selected = () => (stack[1] && stack[1].name === "model" ? stack[1].opts.id : null);

    function addButton(compact) {
      const btn = h("button", { class: compact ? "ib" : "btn primary", type: "button", "data-act": "add", title: "Add a model", "aria-label": "Add a model", onClick: (e) => { e.stopPropagation(); openAdd(); } },
        icon("plus", compact ? 15 : 14, "currentColor", 2), compact ? null : "Add a model");
      return btn;
    }
    const modelButton = (cls, name, ...kids) => h("button", { class: cls, type: "button", "data-id": name, onClick: (e) => { e.stopPropagation(); openModel(name); } }, ...kids);

    function heading(views, mode) {
      const running = views.filter((v) => v.stateKey === "running").length;
      setHead(blade, {
        title: h("span", { class: "t-row" }, "My models", h("span", { class: "count mono" }, String(views.length))),
        sub: mode === "full" ? `Everything this machine serves, behind one OpenAI-compatible endpoint: ${shortEndpoint(ctxNow().endpoint)}` : `${running} running`,
      });
      blade.toolsEl.replaceChildren(...(mode === "full" ? [cliHint("mxc serve <model>"), addButton(false)] : [addButton(true)]));
    }

    function downPanel() {
      return h("div", { class: "hbody" }, h("div", { class: "notice down", role: "alert" },
        h("div", { class: "lab mono" }, icon("triangle-alert", 14), "the daemon is not answering"),
        h("h4", {}, "mxconsole cannot reach the daemon"),
        h("p", {}, "Start it in a terminal, then this page fills in on its own. Nothing here is lost."),
        h("div", { class: "cmd-row" }, h("code", { class: "mono cmd" }, "mxc daemon"), copyButton(() => "mxc daemon", "Copy", "btn ghost sm"),
          h("button", { class: "btn dark sm", type: "button", onClick: () => refresh() }, icon("refresh-cw", 13), "Try again"))));
    }

    function kpis(views) {
      const running = views.filter((v) => v.stateKey === "running");
      const tps = running.reduce((a, v) => a + (v.tps || 0), 0);
      const hw = hardwareView(store.metrics && store.metrics.gpus, system.crew);
      const counts = [["starting", views.filter((v) => v.stateKey === "starting").length], ["idle", views.filter((v) => v.stateKey === "idle").length], ["crashed", views.filter((v) => v.crashed).length]]
        .filter(([, n]) => n).map(([k, n]) => `${n} ${k}`).join(" · ") || "all running";
      const tile = (label, value, unit, sub, extra) => h("div", { class: "kpi" }, h("div", { class: "lbl mono" }, label), h("div", { class: "v mono" }, value, unit ? h("small", {}, ` ${unit}`) : null), sub ? h("div", { class: "sub" }, sub) : null, extra);
      return h("div", { class: "kpis" },
        tile("Serving", String(running.length), `of ${views.length} models`, counts),
        tile("Throughput now", fmt(tps), "tok/s", `across ${running.length} running model${running.length === 1 ? "" : "s"}`),
        tile("Requests · 5 min", fmt(store.metrics ? store.metrics.requests : null), "", store.info && store.info.lan_endpoint ? "secured for your LAN" : "localhost only"),
        tile("GPU memory", hw.present ? hw.label.split("/")[0] : "—", hw.present ? `/ ${hw.totalGb} GB` : "no GPU found", null,
          hw.present ? h("div", { class: "vbar" }, hw.segments.map((seg) => h("span", { title: `${seg.name} · ${seg.gb} GB`, style: { width: `${seg.pct}%`, background: seg.name === "other" || seg.name === "in use" ? "var(--faint)" : colorOf(seg.name) } }))) : null));
    }

    function crashCallouts(views) {
      return views.filter((v) => v.crashed).map((v) => h("div", { class: "attn", role: "alert" },
        icon("triangle-alert", 16), h("span", {}, h("b", { class: "mono" }, v.name), " crashed. Its log says why."), h("span", { class: "grow" }),
        h("button", { class: "btn ghost sm", type: "button", onClick: () => viewLogs(v.name) }, icon("scroll-text", 13), "View logs"),
        h("button", { class: "btn dark sm", type: "button", onClick: () => restart(v.name) }, icon("refresh-cw", 13), "Restart")));
    }

    function listTable(views) {
      const head = ["Model", "State", "Access", "Tokens/s", "First token", "Req · 5 min", "VRAM", "Architecture"];
      return h("div", { class: "table tablewrap" }, h("table", {},
        h("thead", {}, h("tr", {}, head.map((t) => h("th", {}, t)))),
        h("tbody", {}, views.map((v) => h("tr", { class: `row ${v.name === selected() ? "on" : ""}`, tabindex: "0", "data-id": v.name, onClick: () => openModel(v.name),
          onKeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openModel(v.name); } } },
          h("td", { class: "nm" }, h("span", { class: "nmrow" }, mdot(v.name), h("span", { class: "trunc" }, v.name)), h("span", { class: "rep" }, v.repo)),
          h("td", {}, stateEl(v), v.note ? h("span", { class: "rep" }, v.note) : null),
          h("td", {}, accessEl(v)),
          h("td", { class: "num" }, h("span", { class: "tpsrow" }, h("span", { class: "tpsn" }, v.tps === null ? "—" : fmt(v.tps)), v.points.length > 1 ? sparkline(v.points, 64, 18, colorOf(v.name)) : null)),
          h("td", { class: "num" }, v.ttft === null || v.ttft === undefined ? "—" : `${fmt(v.ttft)} ms`),
          h("td", { class: "num" }, v.stateKey === "running" ? fmt(v.requests) : "—"),
          h("td", { class: "num" }, v.vramGb === null ? "—" : `${v.vramGb} GB`),
          h("td", { class: "num muted" }, v.arch))))));
    }

    function cardGrid(views) {
      return h("div", { class: "grid" },
        views.map((v) => h("div", { class: `card ${v.name === selected() ? "sel" : ""}`, role: "button", tabindex: "0", "data-id": v.name, style: { "--mc": colorOf(v.name) }, "aria-label": `${v.name}, ${v.stateLabel}`,
          onClick: () => openModel(v.name), onKeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openModel(v.name); } } },
          h("div", { class: "r" }, mdot(v.name), h("span", { class: "nm" }, v.name), stateEl(v)),
          h("div", { class: "r" }, accessEl(v), h("span", { class: "ep" }, shortEndpoint(v.endpoint))),
          v.crashed ? h("div", { class: "crashnote" }, "crashed · open it for the log")
            : h("div", { class: "tps" }, h("div", { class: "n" }, h("span", {}, v.tps === null ? "—" : fmt(v.tps)), h("small", {}, " tok/s")), v.points.length > 1 ? sparkline(v.points, 110, 28, colorOf(v.name)) : null),
          h("div", { class: "foot" }, h("span", { class: "mono" }, v.note || v.repo), v.vramGb === null ? null : h("span", { class: "mono" }, `${v.vramGb} GB`)))),
        h("button", { class: "card add", type: "button", onClick: openAdd }, icon("plus", 18, "currentColor", 2), h("span", {}, "Add a model"), h("span", { class: "mono small" }, "mxc serve <model>")));
    }

    function emptyPanel() {
      return h("div", { class: "empty-state" }, h("h4", {}, "No models yet"), h("p", {}, "Add one: search the MAX catalog or Hugging Face, and it is serving on localhost in a few steps."),
        h("div", { class: "cmd-row" }, addButton(false), cliHint("mxc serve qwen2.5-0.5b")));
    }

    function fullBody(views) {
      const input = h("input", { placeholder: "Filter models", "aria-label": "Filter models", value: query, onInput: (e) => { query = e.target.value; draw(); } });
      const seg = (id, label, ic) => h("button", { type: "button", class: view === id ? "on" : "", "aria-pressed": String(view === id),
        onClick: () => { view = id; writeView(localStorage, id); draw(); } }, icon(ic, 14), label);
      const shown = filterModels(store.models, query).map((m) => views.find((v) => v.name === m.name));
      return h("div", { class: "hbody" }, kpis(views), crashCallouts(views),
        h("div", { class: "homebar" }, h("label", { class: "filter" }, icon("search", 14), input), h("span", { class: "grow" }),
          h("div", { class: "seg", role: "group", "aria-label": "View" }, seg("list", "List", "list"), seg("cards", "Cards", "layout-grid"))),
        views.length === 0 ? emptyPanel() : shown.length === 0 ? h("div", { class: "empty" }, `No model matches "${query}".`) : view === "list" ? listTable(shown) : cardGrid(shown));
    }

    function sideBody(views) {
      return h("div", { class: "compact" }, views.map((v) => modelButton(`mrow ${v.name === selected() ? "on" : ""}`, v.name,
        mdot(v.name), h("span", { class: "mrow-main" }, h("span", { class: "nm trunc" }, v.name), h("span", { class: "sub" }, stateEl(v), v.access === "secure" ? h("span", { class: "acc secure" }, icon("lock", 11, "currentColor", 2), "Secure") : null)),
        h("span", { class: "tp mono" }, v.tps === null ? "—" : fmt(v.tps), h("small", {}, v.tps === null ? "" : "tok/s")))),
      h("button", { class: "addrow", type: "button", onClick: openAdd }, icon("plus", 14, "currentColor", 2), "Add a model"));
    }

    function railBody(views) {
      return h("div", { class: "rdots" }, views.map((v) => modelButton(`rdot ${v.name === selected() ? "on" : ""}`, v.name, h("i", { style: { "--c": colorOf(v.name) } }))),
        h("button", { class: "rdot plus", type: "button", title: "Add a model", "aria-label": "Add a model", onClick: (e) => { e.stopPropagation(); openAdd(); } }, icon("plus", 14, "currentColor", 2)));
    }

    function draw() {
      if (!blade.alive) return;
      const mode = blade.mode || "full";
      const refocus = document.activeElement && document.activeElement.matches && document.activeElement.matches(".b-home .filter input");
      if (store.error && !store.models) { heading([], mode); body.replaceChildren(mode === "full" ? downPanel() : h("div", { class: "empty" }, "Daemon not answering")); return; }
      if (store.models === null) { heading([], mode); body.replaceChildren(h("div", { class: "empty" }, "Loading…")); return; }
      const ctx = ctxNow();
      const views = rows().map((m) => modelView(m, ctx));
      heading(views, mode);
      const next = mode === "rail" ? railBody(views) : mode === "full" || mode === "narrow" ? fullBody(views) : sideBody(views);
      body.replaceChildren(store.error && mode === "full" ? h("div", { class: "stale mono", role: "status" }, "daemon not answering: showing the last numbers", next) : next);
      if (refocus) {
        const input = body.querySelector(".filter input");
        if (input) { input.focus(); input.setSelectionRange(input.value.length, input.value.length); }
      }
    }
    blade.redraw = draw;
    blade.onMode = draw;
    blade.sync = () => body.querySelectorAll("[data-id]").forEach((el) => el.classList.toggle(el.matches("tr") ? "on" : el.matches(".card") ? "sel" : "on", el.dataset.id === selected()));
    const off = onData(draw);
    blade.onClose = off;
    draw();
  },
}));
