// omxconsole, the Beacon: one dark pill at the top right that says the one thing happening now.
// beaconState() is the priority and is pure (data in, a plain object out); mountBeacon() draws it.
// Priority: a pull or port in progress > a crashed model > running models (live tok/s) > GPU memory in use (idle).
// Its actions only use what the console already has: a hash route (#model/<name>, #logs/<name>, #add) or an admin POST.
import { h, s, icon, api, store, onData, refresh } from "./core.js";
import { hardwareView } from "./hardware.js";
import { system } from "./system-data.js";

const ADD = "#add";
const SPEED = (bytesPerSecond) => (bytesPerSecond > 0 ? `${Math.round(bytesPerSecond / 1e5) / 10} MB/s` : "");
const lastTps = (series, name) => {
  const points = ((series && series.models && series.models[name]) || []).map((b) => b.decode_tps_p50).filter((v) => v !== null && v !== undefined);
  return points.length ? points[points.length - 1] : 0;
};

function pullState(job) {
  const pct = job.percent === null || job.percent === undefined ? null : job.percent;
  const name = job.name || job.ref;
  return {
    kind: "pull", label: `Pulling ${name}`, value: pct === null ? "…" : `${Math.round(pct)}%`, pct, big: pct === null ? "…" : `${Math.round(pct)}%`,
    sub: [job.ref, SPEED(job.speed)].filter(Boolean).join(" · "),
    steps: (job.steps || []).map((st) => ({ state: st.state === "done" ? "ok" : st.state === "now" ? "run" : "next", label: st.label, detail: st.detail || "" })),
    actions: [{ label: "Open pull", go: ADD, primary: true }, { label: "Cancel", post: `/admin/pulls/${job.id}/cancel`, confirm: "Tap again to cancel" }],
  };
}

function portState(job) {
  const seen = new Map();
  for (const st of job.stages || []) seen.set(st.name, st); // a retried stage keeps its newest row
  const stages = [...seen.values()];
  const done = stages.filter((st) => st.status === "done" || st.status === "skipped").length;
  return {
    kind: "port", label: `Porting ${job.name || job.ref}`, value: `step ${Math.min(done + 1, stages.length)} of ${stages.length}`,
    pct: stages.length ? (done / stages.length) * 100 : null, sub: job.ref,
    steps: stages.map((st) => ({ state: st.status === "done" || st.status === "skipped" ? "ok" : st.status === "running" ? "run" : "next", label: st.name, detail: st.detail || "" })),
    actions: [{ label: "Open port", go: ADD, primary: true }, { label: "Cancel", post: `/admin/ports/${job.id}/cancel`, confirm: "Tap again to cancel" }],
  };
}

/** d: {models (/admin/models), pulls, ports (the .pulls/.ports lists), gpus (/admin/metrics), crew (/admin/system), series (/admin/series), error}.
 *  Returns {kind, label, value, pct?, big?, sub, steps: [{state: ok|run|next, label, detail}], actions: [{label, go|post, primary?}]}. */
export function beaconState(d) {
  const pull = (d.pulls || []).find((j) => j.status === "running");
  if (pull) return pullState(pull);
  const port = (d.ports || []).find((j) => j.status === "running");
  if (port) return portState(port);
  const models = d.models || [];
  const crashed = models.find((m) => m.state === "crashed");
  if (crashed) {
    const n = crashed.name;
    return { kind: "crash", label: `${n} crashed`, value: "", sub: "it stopped on its own; the log says why", steps: [],
      actions: [{ label: `Open ${n}`, go: `#model/${n}`, primary: true }, { label: "Restart", post: `/admin/models/${n}/load` }, { label: "View logs", go: `#logs/${n}` }] };
  }
  const starting = models.find((m) => m.state === "starting");
  if (starting) {
    const n = starting.name;
    return { kind: "starting", label: `Starting ${n}`, value: "", sub: "compiling the graph and loading the weights; the first start takes a while", steps: [],
      actions: [{ label: `Open ${n}`, go: `#model/${n}`, primary: true }, { label: "View logs", go: `#logs/${n}` }] };
  }
  const hw = hardwareView(d.gpus, d.crew);
  const running = models.filter((m) => m.state === "ready");
  if (running.length) {
    const tps = running.map((m) => ({ m, tps: Math.round(lastTps(d.series, m.name)) }));
    const gb = (name) => { const c = (d.crew || []).find((x) => x.id === `model:${name}`); return c && c.vram_gb > 0 ? ` · ${Math.round(c.vram_gb * 10) / 10} GB` : ""; };
    return { kind: "running", label: `${running.length} running`, value: `${tps.reduce((a, r) => a + r.tps, 0)} tok/s`, pct: null,
      sub: hw.present ? `${hw.usedGb} of ${hw.totalGb} GB · ${hw.short}` : "",
      steps: tps.map((r) => ({ state: "ok", label: r.m.name, detail: `${r.tps} tok/s${gb(r.m.name)}` })),
      actions: [{ label: `Open ${running[0].name}`, go: `#model/${running[0].name}`, primary: true }] };
  }
  const add = [{ label: "Add a model", go: ADD, primary: true }];
  if (d.error) return { kind: "offline", label: "daemon offline", value: "", sub: "the console cannot reach the daemon; run mxc daemon", steps: [], actions: [] };
  if (!d.gpus) return { kind: "loading", label: "…", value: "", sub: "", steps: [], actions: [] };
  if (!hw.present) return { kind: "nogpu", label: "No GPU", value: "", sub: "models run on the CPU, or not at all", steps: [], actions: add };
  return { kind: "idle", label: hw.short, value: hw.label, big: `${hw.usedGb} GB`, pct: (d.gpus[0].used_mb / d.gpus[0].total_mb) * 100,
    sub: models.length ? "nothing running · models start on the first request" : "no models yet", steps: [], actions: add };
}

// -- the page part ---------------------------------------------------------------------------------
const POLL_MS = 3000;
const jobs = { pulls: [], ports: [] };

async function pollJobs() {
  // a failed poll keeps what we last knew: a blip must not flip "Pulling 62%" to idle and back (store.error drives "offline")
  try { jobs.pulls = (await api("GET", "/admin/pulls")).pulls || []; } catch (_) { /* keep the last value */ }
  try { jobs.ports = (await api("GET", "/admin/ports")).ports || []; } catch (_) { /* keep the last value */ }
}

function ring(pct, warn) {
  const frac = pct === null || pct === undefined ? 0.25 : Math.max(0, Math.min(1, pct / 100));
  return s("svg", { class: "ring", viewBox: "0 0 20 20" },
    s("circle", { cx: 10, cy: 10, r: 8, fill: "none", "stroke-width": 2.5, class: "ring-track" }),
    s("circle", { cx: 10, cy: 10, r: 8, fill: "none", "stroke-width": 2.5, class: warn ? "ring-arc warn" : "ring-arc", "stroke-dasharray": 50.3, "stroke-dashoffset": 50.3 * (1 - frac), transform: "rotate(-90 10 10)" }));
}
const KIND_ICON = { pull: "download", port: "wrench", crash: "circle-alert", starting: "loader-circle", running: "zap", idle: "cpu", nogpu: "cpu", offline: "circle-alert", loading: "cpu" };

function compact(st) {
  const lead = st.kind === "pull" || st.kind === "port" ? ring(st.pct, st.kind === "port")
    : st.kind === "running" || st.kind === "crash" ? h("span", { class: st.kind === "crash" ? "live bad" : "live" })
    : icon(KIND_ICON[st.kind], 14, "currentColor", 1.75);
  return [lead, h("span", { class: "bc-lb" }, st.label), st.value ? h("span", { class: "mono" }, st.value) : null];
}

// Cancel throws work away, so it asks for a second tap within 3 s (armed survives redraws while the percent ticks)
let armed = null, armedTimer = null;
function arm(post, redraw) { armed = post; clearTimeout(armedTimer); armedTimer = setTimeout(() => { armed = null; redraw(); }, 3000); redraw(); }

function details(st, close, redraw) {
  return [
    h("h4", {}, icon(KIND_ICON[st.kind], 18, "currentColor", 1.75), st.label),
    st.sub ? h("div", { class: "sub2" }, st.sub) : null,
    st.big ? h("div", { class: "big" }, st.big) : null,
    st.pct !== null && st.pct !== undefined ? h("div", { class: "bar2" }, h("i", { style: { width: `${Math.max(0, Math.min(100, st.pct))}%` } })) : null,
    st.steps.length ? h("ul", { class: "steps" }, st.steps.map((x) => h("li", {}, h("i", { class: `d ${x.state === "next" ? "" : x.state}` }), x.label, x.detail ? h("span", {}, x.detail) : null))) : null,
    st.actions.length ? h("div", { class: "bacts" }, st.actions.map((a) => h("button", { type: "button", class: `bbtn${a.primary ? " pri" : ""}${a.confirm && armed === a.post ? " armed" : ""}`,
      onClick: () => {
        if (a.confirm && armed !== a.post) return arm(a.post, redraw);
        armed = null; close(); if (a.go) location.hash = a.go; else api("POST", a.post).then(refresh, refresh);
      } }, a.confirm && armed === a.post ? a.confirm : a.label))) : null,
  ];
}

/** Mounts the Beacon into `host` (the element that holds it, positioned by topbar.css) and returns {el, close, isOpen}. */
export function mountBeacon(host, onSize) {
  const root = h("div", { class: "beacon", id: "beacon" });
  const pill = h("button", { class: "bc-compact", type: "button", "aria-expanded": "false", "aria-controls": "bcO", onClick: () => setOpen(true) });
  const panel = h("div", { class: "bc-open", id: "bcO", role: "region", "aria-label": "Now" });
  root.append(pill, panel);
  host.append(root);
  let open = false, st = null, shown = "";

  const size = (animate) => { if (window.Omxqua) window.Omxqua.sizeBeacon(root, animate); onSize(pill.offsetWidth); };
  const close = () => setOpen(false);
  function setOpen(v) {
    open = v;
    root.classList.toggle("open", v);
    pill.setAttribute("aria-expanded", String(v));
    pill.inert = v; panel.inert = !v;
    size(true);
    if (v) panel.querySelector(".bbtn, .x")?.focus({ preventScroll: true });
  }
  function draw() {
    st = beaconState({ models: store.models, pulls: jobs.pulls, ports: jobs.ports, gpus: store.metrics && store.metrics.gpus, crew: system.crew, series: store.series, error: store.error });
    const key = JSON.stringify(st) + (armed || "");
    if (key === shown) return;
    shown = key;
    root.dataset.kind = st.kind;
    pill.replaceChildren(...compact(st).filter(Boolean));
    panel.replaceChildren(h("button", { class: "x", type: "button", "aria-label": "Close", onClick: close }, icon("x", 16, "currentColor", 2)), ...details(st, close, draw).filter(Boolean));
    size(open);
  }
  document.addEventListener("click", (e) => { if (open && !root.contains(e.target)) close(); });
  // capture phase: Esc closes the Beacon first, and only then the newest blade
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && open) { e.preventDefault(); e.stopImmediatePropagation(); close(); pill.focus(); } }, true);
  window.addEventListener("resize", () => size(false));
  panel.inert = true;
  onData(draw);
  // Mounted once for the page's life, so the loop never needs stopping. Each poll waits for the last one to
  // finish (a slow daemon never stacks requests) and a hidden tab skips polling until it is shown again.
  const loop = async () => { try { if (!document.hidden) { await pollJobs(); draw(); } } finally { setTimeout(loop, POLL_MS); } };
  loop();
  draw();
  return { el: root, close, isOpen: () => open };
}
