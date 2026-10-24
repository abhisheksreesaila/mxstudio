// omxconsole, the model page (F-053, ADR 0005): one page per model at #/models/<name>/<tab>. A header with the tabs
// (a segmented control, a pill that slides) and the model's verbs; below it the tab. Overview is built here; Benchmark, Logs and
// Notebook are the existing blade bodies mounted inside the page (their own actions, like Run benchmark, stay in them).
// Data: the polled store (models, metrics, series, info) + /admin/system crew. Everything is updated in place, so focus,
// hover and the 3 s poll never rebuild the page, and switching model or tab never reloads it.
import { h, s, icon, store, onData, fmt, dur, colorOf, api, refresh, toast, copyText, reducedMotion } from "./core.js";
import { register, makeBlade, truncate } from "./blades.js";
import { go } from "./pages.js";
import { tabRoute } from "./routes.js";
import { modelView } from "./blade-home.js";
import { hardwareView } from "./hardware.js";
import { system } from "./system-data.js";

export const MORPH_MS = 220;
const HUE = { start: "var(--ok)", stop: "var(--warn)", restart: "var(--warn)", copy: "var(--accent)" };
/** The tabs, in order. `blade` is the blade body a tab shows (Overview is built in this file). */
export const TAB_DEFS = [
  { key: "overview", label: "Overview", icon: "box", hue: "var(--hue-notebook)" },
  { key: "benchmark", label: "Benchmark", icon: "gauge", hue: "var(--hue-bench)", blade: "bench" },
  { key: "logs", label: "Logs", icon: "scroll-text", hue: "var(--hue-logs)", blade: "logs" },
  { key: "notebook", label: "Notebook", icon: "book-open", hue: "var(--hue-notebook)", blade: "notebook" },
];
const SOURCE = { hf: "Hugging Face", max: "Modular MAX catalog" };

// -- pure ------------------------------------------------------------------------------------------
/** The mxc command that does what a button does (every action shows it). */
export function cliTwin(action, name) {
  return ({ start: `mxc serve ${name}`, stop: `mxc stop ${name}`, restart: `mxc stop ${name} && mxc serve ${name}`, rm: `mxc rm ${name}`,
    secure: "mxc expose", bench: "mxc bench", notebook: `mxc learn ${name}`, logs: `mxc logs ${name}`, copy: "", security: "mxc expose" })[action] ?? "";
}

/** The command bar for a state ("ready" | "starting" | "cold" | "crashed"): the model's verbs and nothing else. Navigation is the
 *  tabs; Run benchmark and Open in Jupyter live inside their tabs. Remove is the danger one, an icon, last. */
export function commandsFor(state) {
  const c = (key, label, ic, hue, extra = {}) => ({ key, label, icon: ic, hue: hue || null, danger: false, iconOnly: false, primary: false, ...extra });
  const power = state === "ready" ? [c("stop", "Stop", "square", HUE.stop), c("restart", "Restart", "rotate-cw", HUE.restart)]
    : state === "starting" ? [c("stop", "Stop", "square", HUE.stop)]
    : state === "crashed" ? [c("restart", "Restart", "rotate-cw", HUE.restart)]
    : [c("start", "Start", "play", HUE.start)];
  power[0].primary = true;  // one filled verb per command bar: the power one
  return [...power, c("copy", "Copy endpoint", "copy", HUE.copy), c("rm", "Remove", "trash-2", null, { danger: true, iconOnly: true })];
}

/** The four "Right now" tiles, always in this order. value null = nothing to show ("—"). */
export function metricsView(v) {
  const t = v.tokens;
  return [{ label: "Requests", value: t.requests || 0, unit: "", digits: 0, hint: "last 5 minutes" },
    { label: "Speed", value: t.tps, unit: "tok/s", digits: 0, hint: "decode, median" },
    { label: "Latency p50", value: t.ttft, unit: "ms", digits: 0, hint: "time to first token" },
    { label: "Memory", value: v.fit.thisGb || null, unit: "GB", digits: 1, hint: "GPU, this model" }];
}

/** The tab a key moves to (Left/Right wrap, Home/End jump), or null for any other key. Pure. */
export function tabKeyTarget(key, keys, current) {
  const i = keys.indexOf(current);
  if (key === "ArrowRight") return keys[(i + 1) % keys.length];
  if (key === "ArrowLeft") return keys[(i - 1 + keys.length) % keys.length];
  if (key === "Home") return keys[0];
  if (key === "End") return keys[keys.length - 1];
  return null;
}

/** What the Remove button's accessible name is: after the first click it says what the second one does. */
export const removeLabel = (name, armed) => (armed ? `Click again to remove ${name}` : "Remove");

/** The tab strip: a tablist with a roving tabindex (only the selected tab is a Tab stop; arrows, Home and End move and activate).
 *  Each tab controls the one panel `<prefix>-panel`. onPick(key) is called when a key or click picks a tab. */
export function tabList(defs, { prefix, onPick }) {
  const keys = defs.map((d) => d.key);
  const buttons = new Map(defs.map((d) => [d.key, h("button", { type: "button", role: "tab", id: `${prefix}-tab-${d.key}`, "aria-controls": `${prefix}-panel`, "data-tab": d.key, tabindex: "-1", "aria-selected": "false",
    style: { "--c": d.hue }, onClick: () => onPick(d.key) }, icon(d.icon, 15, "currentColor", 1.75), d.label)]));
  const pill = h("i", { class: "pill" });
  const el = h("div", { class: "tabs", role: "tablist", "aria-label": "Model sections" }, pill, ...buttons.values());
  let selected = null;
  function select(key) {
    selected = key;
    buttons.forEach((b, k) => { b.classList.toggle("on", k === key); b.setAttribute("aria-selected", String(k === key)); b.setAttribute("tabindex", k === key ? "0" : "-1"); });
  }
  buttons.forEach((b) => b.addEventListener("keydown", (e) => {
    const to = tabKeyTarget(e.key, keys, selected);
    if (!to) return;
    e.preventDefault();
    select(to);
    buttons.get(to).focus();
    onPick(to);
  }));
  return { el, pill, buttons, select, selected: () => selected };
}

/** The content under the tabs for Benchmark, Logs and Notebook: one blade body at a time. Showing another (or none) tears the
 *  old one down: alive = false and its onClose, so pollers and listeners stop. build(bladeName, model) -> {blade, def}. */
export function subHost(el, build) {
  let sub = null;
  function close() {
    if (!sub) return;
    sub.alive = false;
    try { if (sub.onClose) sub.onClose(); } catch (e) { console.error("model tab failed to close cleanly", e); }
    sub.el.remove();
    sub = null;
  }
  function show(bladeName, model) {
    close();
    if (!bladeName) return;
    const made = build(bladeName, model);
    sub = made.blade;
    sub.el.classList.add("embed");
    sub.mode = "full";
    el.replaceChildren(sub.el);
    if (made.def.mount) made.def.mount(sub.body, sub);
  }
  return { show, close };
}

/** What the Tokens card says when there is nothing to chart yet: one true line per state (running, starting, idle,
 *  crashed), and never "no traffic" beside a request count. */
export function quietNote(state, requests) {
  const seen = requests > 0 ? `${requests} requests in the last 5 min` : "";
  if (state === "crashed")
    return { title: "It stopped", text: `${seen ? `${seen} before it stopped. ` : ""}Restart it, or open Logs to see why.` };
  if (state === "starting")
    return { title: "Starting up", text: `${seen ? `${seen}. ` : ""}It answers as soon as the graph is compiled and loaded.` };
  if (state === "running")
    return seen ? { title: "Warming up the chart", text: `${seen}; the chart fills in as more arrive.` }
      : { title: "No traffic yet", text: "It is loaded and waiting: call it and the numbers fill in." };
  return seen ? { title: "Not loaded right now", text: `${seen}; it loads again on the next one.` }
    : { title: "No traffic yet", text: "Start it, or call it: it starts on the first request." };
}

/** The curl that calls this model. The key is read from $MXC_API_KEY, never shown. */
export function curlFor(url, name) {
  return `curl ${url}/chat/completions \\\n  -H "Authorization: Bearer $MXC_API_KEY" \\\n  -H "Content-Type: application/json" \\\n  -d '{"model":"${name}","messages":[{"role":"user","content":"hi"}]}'`;
}

/** This model's VRAM against the others and the card. totalGb is null without a GPU. */
export function fitView(name, hw, crew) {
  const rows = (crew || []).filter((c) => /^model:/.test(c.id || "") && c.vram_gb > 0).map((c) => ({ name: c.id.slice(6), gb: Math.round(c.vram_gb * 10) / 10 }));
  const mine = rows.find((r) => r.name === name);
  const others = rows.filter((r) => r.name !== name);
  const total = hw.present ? hw.totalGb : null;
  const thisGb = mine ? mine.gb : 0;
  const othersGb = Math.round(others.reduce((a, r) => a + r.gb, 0) * 10) / 10;
  const pct = (gb) => (total ? (gb / total) * 100 : 0);
  return { totalGb: total, thisGb, othersGb, freeGb: total === null ? null : Math.round((total - hw.usedGb) * 10) / 10,
    thisPct: pct(thisGb), others: others.map((r) => ({ ...r, pct: pct(r.gb) })) };
}

/** Everything the Overview shows for one model. ctx: {metrics, series, info, system(crew), endpoint}. */
export function overviewView(m, ctx) {
  const v = modelView(m, ctx);
  const hw = hardwareView(ctx.metrics && ctx.metrics.gpus, ctx.system);
  const fit = fitView(m.name, hw, ctx.system);
  const secure = v.access === "secure";
  const url = v.endpoint;
  const unload = m.pinned ? "never (pinned)" : m.keep_alive ? `after ${dur(m.keep_alive)} idle` : "—";
  const vram = fit.thisGb ? `${fit.thisGb} GB${fit.totalGb ? ` · ${Math.round(fit.thisPct)}% of ${fit.totalGb} GB` : ""}` : "—";
  return {
    name: m.name, state: v.stateKey, stateLabel: v.stateLabel, crashed: v.crashed, secure,
    essentials: [["Repository", v.repo || "—"], ["Architecture", v.arch], ["Source", SOURCE[m.source] || m.source || "—"], ["Unloads", unload],
      ["Endpoint", url], ["Access", secure ? "HTTPS on your LAN, with a key" : "localhost only"], ["GPU memory", vram], ["State", v.note ? `${v.stateLabel} · ${v.note}` : v.stateLabel]],
    tokens: { tps: v.tps, ttft: v.ttft, requests: v.requests, points: v.points },
    call: { url, secure, keyHint: "$MXC_API_KEY", curl: curlFor(url, m.name) },
    fit,
  };
}

// -- pieces ----------------------------------------------------------------------------------------
function areaChart(points, color) {
  const w = 600, hgt = 84, pts = points.length > 1 ? points : [0, 0];
  const hi = Math.max(...pts), lo = Math.min(...pts, 0);
  const xy = pts.map((v, i) => [(i / (pts.length - 1)) * w, hgt - 4 - ((v - lo) / (hi - lo || 1)) * (hgt - 10)]);
  const line = xy.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const live = points.length > 1;
  return s("svg", { class: "area", viewBox: `0 0 ${w} ${hgt}`, preserveAspectRatio: "none", role: "img", "aria-label": live ? "Tokens per second, last 5 minutes" : "No recent traffic" },
    live ? s("polygon", { points: `0,${hgt} ${line} ${w},${hgt}`, fill: color, opacity: "0.12" }) : null,
    s("polyline", { points: line, fill: "none", stroke: live ? color : "var(--faint)", "stroke-width": "1.8", "stroke-linejoin": "round", "stroke-linecap": "round", "vector-effect": "non-scaling-stroke" }));
}

/** Show a number in an element, morphing from what it showed (MORPH_MS) so a model swap or a poll never jumps. */
function morph(el, to, digits = 0) {
  const from = el._v;
  el._v = to;
  cancelAnimationFrame(el._raf);
  if (to === null || to === undefined) { el.textContent = "—"; return; }
  if (typeof from !== "number" || from === to || reducedMotion()) { el.textContent = fmt(to, digits); return; }
  const t0 = performance.now();
  const step = (now) => {
    const k = Math.min(1, (now - t0) / MORPH_MS);
    el.textContent = fmt(from + (to - from) * (1 - (1 - k) ** 3), digits);
    if (k < 1) el._raf = requestAnimationFrame(step);
  };
  el._raf = requestAnimationFrame(step);
}

const dd = () => h("dd", {});
/** A card: heading with its icon, body, and its `mxc` source line (the command that shows the same thing). */
const card = (title, ic, hue, source, ...kids) => h("section", { class: "cardx" }, h("h3", { style: { "--c": hue } }, icon(ic, 15, "currentColor", 1.75), title),
  ...kids, source ? h("div", { class: "src" }, source) : null);
const cliLine = (text) => h("div", { class: "cli mono" }, text);

register("model", (blade) => {
  let name = blade.opts.id, tab = blade.opts.tab || "overview";
  return {
    title: name, crumb: name, sub: "Model", icon: "box", hue: colorOf(name),
    mount(body) {
      blade.header.hidden = true;  // the crumb in the top bar already says which model this is
      blade.el.classList.add("model-page");
      const ctxNow = () => ({ metrics: store.metrics, series: store.series, info: store.info, system: system.crew, endpoint: (store.info && store.info.endpoint) || `${location.origin}/v1` });
      let essigPrev = "", crashSig = "", busy = false, confirmTimer = 0, barSig = "", gone = false, confirming = false, removing = false;
      const currentModel = () => (store.models || []).find((m) => m.name === name);
      const path = () => `/admin/models/${encodeURIComponent(name)}`;

      // -- skeleton, built once -----------------------------------------------------------------
      const tabsUi = tabList(TAB_DEFS, { prefix: "mp", onPick: (key) => go(tabRoute({ page: "model", model: name, tab }, key)) });
      const { el: tabs, pill, buttons: tabBtns } = tabsUi;
      const barHost = h("div", { class: "cmdbar", role: "toolbar", "aria-label": "Model actions" });
      const mhead = h("div", { class: "mhead" }, tabs, barHost);

      const crash = h("div", { class: "wide" });
      const tiles = Array.from({ length: 4 }, () => ({ label: h("small", {}), num: h("span", {}), unit: h("em", {}), cap: h("span", { class: "cap" }) }));
      const m4 = h("div", { class: "m4" }, tiles.map((t) => h("div", {}, t.label, h("b", {}, t.num, t.unit), t.cap)));
      const dds = Array.from({ length: 8 }, dd);
      const accessLink = h("a", { href: "#/security" }, "Security ›");
      const reach = h("dd", {});
      const chart = h("div", { class: "chart" });
      const about = h("dl", { class: "dl" }, ["Repository", "Architecture", "Source", "Unloads", "Endpoint", "Reachable", "GPU memory", "State"].flatMap((l, i) => [h("dt", {}, l), i === 5 ? reach : dds[i]]));
      const fitBar = h("div", { class: "fit" }), fitCap = h("div", { class: "fitk" });
      const urlTag = h("span", { class: "tag" }), urlText = h("span", { class: "u" });
      const urlRow = h("div", { class: "urlrow" }, urlTag, urlText, h("button", { class: "ib", type: "button", "aria-label": "Copy URL", title: "Copy URL", onClick: async () => toast((await copyText(urlText.textContent)) ? "Endpoint copied" : "Could not reach the clipboard") }, icon("copy", 14)));
      const curlText = h("pre", { class: "curl mono" });
      const copyCurl = h("button", { class: "cp", type: "button", onClick: async () => toast((await copyText(curlText.textContent)) ? "Copied" : "Could not reach the clipboard") }, "copy");
      const sec = h("div", { class: "sec" }, crash,
        card("Right now", "gauge", "var(--oq-data-1)", `mxc status`, m4),
        card("Tokens", "scroll-text", "var(--oq-data-1)", "mxc metrics --json", chart),
        card("About", "box", "var(--oq-data-2)", "mxc ls --json", about),
        card("GPU fit", "gauge", "var(--oq-data-3)", "", fitBar, fitCap),
        h("section", { class: "cardx wide" }, h("h3", { style: { "--c": "var(--oq-data-4)" } }, icon("terminal", 15, "currentColor", 1.75), "Call it"), urlRow,
          h("div", { class: "keyrow" }, h("span", { class: "klabel" }, "API key"), h("code", { class: "kval mono" }, "$MXC_API_KEY"), h("button", { class: "lnk", type: "button", onClick: () => go({ page: "keys" }) }, "manage keys")),
          h("div", { class: "code" }, copyCurl, curlText), h("div", { class: "src" }, "mxc keys new <app>")),
        h("div", { class: "twins wide" }));
      const twins = sec.querySelector(".twins");
      const overview = h("div", { class: "main2 ov" }, sec);
      const subHostEl = h("div", { class: "main2 subhost", hidden: true });
      const subs = subHost(subHostEl, (bladeName, model) => makeBlade(bladeName, { id: model }, false));
      const panel = h("div", { class: "tpanel", role: "tabpanel", id: "mp-panel" }, overview, subHostEl);
      body.replaceChildren(h("div", { class: "mpage" }, mhead, panel));

      // -- tabs ---------------------------------------------------------------------------------
      function placePill(instant) {
        const on = tabBtns.get(tab);
        if (!on || !on.offsetWidth) return;
        if (instant) pill.style.transition = "none";
        pill.style.left = `${on.offsetLeft}px`;
        pill.style.width = `${on.offsetWidth}px`;
        if (instant) { void pill.offsetWidth; pill.style.transition = ""; }
      }
      function showTab() {
        tabsUi.select(tab);
        panel.setAttribute("aria-labelledby", `mp-tab-${tab}`);
        placePill(false);
        const def = TAB_DEFS.find((t) => t.key === tab);
        overview.hidden = Boolean(def.blade);
        subHostEl.hidden = !def.blade;
        subs.show(def.blade || null, name);
      }

      // -- actions -------------------------------------------------------------------------------
      async function act(label, fn, okMsg) {
        if (busy) return;
        busy = true; paintBar();
        try { await fn(); toast(okMsg); } catch (e) { toast(`Could not ${label} ${name}: ${e.message}`); }
        busy = false;
        await refresh();
      }
      const post = (p) => () => api("POST", `${path()}/${p}`);
      const ACTIONS = {
        start: () => act("start", post("load"), `Starting ${name} · $ ${cliTwin("start", name)}`),
        stop: () => act("stop", post("stop"), `Stopped ${name} · $ ${cliTwin("stop", name)}`),
        restart: () => act("restart", async () => { await api("POST", `${path()}/stop`); await api("POST", `${path()}/load`); }, `Restarting ${name} · $ ${cliTwin("restart", name)}`),
        copy: async () => {
          const url = (store.info && store.info.lan_endpoint) || ctxNow().endpoint;
          toast((await copyText(url)) ? `Endpoint copied · ${url}` : "Could not reach the clipboard: select the text and copy it");
        },
        rm: () => removeModel(),
      };
      function removeModel() {
        if (!confirming) {  // the second click confirms
          confirming = true; paintConfirm();
          clearTimeout(confirmTimer);
          confirmTimer = setTimeout(() => { confirming = false; paintConfirm(); }, 5000);
          return;
        }
        clearTimeout(confirmTimer);
        confirming = false;
        removing = true; paintBar();
        api("DELETE", path()).then(() => {
          gone = true;
          truncate(1);
          toast(`Removed ${name} · $ ${cliTwin("rm", name)}`);
          refresh();
        }).catch((e) => { toast(`Could not remove ${name}: ${e.message}`); removing = false; paintBar(); paintConfirm(); });
      }
      function paintConfirm() {
        const btn = barHost.querySelector('[data-cmd="rm"]');
        if (!btn) return;
        const m = currentModel();
        const gb = m ? overviewView(m, ctxNow()).fit.thisGb : 0;
        btn.lastChild.textContent = confirming ? `Click again to remove${gb ? ` (${gb} GB)` : ""}` : "Remove";
        btn.setAttribute("aria-label", removeLabel(name, confirming));
        btn.classList.toggle("armed", confirming);
      }
      function paintBar() {
        barHost.querySelectorAll("button").forEach((b) => { b.disabled = busy || removing; });
      }
      function buildBar(cmds) {
        barHost.replaceChildren(...cmds.map((c) => h("button", {
          class: `fb${c.primary ? " fill" : ""}${c.danger ? " danger" : ""}${c.iconOnly ? " ic" : ""}${c.key === "copy" ? " cp" : ""}`, type: "button", "data-cmd": c.key, "aria-label": c.label,
          title: cliTwin(c.key, name) ? `$ ${cliTwin(c.key, name)}` : c.label, style: c.hue ? { "--c": c.hue } : null, onClick: () => ACTIONS[c.key](),
        }, icon(c.icon, 15, "currentColor", 1.75), h("span", { class: "t" }, c.label))));
        confirming = false;
      }

      // -- draw: update text and attributes in place; rebuild the command bar only when its set of buttons changes
      const blank = (msg) => {
        if (sec.dataset.empty === msg) return;
        sec.dataset.empty = msg;
        barHost.replaceChildren(); barSig = "";
        [...sec.children].forEach((c) => { c.hidden = c !== crash; });
        crash.hidden = false;
        crash.replaceChildren(h("div", { class: "empty" }, msg));
        crashSig = "";
      };
      function draw() {
        if (!blade.alive || gone) return;
        const m = currentModel();
        if (store.models === null || !m) {
          blank(store.error && store.models === null ? "Daemon not answering" : store.models === null ? "Loading…" : "This model is not registered any more.");
          return;
        }
        if (sec.dataset.empty) { delete sec.dataset.empty; [...sec.children].forEach((c) => { c.hidden = false; }); crash.replaceChildren(); crashSig = ""; }
        const v = overviewView(m, ctxNow());
        const cmds = commandsFor(m.state);
        const sig = cmds.map((c) => c.key).join();
        if (sig !== barSig) { barSig = sig; buildBar(cmds); }
        paintBar(); paintConfirm();
        // the crashed callout (its buttons are focusable: rebuild only when the flag flips)
        if (v.crashed !== (crashSig === "1")) {
          crashSig = v.crashed ? "1" : "0";
          crash.replaceChildren(...(v.crashed ? [h("div", { class: "attn", role: "alert" }, icon("triangle-alert", 16), h("span", {}, h("b", { class: "mono" }, name), " crashed. Its log says why."), h("span", { class: "grow" }),
            h("button", { class: "fb", type: "button", onClick: () => go(tabRoute({ page: "model", model: name, tab }, "logs")) }, icon("scroll-text", 14), "View logs"),
            h("button", { class: "fb fill", type: "button", onClick: () => ACTIONS.restart() }, icon("rotate-cw", 14), "Restart"))] : []));
        }
        // Right now: the same four tiles, numbers morph
        metricsView(v).forEach((t, i) => {
          const el = tiles[i];
          el.label.textContent = t.label; el.cap.textContent = t.hint || "";
          morph(el.num, t.value, t.digits);
          el.unit.textContent = t.value === null || !t.unit ? "" : t.unit;
        });
        chart.replaceChildren(v.tokens.points.length > 1 ? areaChart(v.tokens.points, colorOf(name))
          : ((q) => h("div", { class: "nodata" }, h("b", {}, q.title), q.text))(quietNote(v.state, v.tokens.requests || 0)));
        // About
        const essSig = JSON.stringify(v.essentials);
        if (essSig !== essigPrev) {
          essigPrev = essSig;
          v.essentials.forEach(([, val], i) => { if (i !== 5) dds[i].replaceChildren(...(i === 7 ? [h("span", { class: `state ${v.state}` }, h("span", { class: "sd" }), val)] : [val])); });
          reach.replaceChildren(v.secure ? "LAN · HTTPS · " : "This machine only · ", accessLink);
        }
        urlRow.classList.toggle("secure", v.secure);
        urlTag.textContent = v.secure ? "https · lan" : "local";
        urlText.textContent = v.call.url;
        curlText.textContent = v.call.curl;
        const f = v.fit;
        fitBar.replaceChildren(...f.others.map((o) => h("i", { title: `${o.name} · ${o.gb} GB`, class: "o", style: { width: `${o.pct}%` } })), ...(f.thisGb ? [h("i", { title: `${name} · ${f.thisGb} GB`, style: { width: `${f.thisPct}%`, background: colorOf(name) } })] : []));
        fitCap.replaceChildren(h("span", {}, f.totalGb === null ? "no GPU found" : `this ${f.thisGb} GB`), h("span", {}, f.totalGb === null ? "" : `others ${f.othersGb} GB`), h("span", {}, f.totalGb === null ? "" : `free ${f.freeGb} GB of ${f.totalGb} GB`));
        const twin = [m.state === "ready" || m.state === "starting" ? cliTwin("stop", name) : cliTwin("start", name), cliTwin("rm", name)].join("|");
        if (twins.dataset.sig !== twin) { twins.dataset.sig = twin; twins.replaceChildren(...twin.split("|").map(cliLine)); }
      }

      // -- the page changes in place: pages.js calls this for every model route after the first
      blade.go = (r) => {
        const swapped = r.model !== name;
        name = r.model;
        const tabChanged = r.tab !== tab;
        tab = r.tab;
        if (swapped) {
          clearTimeout(confirmTimer);
          essigPrev = ""; crashSig = ""; barSig = ""; confirming = false; removing = false; busy = false;
          blade.crumb = name;
          blade.el.setAttribute("aria-label", name);
          draw();
        }
        if (swapped || tabChanged) showTab();
      };
      const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(() => placePill(true)) : null;
      if (ro) ro.observe(tabs);
      const off = onData(draw);
      blade.onClose = () => { off(); clearTimeout(confirmTimer); subs.close(); if (ro) ro.disconnect(); };
      draw();
      showTab();
      requestAnimationFrame(() => placePill(true));
    },
  };
});
