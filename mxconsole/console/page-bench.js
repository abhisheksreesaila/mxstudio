// Benchmarks: the decode drag race, the top-speed gauge, the results table and the run history.
// Everything shown comes from a real run (`mxc bench`); before the first run there is only an empty state.
import { h, s, icon, api, fmt, dateTime, colorOf, HUE, store, assignColours, toast, tick, reducedMotion } from "./core.js";

const CAR_W = 110;
const RACE_MS = 3200, NEEDLE_MS = 2400;
const SWEEP = 240; // the gauge spans -120 to +120 degrees
const ARC = 356;   // length of the gauge arc (radius 85 over 240 degrees)

// -- pure helpers (tested under node) ------------------------------------------------------------
export const ordinal = (n) => `${n}${["th", "st", "nd", "rd"][n % 100 >= 11 && n % 100 <= 13 ? 0 : Math.min(n % 10, 4) % 4]}`;

/** Where each car stops, as 0..1 of the track: its decode speed relative to the fastest. */
export function placements(results) {
  const racers = results.filter((r) => r.status === "done" && r.decode_tps > 0);
  const top = Math.max(0, ...racers.map((r) => r.decode_tps));
  return racers.map((r, i) => ({ model: r.model, tps: r.decode_tps, fraction: r.decode_tps / top, place: i + 1 }));
}

/** The gauge scale: the smallest round number above the top speed. */
export function niceMax(top) {
  for (const m of [25, 50, 100, 150, 200, 250, 300, 400, 500, 750, 1000, 1500, 2000, 3000, 5000, 10000]) if (top * 1.05 <= m) return m;
  return Math.ceil((top * 1.05) / 1000) * 1000;
}
export const needleAngle = (top, max) => -SWEEP / 2 + SWEEP * Math.min(1, top / max);

// -- the engine sound ------------------------------------------------------------------------------
// Synthesized with Web Audio (no audio file): rev, shift, rev again. Only ever called from a click.
export function vroom() {
  const Ctx = window.AudioContext || window.webkitAudioContext;
  if (!Ctx) return;
  const ac = new Ctx(), now = ac.currentTime;
  const engine = ac.createOscillator(), growl = ac.createOscillator();
  engine.type = "sawtooth"; growl.type = "square";
  const filter = ac.createBiquadFilter(); filter.type = "lowpass"; filter.frequency.value = 1100; filter.Q.value = 6;
  const gain = ac.createGain();
  const rev = (osc, k) => {
    osc.frequency.setValueAtTime(48 * k, now);
    osc.frequency.exponentialRampToValueAtTime(210 * k, now + 1.1);
    osc.frequency.exponentialRampToValueAtTime(120 * k, now + 1.3);
    osc.frequency.exponentialRampToValueAtTime(300 * k, now + 2.6);
  };
  rev(engine, 1); rev(growl, 0.5);
  gain.gain.setValueAtTime(0.0001, now);
  gain.gain.exponentialRampToValueAtTime(0.22, now + 0.08);
  gain.gain.setValueAtTime(0.22, now + 2.5);
  gain.gain.exponentialRampToValueAtTime(0.0001, now + 3.2);
  engine.connect(filter); growl.connect(filter); filter.connect(gain); gain.connect(ac.destination);
  engine.start(now); growl.start(now); engine.stop(now + 3.3); growl.stop(now + 3.3);
  setTimeout(() => ac.close(), 3600);
}

// -- starting a run (the page button and the ⌘K palette both call this) -----------------------------
export async function startBench() {
  try {
    const started = await api("POST", "/admin/bench", { runs: 5 });
    toast(`Benchmark started: ${started.models.length} model${started.models.length === 1 ? "" : "s"}, ${started.runs} runs each. The first load of a cold model takes a while.`, 6000);
    window.dispatchEvent(new Event("mxc-bench-started"));
    return true;
  } catch (e) {
    toast(`Could not start the benchmark: ${e.message}`, 7000);
    return false;
  }
}

// -- drawing -------------------------------------------------------------------------------------
function car(color) {
  return s("svg", { viewBox: "0 0 120 50", width: CAR_W, height: 46, "aria-hidden": "true", class: "car-svg" },
    s("path", { d: "M 8 34 L 14 22 Q 18 16 30 15 L 58 13 Q 70 12 80 18 L 96 24 Q 110 26 112 32 L 112 36 L 8 36 Z", fill: color }),
    s("path", { d: "M 36 17 L 56 15 Q 66 15 74 20 L 36 21 Z", fill: "#ffffff", opacity: "0.7" }),
    s("circle", { cx: 30, cy: 37, r: 8, fill: "#18181b" }), s("circle", { cx: 30, cy: 37, r: 3, fill: "#a1a1aa" }),
    s("circle", { cx: 92, cy: 37, r: 8, fill: "#18181b" }), s("circle", { cx: 92, cy: 37, r: 3, fill: "#a1a1aa" }));
}

function gauge(top, max, model) {
  const ticks = Array.from({ length: 11 }, (_, k) => s("line", { x1: 100, y1: 22, x2: 100, y2: k % 2 ? 30 : 36, class: "tick", "stroke-width": 2, transform: `rotate(${-120 + k * 24} 100 100)` }));
  const frac = Math.min(1, top / max);
  const arcPath = "M 28 130 A 85 85 0 1 1 172 130";
  const fill = s("path", { d: arcPath, fill: "none", stroke: colorOf(model), "stroke-width": 14, "stroke-linecap": "round", "stroke-dasharray": `${ARC * frac} 400`, class: "gauge-fill" });
  const needle = h("div", { class: "needle" });
  const num = h("span", { class: "num n" }, "0");
  const el = h("div", { class: "gauge" },
    s("svg", { viewBox: "0 0 200 150", width: 200, height: 150, "aria-hidden": "true" },
      s("path", { d: arcPath, fill: "none", class: "gauge-track", "stroke-width": 14, "stroke-linecap": "round" }), fill, ticks),
    needle,
    h("div", { class: "read" }, num, h("span", { class: "u" }, " tok/s")),
    h("span", { class: "lo mono" }, "0"), h("span", { class: "hi mono" }, fmt(max)));
  return { el, needle, num, fill, angle: needleAngle(top, max), top };
}

function racePanel(run, onRace) {
  const racers = placements(run.results);
  const failed = run.results.filter((r) => r.status === "failed");
  const done = run.results.find((r) => r.status === "done");
  const tokens = done ? `${done.estimated ? "~" : ""}${fmt(done.prompt_tokens)}` : `~${fmt(run.prompt_tokens)}`;
  const workload = `${tokens} in · ${run.max_tokens} out · batch 1 · ${run.runs} runs · ${dateTime(run.started_at)}`;
  const cars = [];
  const lanes = racers.map((r, i) => {
    const color = colorOf(r.model);
    const el = h("div", { class: "car", style: { "--c": color } }, car(color));
    cars.push({ el, fraction: r.fraction });
    return h("div", { class: "lane-r", "data-model": r.model },
      h("div", { class: "who mono" }, r.model),
      h("div", { class: "zone" }, el),
      h("div", { class: "res" }, h("span", { class: "num v" }, fmt(r.tps)), h("span", { class: "u" }, " tok/s"), h("div", { class: "pos mono" }, ordinal(r.place))));
  });
  const zoneProbe = h("div", { class: "finish", "aria-hidden": "true" });
  const track = h("div", { class: "track" }, zoneProbe, lanes);
  const btn = h("button", { class: "btn race-btn", type: "button", onClick: () => onRace(true) }, icon("zap", 14, "currentColor"), "Race again", h("span", { class: "snd" }, " · with sound"));
  const node = h("section", { class: "race" },
    h("div", { class: "race-head" }, icon("flag", 18, "#ffffff", 1.8), h("span", { class: "t" }, "Decode drag race"), h("span", { class: "mono w" }, workload), btn),
    racers.length ? track : h("div", { class: "race-none" }, "No model finished the run, so there is no race. The table below says why."),
    failed.length && racers.length ? h("div", { class: "dnf mono" }, "did not finish: ", failed.map((f) => `${f.model} (${f.error || "failed"})`).join(" · ")) : null);
  const top = racers.length ? racers[0].tps : 0;
  const g = racers.length ? gauge(top, niceMax(top), racers[0].model) : null;
  return { node, track, cars, gauge: g, racers };
}

function resultsTable(run) {
  const dot = (n) => h("span", { class: "stat-dot", style: { "--c": colorOf(n), background: colorOf(n) } });
  const est = run.results.some((r) => r.estimated);
  const head = ["Model", "Decode", "Prefill", "TTFT p50", "TTFT p95", "Load", "Runs"];
  const num = (v, d = 0, unit = "") => (v === null || v === undefined ? "—" : `${fmt(v, d)}${unit}`);
  const rows = run.results.map((r, i) => {
    const model = h("td", {}, h("span", { class: "who-cell" }, dot(r.model), h("span", { class: "mono" }, r.model), r.place ? h("span", { class: "place mono" }, ordinal(r.place)) : null));
    if (r.status === "failed") return h("tr", {}, model, h("td", { colspan: 6, class: "bad-text" }, r.error || "failed"));
    const strong = r.place === 1 ? "lead" : "";
    return h("tr", {}, model,
      h("td", { class: `r num ${strong}` }, num(r.decode_tps, r.decode_tps < 100 ? 1 : 0)), h("td", { class: "r num" }, num(r.prefill_tps)),
      h("td", { class: "r num" }, num(r.ttft_p50_ms, 0, " ms")), h("td", { class: "r num" }, num(r.ttft_p95_ms, 0, " ms")),
      h("td", { class: "r num" }, r.load_seconds === null ? h("span", { class: "muted" }, "warm") : `${fmt(r.load_seconds, 1)} s`),
      h("td", { class: "r num" }, `${r.runs_ok}/${r.runs_ok + r.errors}`));
  });
  return h("section", { class: "panel scroll-x bench-table" },
    h("table", { class: "grid" }, h("thead", {}, h("tr", {}, head.map((c, i) => h("th", { class: i ? "r" : "" }, i === 1 ? "Decode tok/s" : i === 2 ? "Prefill tok/s" : c)))), h("tbody", {}, rows)),
    h("div", { class: "foot mono" }, `decode = (tokens − 1) ÷ (duration − first token), median of ${run.runs} runs · prefill = prompt tokens ÷ first token · load is the cold start, not in TTFT${est ? " · tokens counted from streamed chunks (~)" : ""}`));
}

function historyTable(rows, shownId, onPick) {
  const fastest = (run) => { const r = run.results.find((x) => x.status === "done"); return r ? `${r.model} · ${fmt(r.decode_tps, r.decode_tps < 100 ? 1 : 0)} tok/s` : "—"; };
  const status = (run) => (run.status === "done" ? h("span", { class: "pill ok" }, "done") : run.status === "failed" ? h("span", { class: "pill bad" }, "all failed") : h("span", { class: "pill warn" }, run.status));
  return h("section", { class: "panel scroll-x", id: "bench-history" },
    h("table", { class: "grid" },
      h("thead", {}, h("tr", {}, ["When", "Models", "Workload", "Fastest", ""].map((c) => h("th", {}, c)))),
      h("tbody", {}, rows.map((run) => {
        const pickable = run.status === "done" || run.status === "failed";
        return h("tr", { class: pickable ? "pick" : "", "aria-selected": run.id === shownId ? "true" : "false", tabindex: pickable ? "0" : null,
          onClick: pickable ? () => onPick(run.id) : null, onKeydown: pickable ? (e) => { if (e.key === "Enter") onPick(run.id); } : null },
        h("td", { class: "mono" }, dateTime(run.started_at)), h("td", {}, h("span", { class: "mono" }, `${run.models.length} model${run.models.length === 1 ? "" : "s"}`), h("span", { class: "muted names" }, ` ${run.models.join(", ")}`)),
        h("td", { class: "mono" }, `${run.runs} runs · ${run.max_tokens} out`), h("td", { class: "mono" }, fastest(run)), h("td", {}, status(run)));
      }))));
}

function progressPanel(progress) {
  const bar = (m) => {
    const pct = m.state === "done" || m.state === "failed" ? 100 : m.state === "running" ? 8 + 92 * (m.done / progress.runs) : m.state === "warming" ? 8 : 0;
    return h("div", { class: `bar ${m.state}` }, h("div", { style: { width: `${pct}%`, "--c": colorOf(m.name) } }));
  };
  const word = (m) => ({ queued: "waiting", loading: "loading, a cold model compiles first", warming: "warm-up run, not counted", running: `run ${m.done + 1} of ${progress.runs}`,
    done: `done · ${fmt(m.decode_tps, 1)} tok/s decode`, failed: m.error || "failed" }[m.state]);
  return h("section", { class: "panel bench-progress", role: "status" },
    h("div", { class: "ph" }, icon("loader-circle", 16, HUE.bench, 2), h("span", { class: "t" }, "Benchmark running"), h("span", { class: "mono s" }, `${progress.models.length} model${progress.models.length === 1 ? "" : "s"} · one at a time · ${progress.runs} runs each`)),
    progress.models.map((m) => h("div", { class: "prow" }, h("span", { class: "stat-dot", style: { "--c": colorOf(m.name), background: colorOf(m.name) } }), h("span", { class: "mono nm" }, m.name),
      bar(m), h("span", { class: `st mono ${m.state === "failed" ? "bad-text" : ""}` }, word(m)))));
}

function emptyState(onRun) {
  return h("section", { class: "panel empty bench-empty" },
    h("span", { class: "chip40", style: { "--hue": HUE.bench } }, icon("gauge", 22, "currentColor", 1.8)),
    h("h2", {}, "No benchmark yet"),
    h("p", {}, "Run the same streamed workload on every model (about 512 tokens in, 128 out, one request at a time, five runs after a warm-up) and see which one wins. The results stay here."),
    h("div", { class: "code-box" }, h("div", { class: "ln mono" }, h("span", { class: "p" }, "$ "), "mxc bench --all")),
    h("div", { class: "empty-actions" }, h("button", { class: "btn primary", type: "button", onClick: onRun }, icon("zap", 15, "currentColor"), "Run benchmark")));
}

export function benchPage(ctx) {
  let state = null, hist = null, failed = null, shownId = null, starting = false;
  let panel = null, raceKey = "", tableKey = "", histKey = "", racing = 0, observer = null, pollTimer = 0;
  const progressHost = h("div", {}), raceHost = h("div", {}), tableHost = h("div", { class: "bench-block" }), histHost = h("div", { class: "bench-block" });
  const runBtn = h("button", { class: "btn primary", type: "button", onClick: () => run() }, icon("zap", 15, "currentColor"), h("span", {}, "Run benchmark"));
  ctx.actions.append(h("a", { class: "btn", href: "#benchmarks", onClick: (e) => { e.preventDefault(); const el = document.getElementById("bench-history"); if (el) el.scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth", block: "start" }); } }, icon("activity", 15), "History"), runBtn);
  ctx.body.replaceChildren(progressHost, raceHost, tableHost, histHost);
  assignColours(store.models);

  async function run() {
    if (starting) return;
    starting = true; drawButton();
    await startBench();
    starting = false;
    await load();
  }
  const running = () => !!(state && state.running);
  function drawButton() {
    const busy = running() || starting;
    runBtn.disabled = busy;
    runBtn.lastChild.textContent = busy ? "Running…" : "Run benchmark";
  }

  const shownRun = () => {
    if (!state) return null;
    const all = [...(state.latest ? [state.latest] : []), ...(hist || [])];
    return all.find((r) => r.id === shownId) || state.latest;
  };

  // -- the race ----------------------------------------------------------------------------------
  function place(animate) {
    if (!panel) return;
    for (const c of panel.cars) {
      const dist = Math.max(0, c.el.parentNode.clientWidth - CAR_W) * c.fraction;
      if (c.anim) { c.anim.cancel(); c.anim = null; }
      if (animate && !reducedMotion()) {
        c.anim = c.el.animate([{ transform: "translateX(0)" }, { transform: `translateX(${dist}px)` }], { duration: RACE_MS, easing: "cubic-bezier(.3,0,.2,1)", fill: "forwards" });
      } else {
        c.el.style.transform = `translateX(${dist}px)`;
      }
    }
    const g = panel.gauge;
    if (g) {
      if (g.anim) { g.anim.cancel(); g.anim = null; }
      if (animate && !reducedMotion()) {
        g.anim = g.needle.animate([{ transform: "rotate(-120deg)" }, { transform: `rotate(${g.angle}deg)` }], { duration: NEEDLE_MS, easing: "cubic-bezier(.2,.8,.2,1)", fill: "forwards" });
        g.num.dataset.v = "0"; g.num.textContent = "0";
        tick(g.num, g.top, fmt, 0);
      } else {
        g.needle.style.transform = `rotate(${g.angle}deg)`;
        g.num.dataset.v = String(g.top); g.num.textContent = fmt(g.top);
      }
    }
    if (animate && !reducedMotion()) {
      panel.track.classList.add("racing");
      clearTimeout(racing);
      racing = setTimeout(() => { if (panel) panel.track.classList.remove("racing"); }, RACE_MS + 100);
    }
  }
  function race(sound) {
    if (sound) vroom(); // sound only ever follows a click on "Race again"
    place(true);
  }

  function drawRace(run) {
    const key = run ? `${run.id}` : "";
    if (key === raceKey) return;
    raceKey = key;
    if (observer) { observer.disconnect(); observer = null; }
    panel = null;
    if (!run) { raceHost.replaceChildren(); return; }
    panel = racePanel(run, race);
    const lead = h("div", { class: "top-row" }, panel.node,
      panel.gauge ? h("section", { class: "panel top-speed" }, h("div", { class: "tt" }, "Top speed"), panel.gauge.el,
        h("div", { class: "cap mono" }, `${panel.racers[0].model} · fastest of ${panel.racers.length}`)) : null);
    raceHost.replaceChildren(lead);
    place(false);
    requestAnimationFrame(() => race(false)); // the first view runs the race silently
    if (window.ResizeObserver && panel.racers.length) {
      observer = new ResizeObserver(() => { if (panel && !panel.track.classList.contains("racing")) place(false); });
      observer.observe(panel.track);
    }
  }

  function draw() {
    drawButton();
    if (failed) { progressHost.replaceChildren(); raceHost.replaceChildren(); raceKey = ""; tableHost.replaceChildren(h("div", { class: "panel empty" }, h("h2", {}, "Could not load benchmarks"), h("p", {}, failed))); return; }
    if (!state) { tableHost.replaceChildren(h("div", { class: "panel empty" }, h("p", {}, "Loading…"))); return; }
    const progress = running() ? state.progress : null;
    progressHost.replaceChildren(...(progress ? [progressPanel(progress)] : []));
    progressHost.classList.toggle("bench-block", !!progress);
    const run = shownRun();
    if (!run) {
      raceKey = ""; panel = null; raceHost.replaceChildren(); histHost.replaceChildren(); tableKey = ""; histKey = "";
      ctx.setSub("same workload, every model, this machine · mxc bench --all");
      tableHost.replaceChildren(progress ? h("div", {}) : emptyState(() => run0()));
      return;
    }
    ctx.setSub(`${run.results.length} model${run.results.length === 1 ? "" : "s"} · ${run.id === (state.latest && state.latest.id) ? "latest run" : "an earlier run"} · ${dateTime(run.started_at)} · mxc bench --all`);
    assignColours(store.models);
    drawRace(run);
    const tk = JSON.stringify(run.results);
    if (tk !== tableKey) { tableKey = tk; tableHost.replaceChildren(resultsTable(run)); }
    const rows = (hist || []).filter(Boolean);
    const hk = JSON.stringify([rows.map((r) => [r.id, r.status]), run.id]);
    if (hk !== histKey) {
      histKey = hk;
      histHost.replaceChildren(h("h2", { class: "section-title" }, "History", h("span", { class: "sub" }, `${rows.length} run${rows.length === 1 ? "" : "s"} · click one to see its race`)),
        historyTable(rows, run.id, (id) => { shownId = id; raceKey = ""; tableKey = ""; histKey = ""; draw(); window.scrollTo({ top: 0, behavior: reducedMotion() ? "auto" : "smooth" }); }),
        h("div", { class: "cli-note mono" }, h("span", {}, "$ mxc bench --all"), h("span", {}, "$ mxc bench <model> --runs 5 --json")));
    }
  }
  const run0 = () => run();

  async function load() {
    try {
      const [now, history] = await Promise.all([api("GET", "/admin/bench"), api("GET", "/admin/bench/history")]);
      const finished = state && state.running && !now.running; // a run just ended: show it
      state = now; hist = history; failed = null;
      if (finished || shownId === null) shownId = state.latest ? state.latest.id : null;
      if (finished && state.latest) toast(state.latest.status === "done" ? "Benchmark finished." : "Benchmark finished: every model failed.", 4000);
    } catch (e) { failed = e.message; }
    draw();
    schedule();
  }
  const schedule = () => { clearTimeout(pollTimer); pollTimer = setTimeout(load, running() ? 1000 : 4000); };
  const onStarted = () => load();
  window.addEventListener("mxc-bench-started", onStarted);
  load();
  return {
    update() {},
    destroy() { clearTimeout(pollTimer); clearTimeout(racing); if (observer) observer.disconnect(); window.removeEventListener("mxc-bench-started", onStarted); },
  };
}
