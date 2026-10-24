// omxconsole, the one nav (ADR 0005): five places, each exactly once. A left rail on desktop, a bottom tab bar at
// 760 px and under: the same DOM, restyled by shell.css. The active place follows the route (pages.js).
import { h, icon } from "./core.js";
import { onRoute } from "./pages.js";

// [key, label, hash, icon, hue token]. "home" is My models; a model and Add a model belong to it.
export const PLACES = [
  ["home", "Models", "#/", "box", "var(--oq-data-1)"],
  ["keys", "Keys", "#/keys", "key-round", "var(--oq-data-3)"],
  ["gateway", "Gateway", "#/gateway", "route", "var(--oq-data-2)"],
  ["security", "Security", "#/security", "lock", "var(--oq-warn)"],
  ["settings", "Settings", "#/settings", "sliders-horizontal", "var(--oq-ink-2)"],
];
const placeOf = (route) => (route.page === "model" || route.page === "add" ? "home" : route.page);

export function mountNav(nav) {
  const items = PLACES.map(([key, label, hash, ic, hue]) =>
    h("a", { class: "it", href: hash, "data-place": key, style: { "--c": hue } }, h("span", { class: "ico" }, icon(ic, 18, "currentColor", 1.75)), h("span", { class: "lb" }, label)));
  nav.replaceChildren(
    h("a", { class: "brand", href: "#/", "aria-label": "omxconsole home", title: "My models" }, h("span", { class: "mk" }, "omx"), h("span", { class: "nm" }, "omxconsole")),
    ...items,
    h("div", { class: "grow" }),
    h("div", { class: "foot" }, location.host));
  onRoute((route) => items.forEach((a) => {
    const on = a.dataset.place === placeOf(route);
    a.classList.toggle("on", on);
    if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  }));
}
