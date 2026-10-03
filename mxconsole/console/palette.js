// MX Console, the ⌘K palette. Every row is a console action with its CLI twin on the right.
// Enter runs it through the admin API; ⌘C copies the command. Things the console can't do itself
// (pull a model, serve one that isn't registered) copy the command instead, and the row says so.
import { $, h, icon, store, toast, copyText, getTheme, setTheme, THEMES, isMac } from "./core.js";
import { loadModel, stopModel, setPinned, cli } from "./actions.js";
import { newKeyDialog } from "./page-keys.js";
import { exposeDialog, stopExpose } from "./page-network.js";

const PAGES = [
  ["overview", "Overview", "layout-dashboard", "mxc console overview"], ["activity", "Activity", "activity", "mxc console activity"],
  ["models", "Models", "box", "mxc console models"], ["pull", "Pull a model", "download", "mxc pull <model>"],
  ["ports", "Ports", "git-merge", "mxc port <repo>"], ["keys", "API keys", "key-round", "mxc console keys"],
  ["network", "Network", "globe", "mxc console network"], ["api", "API reference", "book-open", "mxc console api"],
  ["metrics", "Metrics", "chart-line", "mxc console metrics"], ["benchmarks", "Benchmarks", "gauge", "mxc console"],
  ["logs", "Logs", "scroll-text", "mxc logs <model> -f"], ["gateway", "Gateway", "route", "mxc status"],
  ["daemon", "Daemon", "server", "mxc daemon"],
];

/** The rows for the current state. Each: {group, icon, label, cli, run, copyOnly?, note?} */
export function buildActions() {
  const rows = [];
  const exposed = !!(store.info && store.info.lan_endpoint);
  rows.push({ group: "Suggested", icon: "plus", label: "Serve a model…", cli: "mxc serve <model>", copyOnly: true,
    note: "The console can't pull or register models: paste the command in a terminal." });
  rows.push({ group: "Suggested", icon: "key-round", label: "Create an API key…", cli: "mxc keys new <app>", run: () => { location.hash = "keys"; newKeyDialog(); } });
  rows.push(exposed
    ? { group: "Suggested", icon: "globe", label: "Stop exposing on the LAN", cli: "mxc expose --stop", run: () => stopExpose() }
    : { group: "Suggested", icon: "globe", label: "Expose on the LAN…", cli: "mxc expose", run: () => { location.hash = "network"; exposeDialog(); } });
  rows.push({ group: "Suggested", icon: "download", label: "Pull a model…", cli: "mxc pull <model>", copyOnly: true,
    note: "The console can't pull models yet: paste the command in a terminal." });
  for (const m of store.models || []) {
    const running = m.state !== "cold";
    rows.push(running
      ? { group: m.name, icon: "square", label: `Stop ${m.name}`, cli: cli.stop(m.name), run: () => stopModel(m.name) }
      : { group: m.name, icon: "play", label: `Load ${m.name}`, cli: cli.serve(m.name), run: () => loadModel(m.name) });
    rows.push(m.pinned
      ? { group: m.name, icon: "pin", label: `Let ${m.name} unload when idle`, cli: cli.unpin(m.name), run: () => setPinned(m.name, false) }
      : { group: m.name, icon: "pin", label: `Keep ${m.name} loaded`, cli: cli.pin(m.name), run: () => setPinned(m.name, true) });
    rows.push({ group: m.name, icon: "scroll-text", label: `Follow the log of ${m.name}`, cli: cli.logs(m.name), run: () => { location.hash = `models/${encodeURIComponent(m.name)}/logs`; } });
  }
  for (const [id, label, ic, twin] of PAGES) {
    rows.push({ group: "Go to", icon: ic, label, cli: twin, run: () => { location.hash = id; } });
  }
  const next = THEMES[(THEMES.indexOf(getTheme()) + 1) % THEMES.length];
  rows.push({ group: "Console", icon: "layout-dashboard", label: `Switch theme to ${next}`, cli: "", run: () => setTheme(next) });
  return rows;
}

/** Fuzzy score: a substring beats a subsequence; -1 means no match. */
export function score(query, text) {
  const q = query.toLowerCase().trim(), t = text.toLowerCase();
  if (!q) return 0;
  const at = t.indexOf(q);
  if (at >= 0) return 1000 - at * 4 - (t.length - q.length) * 0.1;
  let ti = 0, last = -2, sc = 0;
  for (const c of q.replace(/\s+/g, "")) {
    const f = t.indexOf(c, ti);
    if (f < 0) return -1;
    sc += f === last + 1 ? 3 : 1;
    last = f; ti = f + 1;
  }
  return sc;
}
export function filterActions(rows, query) {
  if (!query.trim()) return rows;
  const scored = rows.map((r) => ({ r, s: Math.max(score(query, r.label) * 1.5, score(query, `${r.group} ${r.label}`), score(query, r.cli)) }))
    .filter((x) => x.s >= 0);
  const order = [];
  for (const x of scored.sort((a, b) => b.s - a.s)) if (!order.includes(x.r.group)) order.push(x.r.group);
  return order.flatMap((g) => scored.filter((x) => x.r.group === g).map((x) => x.r));
}

let open = null;
export function openPalette(prefill = "") {
  if (open) { open.focus(prefill); return; }
  const input = h("input", { type: "text", placeholder: "Type a command or search…", "aria-label": "Command", autocomplete: "off", spellcheck: "false", value: prefill });
  const list = h("div", { class: "pal-list", role: "listbox" });
  const pal = h("div", { class: "palette", role: "dialog", "aria-modal": "true", "aria-label": "Command palette" },
    h("div", { class: "pal-input" }, icon("command", 18), input, h("span", { class: "kbd" }, "esc")),
    list,
    h("div", { class: "pal-foot mono" }, h("span", {}, h("span", { class: "kbd" }, "↑↓"), " move"), h("span", {}, h("span", { class: "kbd" }, "↵"), " run"),
      h("span", {}, h("span", { class: "kbd" }, isMac() ? "⌘C" : "Ctrl C"), " copy as CLI"), h("span", { class: "end" }, "every action is a command")));
  const overlay = h("div", { class: "overlay", onMousedown: (e) => { if (e.target === overlay) close(); } }, pal);
  let rows = [], sel = 0;

  const render = () => {
    rows = filterActions(buildActions(), input.value);
    sel = Math.min(sel, Math.max(0, rows.length - 1));
    const nodes = [];
    let group = null;
    rows.forEach((r, i) => {
      if (r.group !== group) { group = r.group; nodes.push(h("div", { class: "pal-group mono" }, group)); }
      nodes.push(h("button", { class: "pal-row", type: "button", role: "option", "aria-selected": i === sel ? "true" : "false", "data-i": i,
        onMousemove: () => { if (sel !== i) { sel = i; mark(); } }, onClick: () => run(i) },
      h("span", { class: "ic" }, icon(r.icon, 16)), h("span", {}, r.label),
      r.copyOnly ? h("span", { class: "copy-only mono" }, "copies command") : null,
      r.cli ? h("span", { class: "cli mono" }, r.cli) : null,
      i === sel ? h("span", { class: "kbd", style: "margin-left:8px" }, "↵") : null));
    });
    if (!rows.length) nodes.push(h("div", { class: "pal-empty" }, `Nothing matches “${input.value}”. Try “serve”, “key” or a model name.`));
    list.replaceChildren(...nodes);
    scrollSel();
  };
  const mark = () => { // move the selection without redrawing, so a click still lands on the row under the pointer
    list.querySelectorAll(".pal-row").forEach((el) => {
      const on = Number(el.dataset.i) === sel;
      el.setAttribute("aria-selected", on ? "true" : "false");
      const k = el.querySelector(".kbd");
      if (on && !k) el.append(h("span", { class: "kbd", style: "margin-left:8px" }, "↵"));
      if (!on && k) k.remove();
    });
    scrollSel();
  };
  const scrollSel = () => { const el = list.querySelector('[aria-selected="true"]'); if (el) el.scrollIntoView({ block: "nearest" }); };

  const before = document.activeElement;
  const close = () => {
    overlay.remove(); open = null; document.removeEventListener("keydown", onKey, true);
    if (before && before.isConnected && before.focus) before.focus();
  };
  async function run(i) {
    const r = rows[i];
    if (!r) return;
    close();
    if (r.copyOnly) {
      const ok = await copyText(r.cli);
      toast(ok ? `Copied “${r.cli}”. ${r.note}` : `Run this in a terminal: ${r.cli}`, 6500);
      return;
    }
    try { await r.run(); } catch (e) { toast(`Failed: ${e.message}`, 7000); }
  }
  async function copy(i) {
    const r = rows[i];
    if (!r || !r.cli) { toast("This action has no command"); return; }
    const ok = await copyText(r.cli);
    toast(ok ? `Copied “${r.cli}”` : `Could not reach the clipboard: ${r.cli}`);
    close();
  }
  function onKey(e) {
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); sel = Math.min(rows.length - 1, sel + 1); mark(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); sel = Math.max(0, sel - 1); mark(); }
    else if (e.key === "Enter") { e.preventDefault(); run(sel); }
    else if ((e.key === "c" || e.key === "C") && (e.metaKey || e.ctrlKey) && input.selectionStart === input.selectionEnd) { e.preventDefault(); copy(sel); }
  }
  input.addEventListener("input", () => { sel = 0; render(); });
  document.addEventListener("keydown", onKey, true);
  $("layer").append(overlay);
  open = { focus: (v) => { if (v) input.value = v; render(); input.focus(); } };
  render();
  input.focus();
  input.setSelectionRange(input.value.length, input.value.length);
}

export function installPaletteShortcut() {
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); open ? null : openPalette(); }
  });
}
