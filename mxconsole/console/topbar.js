// omxconsole, the top bar (ADR 0005, F-061): the logo (home) · a plain breadcrumb · the island (the Beacon, beacon.js) · the four
// places that apply to every model (nav.js). There is no side rail. The theme toggle lives in Settings.
import { h } from "./core.js";
import { onRoute } from "./pages.js";
import { placesEl, renderCrumbs, listenForSearch } from "./nav.js";
import { mountBeacon } from "./beacon.js";
import { readTheme, safeStorage, applyTheme } from "./prefs.js";

export function mountTopbar(bar) {
  applyTheme(readTheme(safeStorage()));
  const brand = h("a", { class: "brand", href: "#/", "aria-label": "omxconsole home", title: "My models" }, h("span", { class: "mk" }, "omx"), h("span", { class: "bt" }, "omxconsole"));
  const crumbs = h("nav", { class: "crumbs", "aria-label": "Where you are" });
  const right = h("div", { class: "cx-right" });
  bar.replaceChildren(brand, crumbs, h("div", { class: "sp" }), right, placesEl());
  mountBeacon(right, (w) => { right.style.paddingRight = `${w}px`; });  // the Beacon floats over this space
  listenForSearch();
  onRoute((route) => renderCrumbs(crumbs, route));
}
