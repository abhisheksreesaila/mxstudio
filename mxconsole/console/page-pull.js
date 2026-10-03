// Pull a model: search box, the cloud-and-cartridge animation driven by real progress, the step list,
// per-file rows, Cancel, and recent pulls. Everything here follows the daemon's /admin/pulls jobs:
// the shard rate follows the download speed, the cartridge fill follows the percentage, and the
// motion stops when the pull is done, failed or cancelled. prefers-reduced-motion turns the motion
// off in CSS; the numbers and the fill still update.
import { h, s, icon, api, ADMIN, store, toast, copyButton, terminalBox, ago, HUE, SIGNAL } from "./core.js";

const POLL_MS = 1000;
const SHARDS = 14;
const FINISHED = ["done", "failed", "cancelled", "unsupported"];
const SHARD_COLOURS = [HUE.pull, SIGNAL[0], SIGNAL[1], HUE.keys];
const CLOUD = "M 70 100 C 30 100 20 60 55 52 C 55 20 105 10 120 40 C 135 15 190 20 190 55 C 225 55 230 100 195 100 Z";
const STEP_ICON = { done: "check", now: "loader-circle", next: "circle-dashed", failed: "x", cancelled: "x" };

// -- pure helpers (also run under node by the tests) ---------------------------------------------------
export function bytes(n) {
  n = Number(n) || 0;
  if (n >= 1 << 30) return `${(n / (1 << 30)).toFixed(2)} GB`;
  if (n >= 1 << 20) return `${(n / (1 << 20)).toFixed(1)} MB`;
  if (n >= 1024) return `${Math.round(n / 1024)} KB`;
  return `${n} B`;
}
export const speedText = (bps) => (bps > 0 ? `${bytes(bps)}/s` : "—");
export function etaText(sec) {
  if (sec === null || sec === undefined) return "—";
  return sec >= 60 ? `${Math.floor(sec / 60)} min ${sec % 60} s` : `${sec} s`;
}
/** 0..1 from a download speed in bytes/s, on a log scale: 100 MB/s is full rain, 0 is none. */
export function intensity(bps) {
  if (!(bps > 0)) return 0;
  return Math.min(1, Math.log10(1 + bps / 1e6) / Math.log10(101));
}
/** How many of the shards are in the air, and the seconds one takes to fall. Quantised so the animation
 * is not restarted by every poll. Not running (done, failed, idle): none. */
export function rain(bps, running) {
  if (!running) return { count: 0, fall: 2.8, level: 0 };
  const level = bps > 0 ? Math.max(1, Math.round(intensity(bps) * 6)) : 0;
  return { count: Math.round((level / 6) * SHARDS), fall: +(2.9 - level * 0.35).toFixed(2), level };
}
export const isRunning = (job) => !!job && job.status === "running";
/** The job to show: the one the person picked, else a running one, else the newest. */
export function pickFocus(pulls, selectedId) {
  if (!pulls || !pulls.length) return null;
  return pulls.find((p) => p.id === selectedId) || pulls.find((p) => p.status === "running") || pulls[0];
}
export function stage(job) {
  if (!job) return "idle";
  return { running: "running", done: "done", cancelled: "cancelled" }[job.status] || "failed";
}
export function headline(job) {
  if (!job) return "Pick a model to pull";
  const names = { done: "Pulled and ready", failed: "The pull failed", cancelled: "Cancelled", unsupported: "MAX can't run this yet" };
  if (job.status !== "running") return names[job.status] || job.status;
  const step = (job.steps.find((x) => x.state === "now") || {}).label;
  return step ? `${step}…` : "Working…";
}

// -- the animation -----------------------------------------------------------------------------------
function buildStage() {
  const shards = [];
  for (let i = 0; i < SHARDS; i++) {
    const x = 18 + ((i * 37) % 60) + (i % 3) * 2; // % across the column, spread but not regular
    const el = h("span", { class: "shard", style: { left: `${x}%`, "--delay": `${((i * 0.37) % 2.4).toFixed(2)}s`, "--c": SHARD_COLOURS[i % 4] } });
    shards.push(el);
  }
  const cloud = s("svg", { class: "cloud", viewBox: "0 0 260 120", "aria-hidden": "true" },
    s("path", { d: CLOUD, class: "cloud-shape" }),
    s("text", { x: 122, y: 80, "text-anchor": "middle", class: "cloud-label" }, "Hugging Face Hub"));
  const ringA = s("svg", { class: "ring a", viewBox: "0 0 160 160", "aria-hidden": "true" }, s("circle", { cx: 80, cy: 80, r: 70 }));
  const ringB = s("svg", { class: "ring b", viewBox: "0 0 160 160", "aria-hidden": "true" }, s("circle", { cx: 80, cy: 80, r: 54 }));
  const fill = h("div", { class: "fill" });
  const tick = h("span", { class: "tick" }, icon("check", 22, "currentColor", 2.4));
  const cart = h("div", { class: "cart" }, fill, h("div", { class: "cart-label mono" }, "MODEL"), tick);
  const caption = h("span", { class: "mono cap-l" });
  const bar = h("div", { class: "bar-in" });
  const pct = h("span", { class: "num mono cap-r" });
  const root = h("div", { class: "pull-stage is-idle" },
    h("div", { class: "stage-from mono" }, "huggingface.co → this machine"),
    cloud, h("div", { class: "shards", "aria-hidden": "true" }, shards),
    h("div", { class: "churn" }, ringA, ringB, cart),
    h("div", { class: "stage-foot" }, caption, h("div", { class: "bar", role: "progressbar", "aria-label": "Download progress", "aria-valuemin": "0", "aria-valuemax": "100" }, bar), pct));
  const bigNums = h("div", { class: "stage-nums mono num", "aria-live": "off" });
  root.append(bigNums);

  let lastLevel = -1;
  return {
    root,
    draw(job) {
      const st = stage(job);
      root.className = `pull-stage is-${st}`;
      const r = rain(job ? job.speed : 0, st === "running");
      shards.forEach((el, i) => el.classList.toggle("on", i < r.count));
      if (r.level !== lastLevel) { // one write per level, so the shards keep falling instead of restarting
        lastLevel = r.level;
        root.style.setProperty("--fall", `${r.fall}s`);
        root.style.setProperty("--churn-a", `${(7 - r.level * 0.9).toFixed(1)}s`);
        root.style.setProperty("--churn-b", `${(5 - r.level * 0.6).toFixed(1)}s`);
      }
      const p = job ? (job.status === "done" ? 100 : job.percent || 0) : 0;
      fill.style.height = `${Math.max(job ? 3 : 0, p)}%`;
      bar.style.width = `${p}%`;
      bar.setAttribute("class", `bar-in ${st}`);
      root.querySelector(".bar").setAttribute("aria-valuenow", String(Math.round(p)));
      caption.textContent = job ? (job.name || job.ref) : "no pull running";
      pct.textContent = job && job.percent !== null && job.percent !== undefined ? `${Math.round(p)}%` : job && job.status === "done" ? "100%" : "—";
      const eta = st === "running" && job.eta_seconds !== null && job.eta_seconds !== undefined ? ` · ${etaText(job.eta_seconds)} left` : "";
      bigNums.textContent = st === "running" && job.speed ? `${speedText(job.speed)}${eta}` : "";
    },
  };
}

// -- the page --------------------------------------------------------------------------------------------
export function pullPage(ctx) {
  const ui = { selected: null, pulls: null, curated: {}, error: null, formError: "", prefill: ctx.args[0] || "" };
  const stageView = buildStage();

  const input = h("input", { class: "input mono", id: "pull-ref", name: "ref", list: "pull-suggest", placeholder: "qwen2.5-0.5b or owner/repo", "aria-label": "Model to pull",
    autocomplete: "off", spellcheck: "false", value: ui.prefill });
  const datalist = h("datalist", { id: "pull-suggest" });
  const go = h("button", { class: "btn primary", type: "submit" }, icon("download", 15), "Pull");
  const hubBadge = h("span", { class: "pill warn", id: "pull-test-hub", hidden: true, title: "This daemon pulls from a fake Hugging Face (MXC_FAKE_HUB). Nothing real is downloaded." }, "test hub");
  const formErr = h("div", { class: "bad-text pull-err", role: "alert" });
  const chips = h("div", { class: "pull-chips" });
  const form = h("form", { class: "pull-form", onSubmit: (e) => { e.preventDefault(); start(input.value.trim()); } },
    h("label", { class: "pull-search" }, h("span", { class: "sr-only" }, "Model to pull"), h("span", { class: "ic" }, icon("search", 15)), input, datalist), go, hubBadge);

  const cancelBtn = h("button", { class: "btn danger", type: "button", hidden: true, onClick: cancel }, icon("x", 15), "Cancel");
  const anotherBtn = h("button", { class: "btn primary", type: "button", hidden: true, onClick: () => { ui.selected = null; input.value = ""; input.focus(); draw(); } }, icon("plus", 15), "Pull another");
  ctx.actions.append(cancelBtn, anotherBtn);

  const steps = h("div", { class: "pull-steps" });
  const files = h("div", { class: "pull-files" });
  const note = h("div", {});
  const twin = h("div", {});
  const side = h("section", { class: "panel pull-side", style: { "--hue": HUE.pull } },
    h("h2", { class: "section-title" }, "What is happening", h("span", { class: "sub", id: "pull-title" }, "")), steps, files, note, twin);
  const recent = h("section", { class: "panel pull-recent scroll-x" });
  ctx.body.replaceChildren(
    form, formErr, chips,
    h("div", { class: "pull-grid" }, h("section", { class: "panel pull-stage-panel", style: { "--hue": HUE.pull, "--hue-2": SIGNAL[0] } }, stageView.root), side),
    h("h2", { class: "section-title pull-recent-title" }, "Recent pulls", h("span", { class: "sub" }, "kept after a restart")), recent);

  function current() { return pickFocus(ui.pulls, ui.selected); }
  // Redraw a block only when what it shows changed: the page polls every second, and rebuilding an
  // unchanged button under the pointer would eat the click.
  const seen = new Map();
  const changed = (id, key) => { const k = JSON.stringify(key); if (seen.get(id) === k) return false; seen.set(id, k); return true; };

  async function start(ref) {
    ui.formError = "";
    if (!ref) { ui.formError = "Type a short name (qwen2.5-0.5b) or a Hugging Face id like owner/repo."; draw(); return; }
    go.disabled = true;
    try {
      const res = await fetch("/admin/pulls", { method: "POST", headers: { ...ADMIN, "Content-Type": "application/json" }, body: JSON.stringify({ ref }) });
      let body = {};
      try { body = await res.json(); } catch (_) { /* no JSON: report the status below */ }
      if (res.status === 409 && body.pull) { // already being pulled: watch that one instead of showing an error
        ui.selected = body.pull.id;
      } else if (!res.ok) {
        throw new Error((body.error && body.error.message) || `HTTP ${res.status}`);
      } else ui.selected = body.id;
      await load();
    } catch (e) {
      ui.formError = e.message;
      draw();
    } finally { go.disabled = false; }
  }
  async function cancel() {
    const job = current();
    if (!job || job.status !== "running") return;
    cancelBtn.disabled = true;
    try { await api("POST", `/admin/pulls/${job.id}/cancel`); toast("Cancelling: partial files are kept, so pulling again resumes."); } catch (e) { toast(`Could not cancel: ${e.message}`, 6000); }
    await load();
    cancelBtn.disabled = false;
  }

  function drawChips() {
    const installed = new Set((store.models || []).map((m) => m.name));
    if (!changed("chips", [ui.curated, [...installed]])) return;
    chips.replaceChildren(h("span", { class: "muted mono" }, "curated"), ...Object.entries(ui.curated).map(([name, repo]) =>
      h("button", { class: "chip-btn mono", type: "button", title: repo, onClick: () => { input.value = name; input.focus(); } },
        installed.has(name) ? icon("check", 12, "currentColor", 2.4) : null, name)));
    datalist.replaceChildren(...Object.entries(ui.curated).map(([name, repo]) => h("option", { value: name }, repo)));
  }

  function drawSteps(job) {
    if (!changed("steps", job && [job.id, job.steps])) return;
    if (!job) {
      steps.replaceChildren(h("p", { class: "pull-hint" }, "Nothing is downloading. Pick a name above, or paste any Hugging Face repo id. MAX needs safetensors weights; the pull is pinned to one revision and resumes if it stops."));
      return;
    }
    steps.replaceChildren(...job.steps.map((st) => h("div", { class: `pull-step ${st.state}` },
      h("span", { class: "dot-ic" }, icon(STEP_ICON[st.state] || "circle-dashed", 14, "currentColor", 2)),
      h("div", {}, h("div", { class: "t" }, stepTitle(st)), st.detail ? h("div", { class: "d mono" }, st.detail) : null))));
  }
  const stepTitle = (st) => (st.state === "done" ? { resolve: "Resolved", architecture: "Architecture supported", download: "Downloaded", verify: "Verified", register: "Registered" }[st.id] : st.label);

  function drawFiles(job) {
    const rows = job ? job.files : [];
    if (!changed("files", [job && job.id, rows])) return;
    files.replaceChildren(...rows.map((f) => {
      const done = f.state === "done";
      const p = f.size ? Math.min(100, Math.round((f.done * 100) / f.size)) : null;
      return h("div", { class: "file-row" },
        h("span", { class: "mono fname", title: f.name }, f.name),
        h("span", { class: `mono fstat ${done ? "ok-text" : "muted"}` }, f.size ? (done ? `${bytes(f.size)} ✓` : `${bytes(f.size)} · ${p}%`) : bytes(f.done)));
    }));
  }

  function drawNote(job) {
    if (!changed("note", job && [job.id, job.status, job.message, job.hint, job.model])) return;
    if (!job || job.status === "running" || job.status === "done") {
      if (job && job.status === "done") {
        note.replaceChildren(h("div", { class: "pull-done" }, icon("circle-check", 16), h("div", {}, h("strong", {}, `${job.model} is ready. `), "It is registered and loads on its first request. ",
          h("a", { href: `#models/${encodeURIComponent(job.model)}` }, "Open it in Models"))));
      } else note.replaceChildren();
      return;
    }
    const bad = job.status === "unsupported" || job.status === "failed";
    note.replaceChildren(h("div", { class: `pull-problem ${job.status}` }, icon(job.status === "cancelled" ? "x" : "triangle-alert", 16),
      h("div", {}, h("div", {}, job.message),
        job.hint ? h("div", { class: "pull-hint-row" }, h("span", { class: "mono" }, job.hint), copyButton(() => job.hint, "Copy")) : null,
        bad && !job.hint ? h("button", { class: "btn", type: "button", style: "margin-top:8px", onClick: () => start(job.ref) }, icon("refresh-cw", 14), "Try again") : null,
        job.status === "cancelled" ? h("button", { class: "btn", type: "button", style: "margin-top:8px", onClick: () => start(job.ref) }, icon("refresh-cw", 14), "Resume") : null)));
  }

  function drawRecent() {
    if (!changed("recent", [ui.pulls && ui.pulls.map((p) => [p.id, p.status, p.name, p.ref, p.repo_id, p.total_bytes, Math.round(p.percent || 0), p.finished_at, p.model]), ui.selected, (current() || {}).id, Math.floor(Date.now() / 30000)])) return;
    if (ui.pulls === null) { recent.replaceChildren(h("div", { class: "empty" }, h("p", {}, "Loading…"))); return; }
    if (!ui.pulls.length) { recent.replaceChildren(h("div", { class: "empty" }, h("p", {}, "No pulls yet. They are listed here, with how each one ended."))); return; }
    const focus = current();
    recent.replaceChildren(h("table", { class: "grid" },
      h("thead", {}, h("tr", {}, ["Model", "Result", "Size", "When", ""].map((c) => h("th", {}, c)))),
      h("tbody", {}, ui.pulls.map((p) => h("tr", { class: "pick", "aria-selected": focus && focus.id === p.id ? "true" : "false", onClick: () => { ui.selected = p.id; draw(); } },
        h("td", {}, h("span", { class: "mono", style: "font-weight:500" }, p.name || p.ref), p.repo_id && p.repo_id !== p.ref ? h("div", { class: "mono muted", style: "font-size:12px" }, p.repo_id) : null),
        h("td", {}, h("span", { class: `pill ${pillClass(p.status)}` }, pillText(p))),
        h("td", { class: "mono" }, p.total_bytes ? bytes(p.total_bytes) : "—"),
        h("td", { class: "muted" }, ago(p.finished_at || p.started_at)),
        h("td", { class: "r" }, p.status === "done" ? h("a", { class: "btn", href: `#models/${encodeURIComponent(p.model)}`, onClick: (e) => e.stopPropagation(), style: "height:28px" }, "Open in Models") : null))))));
  }

  function draw() {
    const job = current();
    ctx.setSub(job && job.status === "running" ? `${job.repo_id || job.ref} · ${headline(job).toLowerCase()}` : "pinned revision · safetensors only · resumable");
    stageView.draw(job);
    hubBadge.hidden = !ui.fakeHub;
    cancelBtn.hidden = !isRunning(job);
    anotherBtn.hidden = !job || isRunning(job);
    formErr.textContent = ui.formError || (ui.error ? `Could not reach the daemon: ${ui.error}` : "");
    document.getElementById("pull-title").textContent = job ? `mxc pull ${job.ref}` : "";
    drawChips(); drawSteps(job); drawFiles(job); drawNote(job);
    if (changed("twin", job && job.ref)) twin.replaceChildren(terminalBox([job ? `mxc pull ${job.ref}` : "mxc pull qwen2.5-0.5b", "mxc pull <repo> --json"], "same from the terminal"));
    drawRecent();
  }

  async function load() {
    try {
      const body = await api("GET", "/admin/pulls");
      ui.pulls = body.pulls; ui.curated = body.curated || {}; ui.fakeHub = !!body.fake_hub; ui.error = null;
    } catch (e) { ui.error = e.message; }
    draw();
  }
  const timer = setInterval(load, POLL_MS);
  load();
  if (ui.prefill) input.focus();
  return {
    update() {},
    route(args) { if (args[0]) { input.value = args[0]; input.focus(); } },  // the palette opens this page with a ref
    destroy() { clearInterval(timer); },
  };
}

const pillClass = (status) => ({ done: "ok", running: "info", failed: "bad", unsupported: "warn", cancelled: "" }[status] || "");
const pillText = (p) => (p.status === "running" ? `Pulling ${Math.round(p.percent || 0)}%` : { done: "Pulled", failed: "Failed", unsupported: "Unsupported", cancelled: "Cancelled" }[p.status] || p.status);
