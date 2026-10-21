// omxconsole, the top islands: brand (home), breadcrumb, the shared blades (API keys, Gateway, Settings) and the
// theme toggle, then the Beacon (beacon.js). At phone width the tools fold into one ••• menu and the breadcrumb hides.
import { h, icon } from "./core.js";
import { stack, push, truncate, onStackChange } from "./blades.js";
import { mountBeacon } from "./beacon.js";
import { readTheme, nextTheme, safeStorage, applyTheme, currentTheme, setTheme, onTheme } from "./prefs.js";

const SHARED = [["keys", "API keys", "key-round", "var(--hue-keys)"], ["gateway", "Gateway", "route", "var(--hue-gateway)"], ["settings", "Settings", "sliders-horizontal", null]];
const toggleBlade = (name) => { if (stack[1] && stack[1].name === name) truncate(2); else push(name, {}, 0); };

export function mountTopbar(bar) {
  applyTheme(readTheme(safeStorage()));

  const brand = h("button", { class: "brand-isl", type: "button", "aria-label": "omxconsole home", title: "My models", onClick: () => truncate(1) },
    h("span", { class: "mark" }, "omx"), h("span", { class: "nm" }, "omxconsole"), h("span", { class: "loc" }, "local"));
  const crumbs = h("nav", { class: "cx-crumbs", "aria-label": "Breadcrumb" });
  const nav = h("div", { class: "cx-tools" }, SHARED.map(([name, label, ic, hue]) =>
    h("button", { class: `tool${hue ? "" : " ic"}`, type: "button", "data-nav": name, title: label, "aria-label": label, style: hue ? { "--hue": hue } : {}, onClick: () => toggleBlade(name) },
      icon(ic, 16, "currentColor", 1.75), hue ? h("span", { class: "tl" }, label) : null)));
  const themeBtn = h("button", { class: "tool ic", type: "button", onClick: () => setTheme(nextTheme(currentTheme())) });
  const themeItem = h("button", { class: "mi", type: "button", role: "menuitem", onClick: () => { closeMenu(); setTheme(nextTheme(currentTheme())); } });
  const moreBtn = h("button", { class: "tool ic more", type: "button", title: "API keys, Gateway, Settings, Theme", "aria-label": "More", "aria-haspopup": "menu", "aria-expanded": "false", onClick: (e) => { e.stopPropagation(); menu.hidden ? openMenu() : closeMenu(); } },
    icon("ellipsis", 18, "currentColor", 2));
  const menu = h("div", { class: "menu", role: "menu", "aria-label": "More", hidden: true },
    SHARED.map(([name, label, ic]) => h("button", { class: "mi", type: "button", role: "menuitem", onClick: () => { closeMenu(); toggleBlade(name); } }, icon(ic, 16, "currentColor", 1.75), label)), themeItem);
  nav.append(moreBtn, themeBtn);
  const right = h("div", { class: "cx-right" }, nav, menu);
  bar.replaceChildren(brand, crumbs, right);

  // keep the breadcrumb clear of both side islands: it centres in what is left of the wider side
  const fit = () => bar.style.setProperty("--cx-side", `${Math.max(brand.offsetWidth, right.offsetWidth) + 32}px`);
  mountBeacon(right, (w) => { right.style.paddingRight = `${w + 8}px`; fit(); });
  new ResizeObserver(fit).observe(bar);

  function openMenu() { if (window.Omxqua) window.Omxqua.gentleIn(menu); else menu.hidden = false; moreBtn.setAttribute("aria-expanded", "true"); }
  function closeMenu() { if (window.Omxqua) window.Omxqua.gentleOut(menu); else menu.hidden = true; moreBtn.setAttribute("aria-expanded", "false"); }
  document.addEventListener("click", (e) => { if (!menu.hidden && !menu.contains(e.target)) closeMenu(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !menu.hidden) { e.preventDefault(); e.stopImmediatePropagation(); closeMenu(); moreBtn.focus(); } }, true);

  function paintTheme() {
    const dark = currentTheme() === "dark";
    themeBtn.replaceChildren(icon(dark ? "sun" : "moon", 16, "currentColor", 1.75));
    themeBtn.title = dark ? "Light mode" : "Dark mode";
    themeBtn.setAttribute("aria-label", themeBtn.title);
    themeBtn.setAttribute("aria-pressed", String(dark));
    themeItem.replaceChildren(icon(dark ? "sun" : "moon", 16, "currentColor", 1.75), "Theme", h("span", { class: "cur" }, dark ? "Dark" : "Light"));
  }
  onTheme(paintTheme);
  paintTheme();

  function drawCrumbs() {
    const n = stack.length;
    crumbs.replaceChildren(...stack.flatMap((b, i) => [
      i ? icon("chevron-right", 14, "currentColor", 2) : null,
      h("button", { type: "button", class: `${i === n - 1 ? "on" : ""} ${i < n - 2 ? "far" : ""}`, "aria-current": i === n - 1 ? "page" : false, onClick: () => { if (i !== n - 1) truncate(i + 1); } }, b.crumb || b.name),
    ]).filter(Boolean));
    nav.querySelectorAll(".tool[data-nav]").forEach((t) => t.classList.toggle("on", !!stack[1] && stack[1].name === t.dataset.nav));
  }
  onStackChange(drawCrumbs);
  drawCrumbs();
  fit();
}
