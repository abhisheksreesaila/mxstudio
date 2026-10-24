// omxconsole, the model switcher (F-056): the model's name in the top bar (or `/`, Ctrl/Cmd+K on a model page) opens a wide panel over
// the content: search across the top, the library's groups column, and your models as cards (Recent first, then by kind). Picking one
// keeps the tab you are on (routes.pickRoute) and the model page swaps it in place. On a narrow page it is a full-height sheet whose
// groups are a chip row. The search, groups and label editing are library.js's, the same component My models uses; the rules are
// library-logic.js's. A modal dialog: Tab stays inside, Esc, the scrim, a pick or any route change closes it, focus returns to the crumb.
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

const typingIn = () => { const a = document.activeElement; return !!a && (/^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName) || a.isContentEditable); };
/** `/` (outside a field) or Ctrl/Cmd+K opens the switcher on a model page; anywhere else the keys are left alone. Returns the unsubscribe. */
export function listenForSwitcher(getAnchor, env = DEFAULT_ENV) {
  const onKey = (e) => {
    if (open || env.currentRoute().page !== "model") return;
    const slash = e.key === "/" && !typingIn() && !e.metaKey && !e.ctrlKey && !e.altKey;
    const k = String(e.key).toLowerCase() === "k" && (e.metaKey || e.ctrlKey);
    if (!slash && !k) return;
    const anchor = getAnchor();
    if (!anchor) return;
    e.preventDefault();
    openPicker(anchor, env);
  };
  document.addEventListener("keydown", onKey);
  return () => document.removeEventListener("keydown", onKey);
}

export function openPicker(anchor, env = DEFAULT_ENV) {
  if (open) { open.close(); return; }
  const route = env.currentRoute();
  if (route.page !== "model") return;
  const host = document.querySelector(".app") || document.body;
  const widthNow = () => (document.querySelector(".model-page .bb") || host).getBoundingClientRect().width;
  const sheet = usesSheet(widthNow());
  const recent = readRecent((env.storage || safeStorage)());
  let kb = 0, want = route.model, filtered = "all|";  // kb: the card the keyboard is on (Enter opens it); want: a model to put it on at the next paint

  const lib = createLibrary({ onChange: () => {
    const shown = `${lib.group}|${lib.q}`;  // only a new search or group puts the keyboard back on the first card (not a label load or a tick)
    if (shown !== filtered) { filtered = shown; kb = 0; want = null; }
    paint();
  }, placeholder: sheet ? `Find any of ${(store.models || []).length} models` : `Find any of ${(store.models || []).length} models by name, kind or #label` });
  const hint = lib.search.querySelector("kbd");
  if (hint) hint.textContent = "esc";
  const input = lib.search.querySelector("input");
  const list = h("div", { class: "plist", "aria-label": "Your models" });
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
    if (all[kb]) { input.setAttribute("aria-activedescendant", all[kb].getAttribute("id")); if (all[kb].scrollIntoView) all[kb].scrollIntoView({ block: "nearest" }); }
    else input.removeAttribute("aria-activedescendant");
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
    const models = store.models || [], q = lib.q.trim();
    const ctx = { metrics: store.metrics, series: store.series, info: store.info };
    const sections = switcherSections(models, { group: lib.group, q, recent });
    let n = 0;
    const heading = (s) => {
      if (s.id === "recent") return h("div", { class: "grp" }, "Recent");
      if (s.id.startsWith("kind:")) return h("div", { class: "grp" }, `${s.id === "kind:other" ? "Other" : kindName(s.id.slice(5))} · ${s.models.length}`);
      return h("div", { class: "grp" }, `${groupName(lib.group)} · ${s.models.length}${q ? ` matching “${q}”` : ""}`);
    };
    const body = sections.flatMap((s) => [heading(s), h("div", { class: "pgrid", role: "listbox", "aria-label": s.id === "recent" ? "Recent" : "Models" }, s.models.map((m) => card(m, n++, ctx)))]);
    list.replaceChildren(...(body.length ? body : [h("div", { class: "none" },
      store.models === null ? "Loading…" : !models.length ? "No models yet." : `No model matches “${q || groupName(lib.group)}”.`)]));
    mark();
  }
  function move(delta) {
    kb = Math.max(0, Math.min(cards().length - 1, kb + delta));
    mark();
  }
  const tabStops = () => [...panel.querySelectorAll("button, input")].filter((e) => !e.disabled && !e.hidden && e.getAttribute("tabindex") !== "-1");
  function key(e) {
    if (e.key === "Escape") { e.preventDefault(); close(); return; }
    const a = document.activeElement;
    if (e.key === "Tab") {  // the dialog is modal: focus moves between its controls and never leaves
      e.preventDefault();
      const stops = tabStops();
      if (!stops.length) return;
      stops[(stops.indexOf(a) + (e.shiftKey ? -1 : 1) + stops.length) % stops.length].focus();
      return;
    }
    // the cards are driven from the search box (or the panel itself on a phone): never from a rename field, a drag handle or a button,
    // which keep their own arrows and Enter
    if (a !== input && a !== panel) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp" || ((e.key === "ArrowRight" || e.key === "ArrowLeft") && a !== input)) {
      e.preventDefault();
      move(e.key === "ArrowDown" || e.key === "ArrowRight" ? 1 : -1);
    } else if (e.key === "Enter") {
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
    document.removeEventListener("keydown", key);
    window.removeEventListener("resize", onResize);
    panel.classList.remove("open"); scrim.classList.remove("on");
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
  document.addEventListener("keydown", key);
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
