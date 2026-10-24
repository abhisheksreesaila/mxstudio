// omxconsole, the top bar (ADR 0005): it only says where you are ("My models", "My models > model", a place's name),
// plus the Beacon (beacon.js). Navigation lives in the nav (nav.js); the theme toggle lives in Settings.
import { h, icon } from "./core.js";
import { onRoute, go } from "./pages.js";
import { crumbsFor } from "./routes.js";
import { openPicker } from "./picker.js";
import { mountBeacon } from "./beacon.js";
import { readTheme, safeStorage, applyTheme } from "./prefs.js";

export function mountTopbar(bar) {
  applyTheme(readTheme(safeStorage()));
  const crumbs = h("nav", { class: "crumbs", "aria-label": "Where you are" });
  const right = h("div", { class: "cx-right" });
  bar.replaceChildren(crumbs, h("div", { class: "sp" }), right);
  mountBeacon(right, (w) => { right.style.paddingRight = `${w}px`; });  // the Beacon floats over this space

  onRoute((route) => {
    const list = crumbsFor(route), last = list.length - 1;
    crumbs.replaceChildren(...list.flatMap((c, i) => {
      const cls = [i === last ? "on" : "", c.home ? "home-c" : "", c.picker ? "picker" : i === last ? "ttl" : ""].filter(Boolean).join(" ");
      // where you are is text; the model's name is the one button that is "here": it opens the model picker (picker.js)
      return [i ? h("span", { class: "sep" }, icon("chevron-right", 14, "currentColor", 2)) : null,
        c.picker ? h("button", { type: "button", class: cls, "aria-haspopup": "listbox", "aria-label": `${c.label}: pick another model`, onClick: (e) => openPicker(e.currentTarget) },
          h("span", {}, c.label), icon("chevron-down", 14, "currentColor", 2))
          : i === last ? h("span", { class: `cur ${cls}`, "aria-current": "page" }, c.label)
            : h("button", { type: "button", class: cls, onClick: () => go(c.to) }, c.label)];
    }).filter(Boolean));
  });
}
