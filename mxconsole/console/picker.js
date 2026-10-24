// omxconsole, the model switcher (F-056): the model's name in the top bar (or `/`, Ctrl/Cmd+K on a model page) opens a wide panel over
// the content: search across the top, the library's groups column (plus Recent), and your models as cards (Recent first, then by kind).
// Picking one keeps the tab you are on (routes.pickRoute) and the model page swaps it in place. On a narrow page it is a full-height
// sheet whose groups are a chip row. The search, groups and label editing are library.js's, the same component My models uses; the
// rules are library-logic.js's. A modal dialog: Tab stays inside, Esc, the scrim, a pick or any route change closes it, focus returns
// to the crumb. The search is a combobox over ONE listbox of cards (sections are groups); the keyboard highlight is its active descendant.
import { h, icon, store, onData, colorOf } from "./core.js";
import { go, currentRoute, onRoute } from "./pages.js";
import { pickRoute } from "./routes.js";
import { createLibrary, groupName, kindName, tagsEl } from "./library.js";
import { switcherSections, pushRecent } from "./library-logic.js";
import { modelView } from "./blade-home.js";
import { readRecent, writeRecent, safeStorage } from "./prefs.js";

const WORD = { ready: "running", starting: "starting", crashed: "crashed" };
/** The same cut as model-page.css's phone rules (a container of 760 px or less): the switcher is a sheet when the page is. */
export const SHEET_MAX = 760;
export const usesSheet = (pageWidth) => pageWidth <= SHEET_MAX;
let open = null;  // { close } while the switcher is showing

const DEFAULT_ENV = { currentRoute, onRoute, go, storage: safeStorage };

/** Call for every route: visiting a model puts it first in Recent (the last three, kept in this browser). */
export function rememberVisit(route, storage = safeStorage()) {
  if (route.page !== "model" || !storage) return;
  writeRecent(storage, pushRecent(readRecent(storage), route.model));
}

const isText = (el) => !!el && (/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || el.isContentEditable);
const isMultiline = (el) => !!el && (el.tagName === "TEXTAREA" || el.isContentEditable);
/** `/` (outside a field) opens the switcher on a model page; Ctrl/Cmd+K does too, from anywhere but a multi-line editor (a Notebook
 *  cell keeps its own Ctrl+K). Anywhere else the keys are left alone. Returns the unsubscribe. */
export function listenForSwitcher(getAnchor, env = DEFAULT_ENV) {
  const onKey = (e) => {
    if (open || env.currentRoute().page !== "model") return;
    const a = document.activeElement;
    const slash = e.key === "/" && !isText(a) && !e.metaKey && !e.ctrlKey && !e.altKey;
    const k = String(e.key).toLowerCase() === "k" && (e.metaKey || e.ctrlKey) && !isMultiline(a);
    if (!slash && !k) return;
    const anchor = getAnchor();
    if (!anchor) return;
    e.preventDefault();
    openPicker(anchor, env);
  };
  document.addEventListener("keydown", onKey);
  return () => document.removeEventListener("keydown", onKey);
}

/** How many cards sit side by side in a grid (from its computed columns); 1 where that cannot be known. */
function columnsOf(grid) {
  try { return Math.max(1, String(getComputedStyle(grid).gridTemplateColumns).trim().split(/\s+/).filter((c) => c && c !== "none").length); } catch (_) { return 1; }
}

export function openPicker(anchor, env = DEFAULT_ENV) {
  if (open) { open.close(); return; }
  const route = env.currentRoute();
  if (route.page !== "model") return;
  const host = document.querySelector(".app") || document.body;
  const widthNow = () => (document.querySelector(".model-page .bb") || host).getBoundingClientRect().width;
  const sheet = usesSheet(widthNow());
  const recent = readRecent((env.storage || safeStorage)());
  // kb: the card the keyboard is on (Enter opens it); want: a model to put it on at the next paint; follow: scroll it into view at the
  // next paint (only when the keyboard, a search or a group moved it: a poll must never move what you scrolled to)
  let kb = 0, want = route.model, filtered = "all|", follow = true, shownSig = "", dragging = false, stale = false;

  const lib = createLibrary({ recent, onChange: () => {
    const shown = `${lib.group}|${lib.q}`;  // only a new search or group puts the keyboard back on the first card (not a label load or a tick)
    if (shown !== filtered) { filtered = shown; kb = 0; want = null; follow = true; list.scrollTop = 0; }
    paint();
  }, placeholder: sheet ? `Find any of ${(store.models || []).length} models` : `Find any of ${(store.models || []).length} models by name, kind or #label` });
  const hint = lib.search.querySelector("kbd");
  if (hint) hint.textContent = "esc";
  const input = lib.search.querySelector("input");
  const list = h("div", { class: "plist", id: "pk-list", role: "listbox", "aria-label": "Your models" });
  for (const [k, v] of [["role", "combobox"], ["aria-controls", "pk-list"], ["aria-expanded", "true"], ["aria-autocomplete", "list"], ["aria-haspopup", "listbox"]]) input.setAttribute(k, v);
  list.addEventListener("dragstart", () => { dragging = true; });  // a card being dragged onto a label must not be rebuilt under the pointer
  list.addEventListener("dragend", () => { dragging = false; if (stale) { stale = false; paint(); } });
  const scrim = h("div", { class: "scrim2" });
  const panel = h("div", { class: `pmenu${sheet ? " sheet" : ""}`, role: "dialog", "aria-modal": "true", "aria-label": "Pick a model", tabindex: "-1" },
    h("div", { class: "grab2" }),
    h("div", { class: "ptop" }, lib.search, h("span", { class: "stay" }, "Switching keeps you on ", h("b", {}, route.tab))),
    h("div", { class: "lib" }, lib.groups, list),
    h("div", { class: "pfoot" }, h("button", { type: "button", class: "fb", "data-act": "add-model", onClick: () => { close(); env.go({ page: "add" }); } }, icon("plus", 14), "Add a model"),
      h("span", { class: "hint" }, "↑ ↓ move · Enter open · drag a model onto a label")));

  const cards = () => [...list.querySelectorAll(".pr")];
  const nameOf = (c) => c.getAttribute("data-pick");
  function mark() {
    const all = cards();
    if (want) { kb = Math.max(0, all.findIndex((c) => nameOf(c) === want)); want = null; }
    kb = Math.min(kb, Math.max(0, all.length - 1));
    all.forEach((c, i) => c.classList.toggle("kb", i === kb));
    if (all[kb]) {
      input.setAttribute("aria-activedescendant", all[kb].getAttribute("id"));
      if (follow && all[kb].scrollIntoView) all[kb].scrollIntoView({ block: "nearest" });
    } else input.removeAttribute("aria-activedescendant");
    follow = false;
  }
  function card(m, n, ctx) {
    const on = m.name === route.model, tps = modelView(m, ctx).tps;
    const el = h("button", { type: "button", class: `pr${on ? " on" : ""}`, role: "option", id: `pk-${n}`, tabindex: "-1", "aria-selected": String(on), "data-pick": m.name,
      onClick: () => { close(); if (!on) env.go(pickRoute(route, m.name)); } },
    h("span", { class: `mdot ${WORD[m.state] || "stopped"}`, style: { "--mc": colorOf(m.name) } }),
    h("span", { class: "nm2" }, h("b", {}, m.name), tagsEl(m)),
    h("span", { class: "v" }, tps ? `${tps} tok/s` : WORD[m.state] || "stopped"));
    lib.draggable(el, m.name);
    return el;
  }
  function paint() {
    lib.update();
    if (dragging) { stale = true; return; }
    const models = store.models || [], q = lib.q.trim();
    const ctx = { metrics: store.metrics, series: store.series, info: store.info };
    const sections = switcherSections(models, { group: lib.group, q, recent });
    // a poll that changed nothing you can see leaves the cards (and your scroll position) alone
    const sig = JSON.stringify([lib.group, q, store.models === null, models.length, sections.map((s) => [s.id, s.models.map((m) => [m.name, m.state, m.kind, m.labels, modelView(m, ctx).tps])])]);
    if (sig !== shownSig) {
      shownSig = sig;
      const top = list.scrollTop;
      let n = 0;
      const title = (s) => (s.id === "recent" ? "Recent" : s.id.startsWith("kind:") ? `${s.id === "kind:other" ? "Other" : kindName(s.id.slice(5))} · ${s.models.length}`
        : `${groupName(lib.group)} · ${s.models.length}${q ? ` matching “${q}”` : ""}`);
      const body = sections.map((s) => h("div", { role: "group", "aria-label": title(s) }, h("div", { class: "grp", "aria-hidden": "true" }, title(s)),
        h("div", { class: "pgrid" }, s.models.map((m) => card(m, n++, ctx)))));
      list.replaceChildren(...(body.length ? body : [h("div", { class: "none", role: "presentation" },
        store.models === null ? "Loading…" : !models.length ? "No models yet." : `No model matches “${q || groupName(lib.group)}”.`)]));
      list.scrollTop = top;
    }
    mark();
  }
  /** Move the highlight: Left/Right one card; Up/Down one row (the grid's columns), into the next section when a row runs out. */
  function move(dir) {
    const grids = [...list.querySelectorAll(".pgrid")].map((g) => ({ g, n: [...g.children].length, cols: columnsOf(g) }));
    let start = 0;
    grids.forEach((s) => { s.start = start; start += s.n; });
    const at = grids.findIndex((s) => kb >= s.start && kb < s.start + s.n);
    if (at < 0) return;
    const s = grids[at], idx = kb - s.start, col = idx % s.cols;
    let next = kb;
    if (dir === "right") next = Math.min(start - 1, kb + 1);
    else if (dir === "left") next = Math.max(0, kb - 1);
    else if (dir === "down") {
      if (idx + s.cols < s.n) next = kb + s.cols;
      else if (Math.floor(idx / s.cols) < Math.floor((s.n - 1) / s.cols)) next = s.start + s.n - 1;  // a short last row: its last card
      else if (grids[at + 1]) next = grids[at + 1].start + Math.min(col, grids[at + 1].n - 1);
    } else if (dir === "up") {
      if (idx - s.cols >= 0) next = kb - s.cols;
      else if (grids[at - 1]) { const p = grids[at - 1]; next = p.start + Math.min(Math.floor((p.n - 1) / p.cols) * p.cols + col, p.n - 1); }
    }
    kb = next; follow = true;
    mark();
  }
  const tabStops = () => [...panel.querySelectorAll("button, input")].filter((e) => !e.disabled && !e.hidden && e.getAttribute("tabindex") !== "-1");
  // Runs in the capture phase, before the field under the cursor: so it can see which field Esc was pressed in.
  function key(e) {
    const a = document.activeElement;
    if (e.key === "Escape") {
      if (isText(a) && a !== input) return;  // New label, rename: those fields handle Esc themselves and the switcher stays
      e.preventDefault();
      if (a === input && input.value) { if (e.stopPropagation) e.stopPropagation(); lib.clearSearch(); return; }  // first Esc clears the search, the next closes
      close();
      return;
    }
    if (e.key === "Tab") {  // the dialog is modal: focus moves between its controls and never leaves
      e.preventDefault();
      const stops = tabStops();
      if (!stops.length) return;
      const at = stops.indexOf(a), back = !!e.shiftKey;
      stops[at < 0 ? (back ? stops.length - 1 : 0) : (at + (back ? -1 : 1) + stops.length) % stops.length].focus();
      return;
    }
    if (e.key === "/" && !isText(a) && !e.metaKey && !e.ctrlKey) { e.preventDefault(); lib.focusSearch(); return; }  // a hardware keyboard on a phone
    // the cards are driven from the search box (or the panel itself on a phone): never from a rename field, a drag handle or a button,
    // which keep their own arrows and Enter
    if (a !== input && a !== panel) return;
    const dirs = { ArrowDown: "down", ArrowUp: "up", ArrowRight: "right", ArrowLeft: "left" };
    if (dirs[e.key] && !((e.key === "ArrowRight" || e.key === "ArrowLeft") && a === input)) { e.preventDefault(); move(dirs[e.key]); }
    else if (e.key === "Enter") {
      const on = cards()[kb];
      if (on) { e.preventDefault(); on.click(); }
    }
  }
  const offData = onData(paint);
  const offRoute = env.onRoute((r) => { if (r !== route) close(); });  // Back, a hash edit, a pick: the list is stale once the route moved
  function close() {
    if (open !== handle) return;
    open = null;
    offData(); offRoute();
    document.removeEventListener("keydown", key, true);
    window.removeEventListener("resize", onResize);
    panel.classList.remove("open"); scrim.classList.remove("on");
    input.setAttribute("aria-expanded", "false");
    if (anchor && anchor.isConnected) anchor.focus();
    setTimeout(() => {
      panel.remove(); scrim.remove();
      // a pick re-draws the top bar and the old crumb is gone: put focus on the new one, unless the person has moved on
      const crumb = document.querySelector(".crumbs .picker"), at = document.activeElement;
      if (crumb && (!at || at === document.body)) crumb.focus();
    }, 260);
  }
  const handle = { close };
  open = handle;
  scrim.addEventListener("click", close);
  document.addEventListener("keydown", key, true);
  // Stale only when the menu/sheet choice flips. A phone's keyboard or URL bar changes the height and fires resize too;
  // that must not close the switcher while someone is typing in its search.
  function onResize() { if (usesSheet(widthNow()) !== sheet) close(); }
  window.addEventListener("resize", onResize);
  const area = document.querySelector("#journey") || anchor, box = area.getBoundingClientRect(), app = host.getBoundingClientRect();
  panel.style.setProperty("--px", `${Math.max(8, box.left - app.left + 12)}px`);
  panel.style.setProperty("--py", `${Math.max(8, box.top - app.top)}px`);
  paint();
  host.append(scrim, panel);
  requestAnimationFrame(() => {
    panel.classList.add("open"); scrim.classList.add("on");
    if (sheet) panel.focus(); else lib.focusSearch();  // not on a phone: focusing the search would pop the keyboard over the list
  });
}
