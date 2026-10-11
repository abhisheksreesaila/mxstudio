// mxconsole, the Logs blade: one model's server log in a dark monospace pane. Follow (auto-scroll) or pause, filter,
// copy. Polls GET /admin/models/{name}/log every 2 s while open (the daemon returns the last ~8 KB as text) and
// stops on close. Log text only ever goes in as a text node.
import { register } from "./blades.js";
import { h, icon, ADMIN, store, onData, copyButton } from "./core.js";

const POLL_MS = 2000;
const MAX_LINES = 2000;

// -- pure helpers (tested under node) ------------------------------------------------------------
const ERR = /\b(error|exception|traceback|fatal|critical|out of memory|exited with code [1-9]\d*)\b/i;
const WARN = /\b(warn|warning|deprecated)\b/i;
/** "err", "warn" or "" for one log line: whole words only, so "terrorism" is not an error. */
export function classifyLine(line) {
  if (ERR.test(line)) return "err";
  if (WARN.test(line)) return "warn";
  return "";
}
/** The lines to show: blank lines dropped, a tail that was cut mid-line loses its partial first line, only the
 * last MAX_LINES kept, then the case-insensitive filter. `total` counts lines before the filter. */
export function visibleLines(text, needle, truncated) {
  let lines = String(text || "").split("\n");
  if (truncated && lines.length > 1) lines = lines.slice(1);
  lines = lines.filter((l) => l.trim() !== "");
  if (lines.length > MAX_LINES) lines = lines.slice(-MAX_LINES);
  const total = lines.length;
  const q = String(needle || "").trim().toLowerCase();
  return { lines: q ? lines.filter((l) => l.toLowerCase().includes(q)) : lines, total };
}

/** A self-rescheduling poll. `tick` is awaited, then the next one is scheduled `delay()` ms later; after stop() no further
 * tick runs, even one that was already waiting on its timer or still in flight. `timers` is injectable for tests. */
export function createPoller(tick, delay, timers = { set: (f, ms) => setTimeout(f, ms), clear: (id) => clearTimeout(id) }) {
  let alive = false, id = 0, gen = 0;
  const loop = async (mine) => {
    if (!alive || mine !== gen) return;
    try { await tick(); } catch (_) { /* a failed poll is shown by the tick itself */ }
    if (alive && mine === gen) id = timers.set(() => loop(mine), delay());
  };
  return { start() { if (!alive) { alive = true; loop(++gen); } }, stop() { alive = false; gen++; timers.clear(id); } };
}

// -- the blade -------------------------------------------------------------------------------------
register("logs", (blade) => {
  const id = blade.opts.id || "";
  let text = "", truncated = false, shownText = null, shownNeedle = null, error = "", follow = true, firstDone = false;

  const modelState = () => (store.models || []).find((m) => m.name === id);
  const running = () => { const m = modelState(); return !!m && (m.state === "ready" || m.state === "starting"); };

  const pane = h("div", { class: "lg-pane mono", role: "log", tabindex: "0", "aria-label": `${id} log` });
  const filter = h("input", { class: "lg-filter", type: "search", placeholder: "Filter lines", "aria-label": "Filter lines", spellcheck: "false", autocomplete: "off" });
  const count = h("span", { class: "lg-count mono" });
  const note = h("div", { class: "lg-note", role: "status", hidden: true });
  const followBtn = h("button", { class: "btn ghost sm", type: "button", "aria-pressed": "true" });
  const copy = copyButton(() => visibleLines(text, filter.value, truncated).lines.join("\n"), "Copy", "btn ghost sm");

  const setFollowLabel = () => {
    followBtn.replaceChildren(icon(follow ? "pause" : "play", 13), follow ? "Pause" : "Follow");
    followBtn.setAttribute("aria-pressed", String(follow));
    followBtn.title = follow ? "Stop updating and scrolling" : "Resume updating and scroll to the newest line";
  };
  const selectionInPane = () => {
    const sel = typeof getSelection === "function" ? getSelection() : null;
    return !!sel && !sel.isCollapsed && pane.contains(sel.anchorNode);
  };
  const toBottom = () => { pane.scrollTop = pane.scrollHeight; };

  const render = () => {
    const needle = filter.value;
    if (text === shownText && needle === shownNeedle && !error) return; // nothing changed: leave the DOM, scroll and selection alone
    if (selectionInPane()) return; // the user is selecting text here: don't rebuild under them (the next poll retries)
    shownText = text; shownNeedle = needle;
    const { lines, total } = visibleLines(text, needle, truncated);
    if (error) {
      pane.replaceChildren(h("div", { class: "lg-line err" }, error));
      count.textContent = "";
    } else if (!lines.length) {
      pane.replaceChildren(h("div", { class: "lg-empty" }, total ? "No line matches the filter." : running() ? "Nothing in this log yet." : `${id} has not written a log yet.`));
      count.textContent = total ? `0 of ${total} lines` : "0 lines";
    } else {
      pane.replaceChildren(...lines.map((l) => h("div", { class: `lg-line ${classifyLine(l)}`.trim() }, l)));
      count.textContent = needle.trim() ? `${lines.length} of ${total} lines` : `${total} line${total === 1 ? "" : "s"}`;
    }
    if (follow) toBottom();
  };

  const drawNote = () => {
    const m = modelState();
    let msg = "";
    if (store.models && !m) msg = `${id} is not a registered model.`;
    else if (m && !running()) msg = m.state === "crashed" ? `${id} crashed. This is the end of its log, from before it stopped.` : `${id} is not running, so this log is not growing. Start it with: mxc serve ${id}`;
    note.hidden = !msg;
    note.classList.toggle("bad", !!m && m.state === "crashed");
    if (note.textContent !== msg) note.textContent = msg;
  };

  const fetchLog = async () => {
    try {
      const res = await fetch(`/admin/models/${encodeURIComponent(id)}/log`, { headers: ADMIN });
      if (!blade.alive) return;
      if (!res.ok) { error = res.status === 404 ? `${id} is not a registered model.` : `Could not read this log: HTTP ${res.status}`; }
      else { text = await res.text(); truncated = res.headers.get("X-Log-Truncated") === "1"; error = ""; }
    } catch (e) {
      if (!blade.alive) return;
      error = `Could not read this log: ${e.message}`;
    }
    firstDone = true;
    render();
    drawNote();
  };
  const poller = createPoller(fetchLog, () => POLL_MS);

  const setFollow = (on) => {
    follow = on;
    setFollowLabel();
    if (on) { shownText = null; poller.stop(); poller.start(); toBottom(); } else poller.stop();
  };

  followBtn.addEventListener("click", () => setFollow(!follow));
  filter.addEventListener("input", render);
  // scrolling up to read means: stop chasing the newest line
  pane.addEventListener("scroll", () => {
    if (follow && firstDone && pane.scrollHeight - pane.scrollTop - pane.clientHeight > 48) setFollow(false);
  });
  setFollowLabel();

  return {
    title: "Logs",
    crumb: "Logs",
    sub: `${id} · max serve`,
    icon: "scroll-text", hue: "var(--hue-logs)", side: 620, min: 520,
    tools: [followBtn, copy],
    mount(body) {
      body.replaceChildren(h("div", { class: "lg" },
        h("div", { class: "lg-bar" },
          h("label", { class: "lg-search" }, icon("search", 14), filter), count),
        note, pane,
        h("div", { class: "lg-foot" }, h("span", { class: "cli mono" }, `mxc logs ${id} -f`), h("span", { class: "grow" }),
          h("span", { class: "small muted" }, `the last 8 KB · refreshes every ${POLL_MS / 1000} s`))));
      pane.replaceChildren(h("div", { class: "lg-empty" }, "Loading…"));
      poller.start();
      // the model's state (running or not) comes from the console's shared poll
      const off = onData(drawNote);
      blade.onClose = () => { off(); poller.stop(); };
    },
  };
});
