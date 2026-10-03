// mxconsole, shell: top bar, menu, status bar, banner and the hash router.
import { $, h, s, icon, chip, store, onData, uptime, fmt, HUE, getTheme, cycleTheme, reducedMotion, assignColours, isMac } from "./core.js";
import { openPalette } from "./palette.js";

export const NAV = [
  [null, [["overview", "Overview", "layout-dashboard", "overview"], ["activity", "Activity", "activity", "activity"]]],
  ["Models", [["models", "Models", "box", "models"], ["pull", "Pull a model", "download", "pull"], ["ports", "Ports", "git-merge", "ports"]]],
  ["Access", [["keys", "API keys", "key-round", "keys"], ["network", "Network", "globe", "network"], ["api", "API reference", "book-open", "api"]]],
  ["Observe", [["metrics", "Metrics", "chart-line", "metrics"], ["benchmarks", "Benchmarks", "gauge", "bench"], ["logs", "Logs", "scroll-text", "logs"]]],
  ["System", [["gateway", "Gateway", "route", "gateway"], ["daemon", "Daemon", "server", "daemon"]]],
];
export const PAGE_META = Object.fromEntries(NAV.flatMap(([, items]) => items.map(([id, label, ic, hue]) => [id, { label, icon: ic, hue: HUE[hue] }])));

export function parseRoute() {
  const [page, ...rest] = (location.hash || "#overview").slice(1).split("/");
  return { page: PAGE_META[page] ? page : "overview", args: rest.map(decodeURIComponent) };
}

export function mountShell(pages) {
  buildTopbar();
  buildMenu();
  const view = $("view");
  let current = null, currentKey = "";

  function show() {
    const route = parseRoute();
    const key = route.page;
    if (current && key === currentKey && current.route) { // same page, new argument (e.g. another model's sheet)
      current.route(route.args);
      markMenu(route.page);
      return;
    }
    if (current && current.destroy) current.destroy();
    const meta = PAGE_META[route.page];
    const subEl = h("div", { class: "page-sub mono" });
    const actionsEl = h("div", { class: "page-actions" });
    const body = h("div", {});
    const ctx = { args: route.args, actions: actionsEl, body, setSub: (t) => { subEl.textContent = t; } };
    document.title = `${meta.label} · mxconsole`;
    view.replaceChildren(
      h("div", { class: "page-head" }, h("div", { class: "titles" }, h("h1", { class: "page-title" }, meta.label), subEl), actionsEl), body);
    ctx.setSub(`mxc@${(store.info && store.info.host) || "local"}`);
    try {
      current = pages[route.page](ctx) || {};
    } catch (e) {
      current = {};
      body.replaceChildren(h("div", { class: "panel empty" }, h("h2", {}, "This page failed to draw"), h("p", {}, String(e && e.message || e))));
    }
    currentKey = key;
    markMenu(route.page);
    $("menu").classList.remove("open");
    $("menu-scrim").hidden = true;
    view.classList.remove("enter");
    void view.offsetWidth; // restart the 180 ms fade + rise
    view.classList.add("enter");
    window.scrollTo(0, 0);
    if (current.update) current.update();
  }

  window.addEventListener("hashchange", show);
  onData(() => {
    try { updateChrome(); } catch (e) { console.error(e); }
    try { if (current && current.update) current.update(); } catch (e) { console.error(e); }
  });
  setInterval(() => { if (!reducedMotion()) updateStatusbar(); }, 1000);
  show();
  updateChrome();
}

// -- top bar -------------------------------------------------------------------------------------
let hostEl, dotEl, themeBtn;
function buildTopbar() {
  hostEl = h("span", {}, "mxc");
  dotEl = h("span", { class: "live-dot" });
  themeBtn = h("button", { class: "ghost", type: "button", onClick: () => { cycleTheme(); drawThemeIcon(); } });
  const menuBtn = h("button", { class: "ghost menu-toggle", type: "button", "aria-label": "Collapse the menu", "aria-expanded": "true",
    title: "Collapse or open the menu ( [ )", onClick: toggleMenu }, icon("menu", 18));
  $("menu-scrim").addEventListener("click", () => { $("menu").classList.remove("open"); $("menu-scrim").hidden = true; });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "[" || e.metaKey || e.ctrlKey || e.altKey || e.target.closest("input, textarea, select, [contenteditable]")) return;
    e.preventDefault();
    toggleMenu();
  });
  $("topbar").replaceChildren(
    menuBtn,
    h("a", { class: "brand", href: "#overview", "aria-label": "mxconsole, home" }, brandMark(26),
      h("span", { class: "wordmark", "aria-hidden": "true" }, h("b", {}, "mx"), "console")),
    h("span", { class: "crumb-sep" }, "/"),
    h("span", { class: "hostchip mono" }, dotEl, hostEl),
    h("button", { class: "search-btn", type: "button", "aria-label": "Search or run a command", onClick: () => openPalette() },
      icon("search", 15), h("span", { class: "lbl" }, "Search or run a command"), h("span", { class: "kbd" }, isMac() ? "⌘K" : "Ctrl K")),
    themeBtn,
    h("a", { class: "ghost docs", href: "#api" }, icon("book-open", 15, HUE.api), "API reference"),
    h("span", { class: "avatar", "aria-hidden": "true" }));
  drawThemeIcon();
}
// The Ligature mark: the m's last arch runs into the x's falling stroke; the dot is the daemon light.
function brandMark(size) {
  return s("svg", { viewBox: "0 0 48 48", width: size, height: size, class: "mark", "aria-hidden": "true" },
    s("rect", { width: 48, height: 48, rx: 12 }),
    s("path", { d: "M8 35V21a5.5 5.5 0 0 1 11 0v14M19 21a5.5 5.5 0 0 1 11 0l10 14M40.5 18 29.5 35" }),
    s("circle", { cx: 40.5, cy: 11.5, r: 2.6 }));
}

// Wide screens fold the menu to an icon rail and remember it; narrow ones slide it over the page.
const RAIL_KEY = "mxc.menu";
const narrow = () => window.matchMedia("(max-width: 900px)").matches;
function toggleMenu() {
  const menu = $("menu");
  if (narrow()) {
    const open = menu.classList.toggle("open");
    $("menu-scrim").hidden = !open;
    return;
  }
  const rail = menu.classList.toggle("rail");
  try { localStorage.setItem(RAIL_KEY, rail ? "rail" : "full"); } catch (e) { /* private window: just not remembered */ }
  drawMenuToggle();
}
function drawMenuToggle() {
  const rail = $("menu").classList.contains("rail");
  const btn = $("topbar").querySelector(".menu-toggle");
  btn.setAttribute("aria-expanded", rail ? "false" : "true");
  btn.setAttribute("aria-label", rail ? "Open the menu" : "Collapse the menu");
}

export function drawThemeIcon() {
  const t = getTheme();
  themeBtn.replaceChildren(icon(t === "dark" ? "moon" : t === "light" ? "sun" : "layout-dashboard", 16));
  themeBtn.setAttribute("aria-label", `Theme: ${t}. Switch theme`);
  themeBtn.title = `Theme: ${t}`;
}

// -- menu ----------------------------------------------------------------------------------------
function buildMenu() {
  const menu = $("menu");
  const nodes = [];
  for (const [label, items] of NAV) {
    if (label) nodes.push(h("div", { class: "menu-label mono" }, label));
    for (const [id, text, ic, hue] of items) {
      nodes.push(h("a", { class: "menu-item", href: `#${id}`, "data-page": id, title: text }, chip(ic, HUE[hue]), h("span", { class: "lbl" }, text)));
    }
  }
  nodes.push(h("div", { class: "menu-card" },
    h("div", { class: "t" }, icon("terminal", 14), "Every view is a command"),
    h("div", { class: "c mono" }, "mxc status", h("br"), "mxc ls --json", h("br"), "mxc metrics --range 1h")));
  menu.replaceChildren(...nodes);
  let saved = null;
  try { saved = localStorage.getItem(RAIL_KEY); } catch (e) { /* no storage: start open */ }
  menu.classList.toggle("rail", saved === "rail");
  drawMenuToggle();
}
function markMenu(page) {
  for (const a of $("menu").querySelectorAll(".menu-item")) {
    if (a.dataset.page === page) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  }
}

// -- live chrome ---------------------------------------------------------------------------------
function updateChrome() {
  assignColours(store.models);
  const down = !!store.error;
  dotEl.classList.toggle("down", down);
  if (store.info) hostEl.textContent = `mxc@${store.info.host}`;
  const banner = $("banner");
  banner.hidden = !down;
  if (down) {
    banner.replaceChildren(h("strong", {}, "The mxconsole daemon isn't answering. "), "Start it with ", h("span", { class: "mono" }, "mxc serve <model>"),
      " or ", h("span", { class: "mono" }, "mxc daemon"), ". This page retries every few seconds.");
  }
  updateStatusbar();
}

function updateStatusbar() {
  const bar = $("statusbar");
  const info = store.info || {}, down = !!store.error;
  const up = info.uptime_seconds === undefined || down ? null : info.uptime_seconds + (Date.now() - store.infoAt) / 1000;
  const gpus = (store.metrics && store.metrics.gpus) || [];
  const used = gpus.reduce((a, g) => a + g.used_mb, 0) / 1024, total = gpus.reduce((a, g) => a + g.total_mb, 0) / 1024;
  const rpm = store.metrics && !down ? store.metrics.per_minute : null;
  bar.replaceChildren(
    h("span", { class: "up" }, h("span", { class: down ? "dot down" : "dot" }), down ? "daemon not answering" : `daemon up ${uptime(up)}`),
    h("span", { class: "opt" }, info.endpoint || "http://127.0.0.1/v1"),
    h("span", { class: "opt" }, info.lan_endpoint ? `LAN ${info.lan_endpoint.replace(/\/v1$/, "")}` : "LAN off"),
    h("span", { class: "opt" }, info.max_version ? `MAX ${info.max_version}` : "MAX unknown"),
    h("span", { class: "opt" }, gpus.length ? `GPU ${fmt(used, 1)} / ${fmt(total, 0)} GB` : "no GPU"),
    h("span", {}, `${rpm === null ? "—" : fmt(rpm, 1)} req/min`),
    h("span", { class: "end opt" }, h("span", { class: "kbd" }, isMac() ? "⌘K" : "Ctrl K"), " commands"));
}
