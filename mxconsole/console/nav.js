// omxconsole, navigation in the top bar (ADR 0005, F-061): the four places that apply to every model (Keys, Gateway, Security,
// Settings) as quiet links on the right, folded into one menu on a narrow window; the breadcrumb as a plain trail; and `/` or
// Ctrl/Cmd+K to jump to the My models search. There is no side rail and no model switcher: to change model, go back to My models.
import { h, icon } from "./core.js";
import { onRoute, go, currentRoute } from "./pages.js";
import { crumbsFor, hashFor } from "./routes.js";

// [key, label, hash, icon, hue token]. My models is home (the logo and the breadcrumb go there), so it is not a place.
export const PLACES = [
  ["keys", "Keys", "#/keys", "key-round", "var(--oq-data-3)"],
  ["gateway", "Gateway", "#/gateway", "route", "var(--oq-data-2)"],
  ["security", "Security", "#/security", "lock", "var(--oq-warn)"],
  ["settings", "Settings", "#/settings", "sliders-horizontal", "var(--oq-ink-2)"],
];

/** The four places plus the ⋯ button that opens them as a menu on a narrow window (topbar.css decides which shows). */
export function placesEl(env = { onRoute }) {
  const links = PLACES.map(([key, label, hash, ic, hue]) =>
    h("a", { href: hash, title: label, "data-place": key, style: { "--c": hue } }, icon(ic, 15, "currentColor", 1.75), h("span", {}, label)));
  const menu = h("nav", { class: "places", id: "places", "aria-label": "For every model" }, ...links);
  const more = h("button", { type: "button", class: "more", "aria-label": "Keys, Gateway, Security, Settings", "aria-haspopup": "menu", "aria-expanded": "false", "aria-controls": "places" }, icon("ellipsis", 18, "currentColor", 2));
  const box = h("div", { class: "placebox" }, menu, more);
  const setOpen = (on) => { menu.classList.toggle("open", on); more.setAttribute("aria-expanded", String(on)); };
  more.addEventListener("click", () => setOpen(!menu.classList.contains("open")));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") setOpen(false); });
  document.addEventListener("click", (e) => { if (!box.contains(e.target)) setOpen(false); });
  env.onRoute((route) => {
    setOpen(false);
    links.forEach((a) => { if (a.getAttribute("data-place") === route.page) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current"); });
  });
  return box;
}

/** The breadcrumb as a plain trail: earlier parts are links, the last is text and where you are. No pill, no dropdown. */
export function renderCrumbs(el, route) {
  const list = crumbsFor(route), last = list.length - 1;
  el.replaceChildren(...list.flatMap((c, i) => [
    i ? h("span", { class: "sep", "aria-hidden": "true" }, icon("chevron-right", 14, "currentColor", 2)) : null,
    i === last ? h("span", { class: `here${route.page === "model" ? " mono" : ""}`, "aria-current": "page" }, c.label) : h("a", { href: hashFor(c.to) }, c.label),
  ]).filter(Boolean));
}

const isText = (el) => !!el && (/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || el.isContentEditable);
const isMultiline = (el) => !!el && (el.tagName === "TEXTAREA" || el.isContentEditable);
const DEFAULT_ENV = { currentRoute, go };
const FOCUS_TRIES = 20;

/** `/` (outside a field) and Ctrl/Cmd+K (outside a textarea or editable, so a Notebook cell keeps its own) take you to My models and
 *  focus its search. The page may take a moment to build, so the focus is retried briefly. Returns the unsubscribe. */
export function listenForSearch(env = DEFAULT_ENV) {
  const focusSearch = (tries) => {
    const input = env.currentRoute().page === "home" ? document.querySelector(".psearch input") : null;
    if (input) input.focus();
    else if (tries > 0) setTimeout(() => focusSearch(tries - 1), 50);
  };
  const onKey = (e) => {
    const a = document.activeElement;
    const slash = e.key === "/" && !isText(a) && !e.metaKey && !e.ctrlKey && !e.altKey;
    const k = String(e.key).toLowerCase() === "k" && (e.metaKey || e.ctrlKey) && !isMultiline(a);
    if (!slash && !k) return;
    e.preventDefault();
    if (env.currentRoute().page !== "home") env.go({ page: "home" });
    focusSearch(FOCUS_TRIES);
  };
  document.addEventListener("keydown", onKey);
  return () => document.removeEventListener("keydown", onKey);
}
