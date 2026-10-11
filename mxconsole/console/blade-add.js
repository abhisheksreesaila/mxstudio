// mxconsole, the Add a model blade (F-026): one search over the MAX catalog and Hugging Face, each result marked
// fits / tight / too big / needs porting for this machine; picking one runs the setup stages (check hardware,
// fetch, compile graph, serve on localhost, smoke test) with a ring timeline, elapsed times and a live log, and
// ends on a Ready card. An unsupported architecture offers "Port it" (F-029's job: /admin/ports).
// Data: /admin/search, /admin/pulls, /admin/models/{name}/load, /admin/try/chat, /admin/ports (polled while running).
// The pure helpers at the top are what the tests run under node; the blade itself is below.
import { h, icon, api, store, refresh, toast, copyButton } from "./core.js";
import { register, push, stack, setHead } from "./blades.js";

// -- pure: formatting ------------------------------------------------------------------------------
const trim = (n) => String(Number(Number(n).toFixed(1)));
export const sizeText = (gb) => (gb === null || gb === undefined ? "" : `${trim(gb)} GB`);
export function paramsText(n) {
  if (!n) return "";
  return n >= 1e9 ? `${trim(n / 1e9)}B` : `${Math.round(n / 1e6)}M`;
}
export function bytesText(n) {
  if (n === null || n === undefined) return "";
  if (n >= 1e9) return `${trim(n / 1e9)} GB`;
  if (n >= 1e6) return `${Math.round(n / 1e6)} MB`;
  if (n >= 1e3) return `${Math.round(n / 1e3)} KB`;
  return `${n} B`;
}
export function elapsedText(ms) {
  if (ms === null || ms === undefined) return "";
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  const s = Math.floor(ms / 1000);
  return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
}
const etaText = (sec) => (sec < 60 ? `${Math.round(sec)}s` : `${Math.floor(sec / 60)}m ${String(Math.round(sec % 60)).padStart(2, "0")}s`);
const archShort = (a) => String(a || "").replace(/(ForCausalLM|ForConditionalGeneration|LMHeadModel)$/, "");
const capital = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);

/** The mxc command that does the same thing as an action in this blade. */
export function twin(kind, ref) {
  return { pull: `mxc pull ${ref}`, serve: `mxc serve ${ref}`, port: `mxc port ${ref} --auto`, guided: `mxc port ${ref}`, search: `mxc search ${ref}`, ps: "mxc ps" }[kind] || "";
}
export function curlFor(endpoint, name) {
  return [`curl ${endpoint}/chat/completions \\`,
    `  -H "Authorization: Bearer $MXC_API_KEY" \\`,
    `  -H "Content-Type: application/json" \\`,
    `  -d '{"model": ${JSON.stringify(name)}, "messages": [{"role": "user", "content": "Hello"}]}'`].join("\n");
}

// -- pure: search results --------------------------------------------------------------------------
const FIT = { ok: ["ok", "fits"], tight: ["tight", "tight fit"], no: ["no", "too big"], port: ["port", "needs porting"] };
export function fitChip(fit) {
  const f = FIT[fit];
  return f ? { key: f[0], label: f[1] } : { key: "inst", label: "size unknown" };
}
export function resultView(r) {
  const hf = r.source === "hf";
  return {
    id: r.id, repo: r.repo_id || r.id, sourceKey: hf ? "hf" : "max", sourceLabel: hf ? "HF" : "MAX", fit: r.fit,
    meta: [archShort(r.architecture), paramsText(r.params), sizeText(r.size_gb)].filter(Boolean).join(" · "),
    vram: r.est_vram_gb === null || r.est_vram_gb === undefined ? "" : `~${trim(r.est_vram_gb)} GB VRAM`,
    chip: fitChip(r.fit),
  };
}
export const FILTERS = [["all", "All"], ["fits", "Fits"], ["port", "Needs porting"]];
export function filterResults(rows, filter) {
  if (filter === "fits") return rows.filter((r) => r.fit === "ok" || r.fit === "tight");
  if (filter === "port") return rows.filter((r) => r.fit === "port");
  return rows;
}
export const memoryText = (m) => (m ? `${m.name} · ${trim(m.free_gb)} GB free of ${trim(m.total_gb)} GB` : "");
/** What the results area shows. Pure state: loading / error / empty / list. (Debouncing lives in the blade.) */
export function searchView({ q = "", resp = null, loading = false, error = null }) {
  const query = String(q).trim();
  if (error) return { kind: "error", message: String(error), title: "", rows: [], memory: "" };
  if (!resp) return { kind: loading ? "loading" : "empty", message: "", title: "", rows: [], memory: "" };
  const rows = resp.results || [];
  const memory = memoryText(resp.memory);
  if (!rows.length) {
    return { kind: "empty", rows, memory, title: "", message: query ? `Nothing matches “${query}”. Try a Hugging Face repo id like owner/name.` : "No curated models are listed." };
  }
  return { kind: "list", rows, memory, message: "", title: query ? `${rows.length} result${rows.length === 1 ? "" : "s"}` : "Curated models" };
}
/** The "this one won't fit" panel: need against free, and the smaller models to pick instead. */
export function warnView(r, memory) {
  const need = r.est_vram_gb, free = memory ? memory.free_gb : 0;
  const name = String(r.repo_id || r.id).split("/").pop();
  return {
    name, need, free, gpu: memory ? memory.name : "this machine",
    freePct: need ? Math.min(100, (free / need) * 100) : 100,
    heading: need ? `${name} needs ~${trim(need)} GB. You have ${trim(free)} GB free.` : `${name} will not fit in the ${trim(free)} GB you have free.`,
    suggestions: (r.suggestions || []).map((s) => ({
      id: s.id, repo: s.repo_id, label: `fits · ~${trim(s.est_vram_gb)} GB`,
      detail: [archShort(s.architecture), paramsText(s.params), sizeText(s.size_gb)].filter(Boolean).join(" · "),
    })),
  };
}
export function hwDetail(r, memory) {
  if (r.est_vram_gb === null || r.est_vram_gb === undefined || !memory) return "size unknown · trying anyway";
  return `needs ~${trim(r.est_vram_gb)} GB · ${trim(memory.free_gb)} GB free on ${memory.name}`;
}

// -- pure: pull and port jobs -> stage rows ----------------------------------------------------------
/** One line about a pull job: bytes, speed, files and time left while downloading, the step's own note otherwise. */
export function pullDetail(job) {
  if (!job) return "";
  if (["failed", "cancelled", "unsupported"].includes(job.status)) return [job.message, job.hint].filter(Boolean).join(" · ");
  const steps = job.steps || [];
  const now = steps.find((s) => s.state === "now") || [...steps].reverse().find((s) => s.detail);
  const note = now ? now.detail : "";
  if (job.status === "running" && job.step === "download" && (job.total_bytes > 0 || job.done_bytes > 0)) {
    const parts = [job.total_bytes > 0 ? `${bytesText(job.done_bytes)} of ${bytesText(job.total_bytes)}` : bytesText(job.done_bytes)];
    if (job.speed > 0) parts.push(`${bytesText(job.speed)}/s`);
    if ((job.files || []).length) parts.push(`${job.files.length} file${job.files.length === 1 ? "" : "s"}`);
    if (job.eta_seconds) parts.push(`${etaText(job.eta_seconds)} left`);
    return parts.join(" · ");
  }
  if (job.status === "done") return job.total_bytes > 0 ? `${bytesText(job.total_bytes)} on disk · files verified` : job.message || "";
  return note || "starting…";
}

const PORT_LABELS = {
  workspace: "Prepare workspace", skills: "Load Modular skills", generate: "Generate the model", build: "Build the graph",
  parity: "Parity check", "parity check": "Parity check", register: "Register the model", serve: "Serve on localhost",
};
const portKey = (name) => String(name || "").toLowerCase().replace(/[_\s-]+/g, " ").trim();
export const portLabel = (name) => PORT_LABELS[portKey(name)] || capital(portKey(name));
/** F-029's job stages -> rows. elapsed is milliseconds (timestamps from the daemon are epoch seconds). */
export function portStageRows(job, now) {
  return ((job && job.stages) || []).map((s) => {
    const status = s.status || "pending";
    const end = s.ended ? s.ended * 1000 : status === "running" ? now : null;
    return {
      id: `port:${portKey(s.name)}${s.attempt ? `:${s.attempt}` : ""}`,
      label: portLabel(s.name) + (s.attempt > 1 ? ` · attempt ${s.attempt}` : ""), status, percent: null, twin: "",
      elapsed: s.started && end ? Math.max(0, end - s.started * 1000) : null,
      detail: s.detail || "",
    };
  });
}
export function portLogLines(job) {
  const tail = job && job.log_tail;
  if (!tail) return [];
  const lines = typeof tail === "string" ? tail.split("\n") : tail; // the daemon sends one string; arrays are accepted too
  return lines.map((l) => (typeof l === "string" ? l : l.line || l.text || l.message || JSON.stringify(l)));
}

const ORDER = ["check", "fetch", "compile", "serve", "smoke"];
const LABEL = { check: "Check hardware", fetch: "Fetch the model", compile: "Compile graph", serve: "Serve on localhost", smoke: "Smoke test" };
/** The five-stage timeline for a run (a port run swaps "fetch" for the port job's own stages). Pure.
 *  run: {kind: pull|port, phase: fetch|compile|serve|smoke|ready|failed|cancelled, failedAt, error, hw, ref, name, fetch, port, smoke, endpoint, times} */
export function setupStages(run, now) {
  const times = run.times || {};
  const cur = run.phase === "ready" ? ORDER.length : ORDER.indexOf(run.phase === "failed" || run.phase === "cancelled" ? run.failedAt : run.phase);
  const name = run.name || run.ref;
  const elapsedOf = (id, status) => {
    const t = times[id];
    if (!t || t.start === null || t.start === undefined) return null;
    const end = t.end ?? (status === "running" ? now : null);
    return end === null ? null : Math.max(0, end - t.start);
  };
  const base = (id) => {
    const i = ORDER.indexOf(id);
    const status = i < cur ? "done" : i === cur ? (run.phase === "failed" ? "failed" : run.phase === "cancelled" ? "cancelled" : "running") : "pending";
    const row = { id, label: LABEL[id], status, detail: "", percent: null, elapsed: elapsedOf(id, status), twin: "" };
    if (status === "failed") row.detail = run.error || "";
    return row;
  };
  const rows = [];
  const check = base("check");
  check.detail = run.hw || "";
  check.twin = twin("search", run.ref || "");
  rows.push(check);
  if (run.kind === "port") {
    const pr = portStageRows(run.port, now);
    if (!pr.length) pr.push({ id: "port:start", label: "Start the port", status: run.phase === "failed" ? "failed" : run.phase === "cancelled" ? "cancelled" : cur > 1 ? "done" : "running", detail: run.error || "", percent: null, elapsed: elapsedOf("fetch", "running"), twin: "" });
    pr[0].twin = twin("port", run.ref);
    rows.push(...pr);
  } else {
    const f = base("fetch");
    f.twin = twin("pull", run.ref);
    f.percent = f.status === "done" ? 100 : f.status === "running" ? (run.fetch && run.fetch.percent !== undefined ? run.fetch.percent : null) : null;
    if (f.status !== "failed") f.detail = pullDetail(run.fetch) || (f.status === "running" ? "starting…" : "");
    if (f.status === "cancelled") f.detail = "cancelled";
    rows.push(f);
  }
  const compile = base("compile");
  compile.twin = twin("serve", name);
  if (compile.status === "running") compile.detail = "building the graph for this GPU · the first start takes a while";
  else if (compile.status === "done") compile.detail = "graph compiled";
  const serve = base("serve");
  serve.twin = twin("ps");
  if (serve.status === "running") serve.detail = "waiting for the server to answer";
  else if (serve.status === "done") serve.detail = run.endpoint ? `answering at ${run.endpoint}` : "answering on localhost";
  const smoke = base("smoke");
  if (smoke.status === "running") smoke.detail = "asking for one short reply";
  else if (smoke.status === "done" && run.smoke) smoke.detail = `“${run.smoke}”`;
  // a port job registers and serves the model itself (its own register and serve rows), so only the smoke test follows it
  if (run.kind === "port") rows.push(smoke); else rows.push(compile, serve, smoke);
  return rows;
}

// -- pure: the blade's control logic (tested under node) ------------------------------------------------
/** What "Cancel" must send for a run in this phase, or null when there is nothing to send yet. */
export function cancelPlan(run, flow) {
  const post = (path) => ({ method: "POST", path });
  const model = () => (run.name ? post(`/admin/models/${encodeURIComponent(run.name)}/stop`) : null);
  if (run.phase === "fetch") {
    if (run.kind === "port") {
      if (run.port && run.port.status === "done") return model();  // the job is finished: stop what it served
      return flow.portId ? post(`/admin/ports/${flow.portId}/cancel`) : null;
    }
    return flow.pullId ? post(`/admin/pulls/${flow.pullId}/cancel`) : null;
  }
  if (run.phase === "compile" || run.phase === "serve" || run.phase === "smoke") return model(); // supervisor.stop also cancels a start in progress
  return null;
}
/** Every timer a blade starts goes through here, so closing it stops all of them and nothing starts afterwards. */
export function timerScope(t = globalThis) {
  const live = new Set();
  let closed = false;
  return {
    later(fn, ms) { if (closed) return 0; const id = t.setTimeout(() => { live.delete(id); fn(); }, ms); live.add(id); return id; },
    every(fn, ms) { if (closed) return 0; const id = t.setInterval(fn, ms); live.add(id); return id; },
    stop(id) { t.clearTimeout(id); t.clearInterval(id); live.delete(id); },
    closeAll() { closed = true; live.forEach((id) => { t.clearTimeout(id); t.clearInterval(id); }); live.clear(); },
    closed: () => closed,
  };
}
/** Search responses can arrive out of order: only the newest request's answer may be drawn. */
export function searchGuard() {
  let n = 0;
  return { next: () => ++n, isCurrent: (k) => k === n };
}
/** What a failed POST /admin/ports means. `e` is the Error from api(): {status, code, body, message}. */
export function classifyPortError(e) {
  if (e.status === 404 || e.status === 405) return { kind: "unavailable", message: e.message };
  if (e.status === 409 && e.body && e.body.port) return { kind: "follow", job: e.body.port, message: e.message };
  if (e.status === 400 && e.code === "no_sandbox") return { kind: "no_sandbox", message: e.message };
  if (e.status === 400) return { kind: "refused", message: e.message };
  return { kind: "other", message: e.message };
}

// -- the blade -------------------------------------------------------------------------------------
const DEBOUNCE_MS = 250, PULL_POLL_MS = 700, PORT_POLL_MS = 1500, TICK_MS = 500, MAX_POLL_ERRORS = 6;
const DEFAULT_SUB = "Search the MAX catalog and Hugging Face · every result is checked against this machine's memory";
const stamp = () => new Date().toTimeString().slice(0, 8);
const endpointNow = () => (store.info && store.info.endpoint) || `${location.origin}/v1`;

const RING = { done: "check", failed: "x", cancelled: "x" };
function ringIcon(status) {
  return RING[status] ? icon(RING[status], 13, "currentColor", 2.4) : null;
}

register("add", (blade) => ({
  title: "Add a model", crumb: "Add a model", sub: DEFAULT_SUB,
  icon: "plus", hue: "var(--hue-add)", wide: true, min: 900,
  mount(body) { mountAdd(blade, body); },
}));

function mountAdd(blade, body) {
  const ui = { q: "", resp: null, loading: true, error: null, filter: "all", seq: 0, flow: 0, run: null, logOpen: true };
  const scope = timerScope(), guard = searchGuard();
  let debounce = 0;
  const live = () => !scope.closed() && blade.alive !== false;
  const later = scope.later, every = scope.every, stop = scope.stop;
  const sleep = (ms) => new Promise((res) => later(res, ms));
  blade.onClose = () => { ui.flow++; scope.closeAll(); };

  // ---- find view ----
  const input = h("input", { type: "search", placeholder: "Search the MAX catalog, or paste a Hugging Face repo id (owner/name)", "aria-label": "Search models", autocomplete: "off", spellcheck: "false" });
  const memEl = h("div", { class: "memline mono" });
  const seg = h("div", { class: "fseg", role: "group", "aria-label": "Show" });
  const slot = h("div", { class: "slot" });
  const resHead = h("div", { class: "h3" });
  const list = h("div", { class: "reslist", "aria-live": "polite" });
  const findWrap = h("div", { class: "findwrap" }, h("div", { class: "find" },
    h("label", { class: "search" }, icon("search", 16, "currentColor", 1.75), input), h("div", { class: "frow" }, seg, memEl), slot, resHead, list));

  function drawFilter() {
    seg.replaceChildren(...FILTERS.map(([key, label]) => {
      const b = h("button", { type: "button", class: ui.filter === key ? "on" : "", "aria-pressed": String(ui.filter === key) }, label);
      b.addEventListener("click", () => { ui.filter = key; drawFilter(); drawResults(); });
      return b;
    }));
  }
  function drawResults() {
    const v = searchView(ui);
    memEl.textContent = v.memory ? `fit checked against ${v.memory}` : "";
    list.classList.toggle("busy", ui.loading && !!ui.resp);
    resHead.replaceChildren(v.title);
    if (v.kind === "loading") { list.replaceChildren(h("div", { class: "empty" }, "Searching…")); return; }
    if (v.kind === "error") { list.replaceChildren(h("div", { class: "empty err" }, `Search failed: ${v.message}`)); return; }
    const rows = filterResults(v.rows, ui.filter);
    if (v.kind === "empty" || !rows.length) { list.replaceChildren(h("div", { class: "empty" }, v.message || "Nothing in this filter.")); return; }
    list.replaceChildren(...rows.map((r) => resultRow(r)));
  }
  function resultRow(r) {
    const v = resultView(r);
    const btn = h("button", { type: "button", class: "res", "data-id": r.id },
      h("span", { class: `src ${v.sourceKey}` }, v.sourceLabel),
      h("span", { class: "nm" }, r.id),
      h("span", { class: "fits" }, h("span", { class: `fit ${v.chip.key}` }, v.chip.label), v.vram ? h("span", { class: "sz" }, v.vram) : null),
      h("span", { class: "mt" }, [v.repo !== r.id ? v.repo : "", v.meta].filter(Boolean).join(" · ")));
    btn.addEventListener("click", () => pick(r));
    return btn;
  }
  async function runSearch() {
    const seq = guard.next();
    ui.loading = true; ui.error = null;
    drawResults();
    try {
      const resp = await api("GET", `/admin/search?q=${encodeURIComponent(ui.q.trim())}`);
      if (!guard.isCurrent(seq) || !live()) return;
      ui.resp = resp;
    } catch (e) {
      if (!guard.isCurrent(seq) || !live()) return;
      ui.error = e.message;
    }
    ui.loading = false;
    drawResults();
  }
  input.addEventListener("input", () => {
    ui.q = input.value;
    stop(debounce);
    debounce = later(runSearch, DEBOUNCE_MS);
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { stop(debounce); runSearch(); }
  });

  function pick(r) {
    slot.replaceChildren();
    if (r.fit === "no") return showWarn(r);
    if (r.fit === "port") return showPortOffer(r);
    startPull(r);
  }
  function meter(label, pct, bad, text) {
    const bar = h("i");
    bar.style.width = `${pct}%`;
    bar.style.background = bad ? "var(--bad)" : "var(--hue-add)";
    return h("div", { class: "meter" }, h("span", {}, label), h("span", { class: "tr" }, bar), h("span", { class: "num" }, text));
  }
  function showWarn(r) {
    const w = warnView(r, ui.resp && ui.resp.memory);
    const picks = w.suggestions.map((s) => {
      const b = h("button", { type: "button", class: "sg", "data-id": s.id },
        h("span", { class: "t" }, s.repo), h("span", { class: "fit ok" }, s.label), h("span", { class: "d" }, s.detail), h("span", { class: "cli mono" }, twin("pull", s.id)));
      b.addEventListener("click", () => {
        const full = (r.suggestions || []).find((x) => x.id === s.id) || {};
        startPull({ ...full, fit: "ok", supported: true, source: "max" });
      });
      return b;
    });
    const notice = h("div", { class: "notice warn", role: "alert" },
      h("div", { class: "lab" }, icon("triangle-alert", 15, "currentColor", 1.9), "HARDWARE CHECK · nothing downloaded"),
      h("h4", {}, w.heading),
      h("p", {}, "mxconsole checks before downloading weights you cannot use. Pick a smaller model that fits, or free memory by stopping a running one."),
      meter("needs", 100, true, w.need ? `~${trim(w.need)} GB` : "?"),
      meter(w.gpu, w.freePct, false, `${trim(w.free)} GB`),
      picks.length ? h("div", { class: "sugg" }, picks) : h("p", { class: "muted" }, "Nothing smaller in the catalog fits either."));
    slot.replaceChildren(notice);
    notice.scrollIntoView({ block: "nearest" });
  }
  function showPortOffer(r, opts = {}) {
    const ref = r.repo_id || r.id;
    const row = h("div", { class: "cmd-row" });
    if (opts.guided) {
      row.append(h("code", { class: "cmd mono" }, twin("guided", ref)), copyButton(() => twin("guided", ref), "Copy"));
    } else if (opts.refused) {
      row.append(h("span", { class: "cli mono" }, twin("guided", ref)));
    } else {
      const go = h("button", { type: "button", class: "btn port" }, icon("hammer", 15, "currentColor", 1.9), "Port it");
      go.addEventListener("click", () => startPort(r));
      row.append(go, h("span", { class: "cli mono" }, twin("port", ref)));
    }
    const notice = h("div", { class: "notice port" },
      h("div", { class: "lab" }, icon("hammer", 15, "currentColor", 1.9), "ARCHITECTURE CHECK"),
      h("h4", {}, `${ref.split("/").pop()} uses ${r.architecture || "an architecture"}, which MAX cannot run yet.`),
      h("p", {}, "“Port it” runs Claude Code with Modular’s import-model skill, unattended and in a sandbox: it reads the config, writes the model as a MAX graph, checks it against the reference and fixes what fails. It may take a while, often several minutes and sometimes much longer, and it uses your Claude Code session. You will see every stage and the full log, and you can cancel."),
      opts.guided ? h("p", { class: "warn-text" }, "Automatic porting is not in this daemon yet. Run the guided port from a terminal instead:") : null,
      opts.note ? h("p", { class: "warn-text" }, opts.note) : null,
      opts.refused ? h("p", { class: "warn-text" }, opts.refused) : null,
      opts.sandbox ? h("p", { class: "muted small" }, "Without a sandbox the agent would run with your full permissions, so it is not offered here. If you accept that, the terminal can: ", h("span", { class: "mono" }, `${twin("port", ref)} --unsandboxed`)) : null,
      h("dl", { class: "kv" }, h("dt", {}, "architecture"), h("dd", {}, r.architecture || "unknown"),
        h("dt", {}, "size"), h("dd", {}, [paramsText(r.params), sizeText(r.size_gb)].filter(Boolean).join(" · ") || "unknown"),
        h("dt", {}, "skill"), h("dd", {}, "import-model")),
      row);
    slot.replaceChildren(notice);
    notice.scrollIntoView({ block: "nearest" });
  }

  // ---- setup view ----
  const logEl = h("div", { class: "log mono", role: "log", "aria-label": "Setup log" });
  const tailEl = h("div");
  const preEl = h("div"), postEl = h("div");
  logEl.append(preEl, tailEl, postEl);
  const logToggle = h("button", { type: "button", class: "ltoggle", "aria-expanded": "true" }, "Hide log");
  const stageList = h("ol", { class: "stages" });
  const rowEls = new Map();
  const after = h("div", { class: "after" });
  const actions = h("div", { class: "runacts" });
  const setupEl = h("div", { class: "setup" },
    h("div", { class: "sl" }, stageList, actions, after),
    h("div", { class: "sr" }, h("div", { class: "lbl" }, icon("terminal", 13, "currentColor", 1.8), "LIVE LOG", h("span", { class: "grow" }), logToggle), logEl));
  logToggle.addEventListener("click", () => {
    ui.logOpen = !ui.logOpen;
    setupEl.classList.toggle("nolog", !ui.logOpen);
    logToggle.textContent = ui.logOpen ? "Hide log" : "Show log";
    logToggle.setAttribute("aria-expanded", String(ui.logOpen));
  });

  function writeLog(text, kind = "", into = postEl) {
    const near = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 40;
    into.append(h("div", { class: kind }, h("span", { class: "t" }, stamp()), " ", text));
    if (near) logEl.scrollTop = logEl.scrollHeight;
  }
  const logLine = (text, kind) => writeLog(text, kind, ui.run && ui.run.phase === "check" ? preEl : postEl);
  let tailKey = "";
  function setTail(lines) {
    const key = `${lines.length}|${lines[lines.length - 1]}`;
    if (key === tailKey) return;
    tailKey = key;
    const near = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 40;
    tailEl.replaceChildren(...lines.map((l) => h("div", { class: /error|fail|traceback/i.test(l) ? "er" : "" }, l)));
    if (near) logEl.scrollTop = logEl.scrollHeight;
  }
  function freezeTail() {
    // the port job's lines become ordinary lines, so what comes after (load, smoke test) lands below them
    const lines = [...tailEl.children];
    preEl.append(...lines);
    tailEl.replaceChildren();
    tailKey = "";
  }

  function paint() {
    const run = ui.run;
    if (!run) return;
    const rows = setupStages(run, Date.now());
    const keep = new Set(rows.map((r) => r.id));
    for (const [id, el] of rowEls) if (!keep.has(id)) { el.li.remove(); rowEls.delete(id); }
    rows.forEach((r, i) => {
      let el = rowEls.get(r.id);
      if (!el) {
        el = { status: null, ring: h("span", { class: "ring" }), n: h("span", { class: "n" }), el: h("span", { class: "el" }), d: h("span", { class: "d" }), tw: h("span", { class: "cli mono tw" }), bar: h("i") };
        el.barWrap = h("div", { class: "bar" }, el.bar);
        el.li = h("li", { class: "stg pending", "data-id": r.id }, el.ring, el.n, h("span", { class: "meta" }, el.el), el.d, el.barWrap, el.tw);
        rowEls.set(r.id, el);
      }
      if (el.status !== r.status) {
        el.li.className = `stg ${r.status}`;
        el.ring.replaceChildren(...[ringIcon(r.status)].filter(Boolean));
        el.status = r.status;
      }
      if (el.n.textContent !== r.label) el.n.textContent = r.label;
      const t = elapsedText(r.elapsed);
      if (el.el.textContent !== t) el.el.textContent = t;
      if (el.d.textContent !== r.detail) { el.d.textContent = r.detail; el.d.title = r.detail; }
      if (el.tw.textContent !== r.twin) el.tw.textContent = r.twin;
      el.tw.hidden = !r.twin;
      el.barWrap.hidden = r.percent === null;
      el.bar.style.width = r.percent === null ? "0%" : `${Math.max(0, Math.min(100, r.percent))}%`;
      if (stageList.children[i] !== el.li) stageList.insertBefore(el.li, stageList.children[i] || null);
    });
  }

  function showFind() {
    ui.flow++;
    if (ui.tick) stop(ui.tick);
    ui.run = null;
    body.replaceChildren(findWrap);
    setHead(blade, { title: "Add a model", crumb: "Add a model", sub: DEFAULT_SUB });
  }
  function showSetup(title, twinText, ref) {
    ui.run.ref = ref;
    for (const el of rowEls.values()) el.li.remove();
    rowEls.clear();
    preEl.replaceChildren(); postEl.replaceChildren(); tailEl.replaceChildren(); tailKey = "";
    after.replaceChildren(); actions.replaceChildren();
    body.replaceChildren(setupEl);
    setHead(blade, { title, crumb: ref, sub: twinText });
    ui.tick = every(paint, TICK_MS);
    paint();
  }

  // ---- the flows ----
  function begin() {
    const flow = { id: ++ui.flow, aborted: false, pullId: null, portId: null };
    flow.ok = () => live() && flow.id === ui.flow && !flow.aborted;
    return flow;
  }
  const mark = (id, key, v = Date.now()) => { ui.run.times[id] = { ...(ui.run.times[id] || {}), [key]: v }; };
  function advance(to) {
    const run = ui.run, from = run.phase;
    if (from !== "check") mark(from, "end");
    run.phase = to;
    mark(to, "start");
    paint();
  }
  function endRun() {
    if (ui.tick) stop(ui.tick);
    paint();
  }
  function fail(flow, message) {
    if (!flow.ok()) return;
    const run = ui.run;
    run.failedAt = run.phase; run.error = message; mark(run.phase, "end");
    run.phase = "failed";
    logLine(message, "er");
    endRun();
    backActions("Try again", () => (run.kind === "port" ? startPort : startPull)(run.source));
  }
  function backActions(retryLabel, retry) {
    const back = h("button", { type: "button", class: "btn ghost" }, icon("search", 14, "currentColor", 1.8), "Back to search");
    back.addEventListener("click", showFind);
    const row = [back];
    if (retry) {
      const again = h("button", { type: "button", class: "btn dark" }, icon("refresh-cw", 14, "currentColor", 1.8), retryLabel);
      again.addEventListener("click", retry);
      row.unshift(again);
    }
    actions.replaceChildren(...row);
  }
  function cancelButton(flow) {
    const b = h("button", { type: "button", class: "btn ghost" }, icon("x", 14, "currentColor", 1.8), "Cancel");
    b.addEventListener("click", async () => {
      if (!flow.ok()) return;
      flow.aborted = true;
      const run = ui.run, at = run.phase;
      run.failedAt = at; mark(at, "end");
      run.phase = "cancelled";
      logLine("cancelled", "wl");
      endRun();
      backActions("Start again", () => (run.kind === "port" ? startPort : startPull)(run.source));
      try {
        const plan = cancelPlan({ ...run, phase: at }, flow);
        if (plan) await api(plan.method, plan.path);
      } catch (e) {
        toast(`Could not cancel cleanly: ${e.message}`);
      }
    });
    actions.replaceChildren(b);
  }
  /** Poll `get` until `done(job)`; tolerates a few failed polls. Returns the last job, or null when the flow ended. */
  async function follow(flow, get, ms, onJob, done) {
    let errors = 0;
    for (;;) {
      await sleep(ms);
      if (!flow.ok()) return null;
      let job;
      try { job = await get(); errors = 0; } catch (e) {
        if (++errors >= MAX_POLL_ERRORS) throw e;
        continue;
      }
      if (!flow.ok()) return null;
      onJob(job);
      if (done(job)) return job;
    }
  }

  async function startPull(r) {
    const flow = begin(), now = Date.now(), ref = r.id;
    ui.run = { kind: "pull", source: r, ref, name: ref, phase: "fetch", hw: hwDetail(r, ui.resp && ui.resp.memory), fetch: null, smoke: "", times: { check: { start: now, end: now }, fetch: { start: now } } };
    slot.replaceChildren();
    showSetup(`Setting up ${ref}`, `${twin("pull", r.repo_id || ref)} && ${twin("serve", ref)}`, ref);
    cancelButton(flow);
    logWith("check", `hardware: ${ui.run.hw}`);
    try {
      let job;
      try { job = await api("POST", "/admin/pulls", { ref }); } catch (e) {
        if (e.status !== 409) throw e;
        job = e.body && e.body.pull;
        if (!job) job = ((await api("GET", "/admin/pulls")).pulls || []).find((p) => p.ref === ref && p.status === "running");
        if (!job) throw e;
        if (flow.ok()) logLine(`${ref} is already being pulled · following it`);
      }
      if (!flow.ok()) {
        // cancelled (or the blade closed) while the POST was in flight: stop that pull on the server too
        if (flow.aborted && job && live()) api("POST", `/admin/pulls/${job.id}/cancel`).catch(() => {});
        return;
      }
      flow.pullId = job.id;
      logLine(`pull #${job.id} started for ${ref}`);
      const seen = new Map();
      const onJob = (j) => {
        ui.run.fetch = j;
        for (const s of j.steps || []) {
          // download detail changes every second: log it at every tenth of the bytes, not every tick
          const key = s.id === "download" && s.state === "now" ? `now|${Math.floor((j.percent || 0) / 10)}` : `${s.state}|${s.detail}`;
          if (seen.get(s.id) !== key && s.state !== "next" && (s.detail || s.state === "done")) {
            logLine(`${s.label || s.id}: ${s.detail || s.state}`, s.state === "done" ? "okl" : "");
            seen.set(s.id, key);
          }
        }
        paint();
      };
      onJob(job);
      const final = ["done", "failed", "cancelled", "unsupported"].includes(job.status) ? job
        : await follow(flow, () => api("GET", `/admin/pulls/${job.id}`), PULL_POLL_MS, onJob, (j) => ["done", "failed", "cancelled", "unsupported"].includes(j.status));
      if (!final) return;
      if (final.status === "unsupported") {
        showFind();
        return showPortOffer({ ...r, fit: "port" }, { note: pullDetail(final) });
      }
      if (final.status !== "done") return fail(flow, pullDetail(final) || `the pull ${final.status}`);
      ui.run.name = final.model || ref;
      refresh();
      await bringUp(flow, ui.run.name, ref);
    } catch (e) {
      fail(flow, e.message);
    }
  }
  const logWith = (phase, text) => writeLog(text, "", phase === "check" ? preEl : postEl);

  async function startPort(r) {
    const ref = r.repo_id || r.id;
    let job;
    try { job = await api("POST", "/admin/ports", { ref }); } catch (e) {
      const c = classifyPortError(e);
      if (!live()) return;
      if (c.kind === "unavailable") return showPortOffer(r, { guided: true });
      if (c.kind === "no_sandbox") return showPortOffer(r, { refused: c.message, sandbox: true });
      if (c.kind === "refused") return showPortOffer(r, { refused: c.message });
      if (c.kind !== "follow") return toast(`Could not start the port: ${c.message}`);
      job = c.job;
    }
    if (!live()) return;
    const flow = begin(), now = Date.now();
    flow.portId = job.id;
    ui.run = { kind: "port", source: r, ref, name: job.name || ref, phase: "fetch", hw: hwDetail(r, ui.resp && ui.resp.memory), port: job, smoke: "", times: { check: { start: now, end: now }, fetch: { start: now } } };
    slot.replaceChildren();
    showSetup(`Porting ${ref.split("/").pop()}`, `${twin("port", ref)} · Claude Code with Modular’s import-model skill`, ref);
    cancelButton(flow);
    logWith("check", `hardware: ${ui.run.hw}`);
    logWith("check", "Claude Code is running Modular’s import-model skill in a sandbox. This may take a while.");
    const finished = (j) => ["done", "failed", "cancelled"].includes(j.status);
    try {
      const onJob = (j) => { ui.run.port = j; setTail(portLogLines(j)); paint(); };
      onJob(job);
      const final = finished(job) ? job : await follow(flow, () => api("GET", `/admin/ports/${job.id}`), PORT_POLL_MS, onJob, finished);
      if (!final) return;
      freezeTail();
      if (final.status !== "done") {
        const bad = (final.stages || []).filter((s) => s.status === "failed").pop();
        return fail(flow, final.message || (bad && bad.detail) || `the port ${final.status}: the log above says what was tried`);
      }
      const name = final.model;
      if (!name) return fail(flow, "the port finished but no model was registered");
      ui.run.name = name;
      refresh();
      await bringUp(flow, name, ref, true);
    } catch (e) {
      fail(flow, e.message);
    }
  }

  /** compile (load) -> serve -> smoke test -> ready, for a model that is registered. Shared by pull and port. */
  async function bringUp(flow, name, ref, served = false) {
    const run = ui.run;
    if (served) {
      // the port job already served it: confirm, then go straight to the smoke test
      run.endpoint = endpointNow();
      try {
        const m = (await api("GET", "/admin/models")).find((x) => x.name === name);
        if (!m || m.state !== "ready") await api("POST", `/admin/models/${encodeURIComponent(name)}/load`);
      } catch (e) { return fail(flow, e.message); }
      if (!flow.ok()) return;
      logLine(`serving on ${run.endpoint}`, "okl");
      return smokeTest(flow, name);
    }
    advance("compile");
    logLine(`loading ${name} · compiling the graph (${twin("serve", name)})`);
    try {
      await api("POST", `/admin/models/${encodeURIComponent(name)}/load`);
    } catch (e) { return fail(flow, e.message); }
    if (!flow.ok()) return;
    logLine("graph compiled", "okl");
    advance("serve");
    run.endpoint = endpointNow();
    try {
      const models = await api("GET", "/admin/models");
      const m = models.find((x) => x.name === name);
      if (!m || m.state !== "ready") return fail(flow, m ? `the server is ${m.state}, not ready` : `${name} is not in your models`);
    } catch (e) { return fail(flow, e.message); }
    if (!flow.ok()) return;
    logLine(`serving on ${run.endpoint}`, "okl");
    refresh();
    return smokeTest(flow, name);
  }
  async function smokeTest(flow, name) {
    const run = ui.run;
    advance("smoke");
    logLine("smoke test: asking for one short reply");
    try {
      const res = await api("POST", "/admin/try/chat", { model: name, messages: [{ role: "user", content: "Say hello in five words." }], max_tokens: 24 });
      const text = String((res.choices && res.choices[0] && res.choices[0].message && res.choices[0].message.content) || "").trim().replace(/\s+/g, " ");
      if (!text) return fail(flow, "the model answered with nothing");
      run.smoke = text.length > 80 ? `${text.slice(0, 79)}…` : text;
    } catch (e) { return fail(flow, `smoke test failed: ${e.message}`); }
    if (!flow.ok()) return;
    logLine(`reply: ${run.smoke}`, "okl");
    mark("smoke", "end");
    run.phase = "ready";
    endRun();
    refresh();
    showReady(name, run.endpoint);
  }

  function showReady(name, endpoint) {
    setHead(blade, { title: `${name} is ready`, crumb: name, sub: `${endpoint} · model “${name}”` });
    const curl = curlFor(endpoint, name);
    const secure = h("button", { type: "button", class: "btn dark" }, icon("lock", 14, "currentColor", 1.9), "Make it secure");
    secure.addEventListener("click", () => push("security", { id: name }, stack.indexOf(blade)));
    const open = h("button", { type: "button", class: "btn primary" }, "Open", h("span", { class: "nm-in" }, name), icon("chevron-right", 14, "currentColor", 2));
    open.addEventListener("click", () => push("model", { id: name }, 0));
    const another = h("button", { type: "button", class: "btn ghost" }, icon("plus", 14, "currentColor", 1.8), "Add another");
    another.addEventListener("click", showFind);
    const card = h("div", { class: "ready", role: "status" },
      h("span", { class: "pill" }, "READY · LOCALHOST"),
      h("h4", {}, `${name} is serving.`),
      h("div", { class: "urlrow" }, h("span", { class: "tag" }, "local"), h("span", { class: "u mono" }, endpoint), copyButton(() => endpoint, "Copy", "btn ghost sm")),
      h("pre", { class: "curl mono" }, curl),
      h("div", { class: "cmd-row" }, copyButton(() => curl, "Copy curl", "btn ghost sm"), h("span", { class: "cli mono" }, twin("serve", name))),
      h("p", { class: "muted small" }, "It is on localhost only. Make it secure to reach it from your phone or another machine with HTTPS and an API key."),
      h("div", { class: "acts" }, open, secure, another));
    actions.replaceChildren();
    after.replaceChildren(card);
    card.scrollIntoView({ block: "nearest" });
  }

  drawFilter();
  body.replaceChildren(findWrap);
  runSearch();
}
