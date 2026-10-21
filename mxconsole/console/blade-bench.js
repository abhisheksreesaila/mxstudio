// mxconsole, the Benchmark blade: standard horizontal bars for decode tokens/s (higher is better) and time to first
// token (lower is better) across the models in the latest run, this model outlined; the workload; Run again in the
// background with progress; the history of past runs. Data: GET/POST /admin/bench, GET /admin/bench/history.
import { register, setHead } from "./blades.js";
import { h, icon, api, fmt, dateTime, colorOf, toast, reducedMotion, store } from "./core.js";

const POLL_RUNNING_MS = 1000, POLL_IDLE_MS = 5000; // idle too, so a run started from the CLI shows up
const RUNS = 5;

// -- pure helpers (tested under node) ------------------------------------------------------------
/** Bars for one metric. Only finished models with a number get a bar. The length is always value / largest value
 * (an honest zero baseline); `lowerIsBetter` only changes the sort (smallest first) and which row is the best. */
export function barRows(results, key, lowerIsBetter, me) {
  const rows = (results || []).filter((r) => r.status === "done" && r[key] > 0).map((r) => ({ model: r.model, value: r[key], estimated: !!r.estimated, older: !!r.older, at: r.at }));
  rows.sort((a, b) => (lowerIsBetter ? a.value - b.value : b.value - a.value) || a.model.localeCompare(b.model));
  const top = Math.max(0, ...rows.map((r) => r.value));
  return rows.map((r, i) => ({ ...r, fraction: r.value / top, best: i === 0, me: r.model === me }));
}
/** Every model's newest finished result across `runs` (newest run first), so a one-model "Run again" keeps the others'
 * bars. Rows that come from a run older than the newest one are marked `older` with that run's start time. `known`
 * (optional) limits the models to those still registered. */
export function mergeLatest(runs, known) {
  const seen = new Set(), out = [];
  const sorted = [...(runs || [])].sort((a, b) => b.started_at - a.started_at || b.id - a.id);
  const newest = sorted.length ? sorted[0].id : null;
  for (const run of sorted) {
    for (const r of run.results || []) {
      if (r.status !== "done" || !(r.decode_tps > 0) || seen.has(r.model) || (known && !known.includes(r.model))) continue;
      seen.add(r.model);
      out.push({ ...r, older: run.id !== newest, at: run.started_at });
    }
  }
  return out;
}
/** A self-rescheduling poll: `tick` is awaited, the next one runs `delay()` ms later; after stop() no tick runs, even one
 * already waiting on its timer or in flight. `timers` is injectable for tests. */
export function createPoller(tick, delay, timers = { set: (f, ms) => setTimeout(f, ms), clear: (id) => clearTimeout(id) }) {
  let alive = false, id = 0, gen = 0;
  const loop = async (mine) => {
    if (!alive || mine !== gen) return;
    try { await tick(); } catch (_) { /* the tick shows its own errors */ }
    if (alive && mine === gen) id = timers.set(() => loop(mine), delay());
  };
  return { start() { if (!alive) { alive = true; loop(++gen); } }, poke() { if (alive) { timers.clear(id); loop(++gen); } }, stop() { alive = false; gen++; timers.clear(id); } };
}
/** How full a model's progress bar is, by the state the daemon reports. */
export function progressPct(m, runs) {
  switch (m.state) {
    case "loading": return 4;
    case "warming": return 10;
    case "running": return 10 + 88 * Math.min(1, (m.done || 0) / Math.max(1, runs));
    case "done": case "failed": return 100;
    default: return 0;
  }
}
/** The workload in one honest line: what was sent, what came back, how often, when. */
export function workloadLine(run) {
  const done = (run.results || []).find((r) => r.status === "done" && r.completion_tokens);
  const parts = [`${fmt(run.prompt_tokens)} prompt tokens`, done ? `${fmt(done.completion_tokens)} tokens out` : `up to ${fmt(run.max_tokens)} tokens out`,
    "batch 1", `${run.runs} run${run.runs === 1 ? "" : "s"} each`, dateTime(run.started_at)];
  if ((run.results || []).some((r) => r.estimated)) parts.splice(4, 0, "token counts estimated");
  return parts.join(" · ");
}
const winner = (run) => (run.results || []).find((r) => r.status === "done" && r.decode_tps);

// -- pieces ----------------------------------------------------------------------------------------
const card = (title, badge, body, ...src) => h("section", { class: "card" },
  h("div", { class: "card-h" }, h("h3", {}, title), h("span", { class: "sp" }), badge ? h("span", { class: "badge" }, badge) : null), h("div", { class: "card-b" }, body), ...src);
const dot = (name) => h("span", { class: "bn-dot", style: { "--mc": colorOf(name) } });
function barBlock(title, hint, rows, unit, digits, source) {
  return card(title, hint, h("div", { class: "bn-bars" }, rows.map((r) => h("div", { class: `bn-row${r.me ? " me" : ""}${r.best ? " best" : ""}` },
      h("span", { class: "bn-nm" }, dot(r.model), h("span", { class: "bn-who" }, h("span", { class: "trunc" }, r.model),
        r.older ? h("span", { class: "bn-old trunc", title: "from an earlier run" }, dateTime(r.at)) : null)),
      h("span", { class: "prog bn-tr" }, h("i", { class: "bn-fill", "data-w": (r.fraction * 100).toFixed(1), style: { "--mc": colorOf(r.model) } })),
      h("span", { class: "bn-v mono" }, `${r.estimated ? "~" : ""}${fmt(r.value, digits)} ${unit}`)))),
    h("div", { class: "src note" }, source));
}
function setWidths(root, instant) {
  const fills = [...root.querySelectorAll(".bn-fill")];
  const apply = () => fills.forEach((f) => f.style.setProperty("width", `${f.dataset.w}%`));
  if (instant || reducedMotion()) return apply();
  requestAnimationFrame(() => requestAnimationFrame(apply));
}

register("bench", (blade) => {
  const id = blade.opts.id || "";
  let state = null, hist = [], failed = null, shownId = null, picked = false, starting = false, histLoaded = false, lastLatest = null, chartKey = "", histKey = "", progressKey = "";
  const rowEls = new Map();

  const runBtn = h("button", { class: "btn fill sm", type: "button" });
  const progressHost = h("div", {});
  const chartsHost = h("div", { class: "bn-charts" });
  const loadNote = h("div", { class: "ox-fail", role: "status", hidden: true });
  const histHost = h("div", {});
  const allBtn = h("button", { class: "btn tonal sm bn-all", type: "button" }, "Run all models");
  allBtn.addEventListener("click", () => start(true));
  const running = () => !!(state && state.running);

  const shownRun = () => {
    if (!state) return null;
    const all = [...(state.latest ? [state.latest] : []), ...hist];
    return (picked && all.find((r) => r.id === shownId)) || state.latest;
  };
  const cmd = (all) => (all || !id ? "mxc bench --all" : `mxc bench ${id}`);
  const cli = (text) => h("div", { class: "src" }, text);

  const drawButton = () => {
    const label = running() || starting ? "Running…" : state && state.latest ? "Run again" : "Run benchmark";
    runBtn.replaceChildren(icon(running() || starting ? "loader-circle" : "rotate-cw", 13), label);
    runBtn.disabled = running() || starting || !state;
    runBtn.title = id ? `Benchmark ${id} again in the background (${cmd()})` : "Benchmark every model in the background";
  };

  const drawProgress = () => {
    const p = running() ? state.progress : null;
    const key = p ? p.models.map((m) => m.name).join("|") : "";
    if (!p) { if (progressKey !== "") { progressHost.replaceChildren(); rowEls.clear(); progressKey = ""; } return; }
    if (key !== progressKey) { // the set of models changed: build the rows once, then update them in place
      progressKey = key; rowEls.clear();
      const rows = p.models.map((m) => {
        const fill = h("i", { class: "bn-pfill", style: { "--mc": colorOf(m.name) } });
        const word = h("span", { class: "bn-pword mono" });
        rowEls.set(m.name, { fill, word });
        return h("div", { class: "bn-prow" }, h("span", { class: "bn-nm" }, dot(m.name), h("span", { class: "trunc" }, m.name)), h("span", { class: "prog bn-ptr" }, fill), word);
      });
      progressHost.replaceChildren(h("div", { role: "status" }, card("Benchmark running", "one model at a time", h("div", { class: "bn-bars" }, rows), h("div", { class: "src note" }, `${p.runs} timed runs each, after one warm-up that is not counted`))));
    }
    for (const m of p.models) {
      const el = rowEls.get(m.name);
      if (!el) continue;
      el.fill.style.setProperty("width", `${progressPct(m, p.runs)}%`);
      const word = { queued: "waiting", loading: "loading (a cold model compiles first)", warming: "warm-up, not counted", running: `run ${Math.min(p.runs, m.done + 1)} of ${p.runs}`,
        done: `done · ${fmt(m.decode_tps, 1)} tok/s`, failed: m.error || "failed" }[m.state] || m.state;
      if (el.word.textContent !== word) el.word.textContent = word;
      el.word.classList.toggle("bad", m.state === "failed");
    }
  };

  const emptyState = () => h("section", { class: "card" },
    h("div", { class: "empty" }, h("div", { class: "ring" }, icon("gauge", 20)),
      h("b", {}, "No benchmark has run yet"),
      `A run loads ${id ? "the model" : "each model"} if it is not running, sends one warm-up request that is not counted, then ${RUNS} timed ones. It records decode tokens per second and time to first token.`,
      h("div", {}, h("button", { class: "btn fill sm", type: "button", disabled: !state || running() || starting, onClick: () => start(false) }, icon("gauge", 13), "Run benchmark"))),
    cli(cmd()));

  const drawCharts = () => {
    if (failed && !state) { chartsHost.replaceChildren(h("div", { class: "ox-fail", role: "alert" }, icon("triangle-alert", 15), `Could not load benchmarks: ${failed}`)); chartKey = ""; return; }
    if (!state) { chartsHost.replaceChildren(h("div", { class: "skel bn-skel" })); return; }
    const run = shownRun();
    const onLatest = !!run && !!state.latest && run.id === state.latest.id;
    const results = !run ? [] : onLatest ? mergeLatest([state.latest, ...hist.filter((r) => r.status === "done" || r.status === "failed")], store.models ? store.models.map((m) => m.name) : null) : run.results;
    const key = JSON.stringify([run && run.id, id, running(), results.map((r) => [r.model, r.status, r.decode_tps, r.ttft_p50_ms, r.at])]);
    if (key === chartKey) return; // idle polls leave the DOM alone
    chartKey = key;
    if (!run) { chartsHost.replaceChildren(running() ? h("div", {}) : emptyState()); return; }
    const decode = barRows(results, "decode_tps", false, id), ttft = barRows(results, "ttft_p50_ms", true, id);
    const failedRows = run.results.filter((r) => r.status === "failed");
    const missing = id && !results.some((r) => r.model === id);
    const only = decode.length === 1 && decode[0].me;
    const mixed = results.some((r) => r.older);
    chartsHost.replaceChildren(...[
      missing ? h("div", { class: "bn-note" }, `${id} has no finished result here. Run it with: ${cmd()}`) : null,
      decode.length ? barBlock("Decode speed · tokens/s", "higher is better", decode, "tok/s", 1, `${run.runs} timed runs per model, after one warm-up`) : h("div", { class: "bn-note" }, "No model finished this run, so there is nothing to chart."),
      ttft.length ? barBlock("Time to first token · p50", "lower is better", ttft, "ms", 0, `the median of ${run.runs} timed runs`) : null,
      only ? h("div", { class: "bn-note" }, "Only this model has a result. To compare it with the others, run all of them.", " ", allBtn) : null,
      failedRows.length ? h("div", { class: "bn-fails" }, failedRows.map((r) => h("div", { class: "ox-fail" }, icon("circle-alert", 14, "currentColor", 2), h("span", { class: "mono" }, r.model), `failed: ${r.error || "no result"}`))) : null,
      card(run.id === (state.latest && state.latest.id) ? "This run" : "An earlier run", "", [h("div", { class: "bn-work mono" }, workloadLine(run)),
        mixed ? h("div", { class: "ox-note" }, "Rows with a date come from an earlier run (each model's newest result). Run all models to refresh everyone.", " ", allBtn) : null,
        decode.some((r) => r.estimated) ? h("div", { class: "ox-note" }, "~ means MAX returned no token counts, so they are estimated at four characters per token.") : null], cli(cmd()))].filter(Boolean));
    setWidths(chartsHost, false);
    setHead(blade, { sub: `${id || "all models"} · ${run.id === (state.latest && state.latest.id) ? "latest run" : "an earlier run"} · ${dateTime(run.started_at)}` });
  };

  const drawHistory = () => {
    const rows = hist.filter((r) => r.id !== (state && state.progress && running() ? state.progress.id : -1));
    const key = JSON.stringify([rows.map((r) => [r.id, r.status]), shownId, picked]);
    if (key === histKey) return;
    histKey = key;
    if (!rows.length) { histHost.replaceChildren(); return; }
    const shown = shownRun();
    histHost.replaceChildren(h("section", { class: "card" }, h("div", { class: "card-h" }, h("h3", {}, "History"), h("span", { class: "sp" }), h("span", { class: "badge" }, `${rows.length} run${rows.length === 1 ? "" : "s"}`)),
      h("div", { class: "bn-hist" }, rows.map((r) => {
        const w = winner(r), ok = r.status === "done" || r.status === "failed";
        const btn = h("button", { class: `bn-hrow${shown && shown.id === r.id ? " on" : ""}`, type: "button", disabled: !ok },
          h("span", { class: "bn-hwhen" }, dateTime(r.started_at)),
          h("span", { class: "bn-hwho trunc" }, `${r.models.length} model${r.models.length === 1 ? "" : "s"}`),
          h("span", { class: "bn-hbest mono trunc" }, w ? `${w.model} · ${fmt(w.decode_tps, 1)} tok/s` : r.status === "running" ? "running" : r.status === "interrupted" ? "interrupted" : "no result"));
        btn.addEventListener("click", () => {
          picked = !(state.latest && state.latest.id === r.id); shownId = r.id; chartKey = ""; histKey = ""; draw();
        });
        return btn;
      })), h("div", { class: "src note" }, "pick a run to chart it; newest first")));
  };

  const draw = () => {
    drawButton(); drawProgress(); drawCharts(); drawHistory();
    loadNote.hidden = !failed || !state;
    if (failed && state) loadNote.textContent = `Could not refresh: ${failed}`;
  };

  const load = async () => {
    try {
      const now = await api("GET", "/admin/bench");
      const justFinished = running() && !now.running;
      const latestId = now.latest ? now.latest.id : null;
      state = now; failed = null;
      if (!histLoaded || justFinished || latestId !== lastLatest) { lastLatest = latestId; hist = await api("GET", "/admin/bench/history?limit=10"); histLoaded = true; }
      if (justFinished) {
        picked = false;
        toast(now.latest && now.latest.status === "done" ? "Benchmark finished." : "Benchmark finished: every model failed.", 4000);
      }
    } catch (e) { failed = e.message; }
    if (!blade.alive) return;
    draw();
  };
  const poller = createPoller(load, () => (running() ? POLL_RUNNING_MS : POLL_IDLE_MS));

  const start = async (all) => {
    if (running() || starting) return;
    starting = true; drawButton();
    try {
      await api("POST", "/admin/bench", { runs: RUNS, ...(id && !all ? { models: [id] } : {}) });
      picked = false;
    } catch (e) {
      if (e.status !== 409) toast(e.message, 5000); // 409: one is already running; just follow it
    }
    starting = false;
    if (blade.alive) poller.poke();
  };

  // opts.run: start as soon as the first read says nothing is running
  const startWhenLoaded = async () => {
    for (let i = 0; i < 50 && blade.alive && !state; i++) await new Promise((r) => setTimeout(r, 100));
    if (blade.alive && state && !running()) start(false);
  };
  runBtn.addEventListener("click", () => start(false));
  drawButton();

  return {
    title: "Benchmark",
    crumb: "Benchmark",
    sub: id,
    icon: "gauge", hue: "var(--hue-bench)", side: 600, min: 520,
    tools: runBtn,
    mount(body) {
      blade.el.classList.add("oq-flat");
      body.replaceChildren(h("div", { class: "bn ox-pad" }, progressHost, loadNote, chartsHost, histHost));
      blade.onClose = () => poller.stop();
      poller.start();
      if (blade.opts.run) startWhenLoaded();
    },
  };
});
