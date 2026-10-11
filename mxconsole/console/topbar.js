// mxconsole, the dark top bar: brand (home), breadcrumb, hardware chip + popover, the shared blades
// (API keys, Gateway, Settings), the theme toggle and the daemon dot. No left menu, no global Logs.
import { h, icon, store, onData, colorOf } from "./core.js";
import { stack, push, truncate, onStackChange, hueSquare } from "./blades.js";
import { hardwareView } from "./hardware.js";
import { system } from "./system-data.js";
import { readTheme, writeTheme, resolveTheme, nextTheme } from "./prefs.js";

const SHARED = [["keys", "API keys", "key-round", "var(--hue-keys)"], ["gateway", "Gateway", "route", "var(--hue-gateway)"], ["settings", "Settings", "sliders-horizontal", null]];

export function applyTheme(pref) {
  if (pref) document.documentElement.dataset.theme = pref;
  else delete document.documentElement.dataset.theme;
}

export function mountTopbar(bar, pop) {
  const sysDark = window.matchMedia("(prefers-color-scheme: dark)");
  let pref = readTheme(localStorage), popOpen = false;
  applyTheme(pref);

  const brand = h("button", { class: "brand", type: "button", title: "My models", onClick: () => truncate(1) }, h("span", { class: "dot" }), "mxconsole", h("small", { class: "mono" }, "local"));
  const crumbs = h("nav", { class: "bc", "aria-label": "Breadcrumb" });
  const chip = h("button", { class: "hwchip", type: "button", "aria-expanded": "false", "aria-controls": "hwpop", title: "Hardware", onClick: (e) => { e.stopPropagation(); setPop(!popOpen); } });
  const nav = h("nav", { class: "tnav", "aria-label": "Shared by every model" }, SHARED.map(([name, label, ic, hue]) =>
    h("button", { class: `tn ${hue ? "" : "icon"}`, type: "button", "data-nav": name, title: label, "aria-label": label, style: hue ? { "--hue": hue } : {},
      onClick: () => { if (stack[1] && stack[1].name === name) truncate(2); else push(name, {}, 0); } }, icon(ic, 16, "currentColor", 1.75), hue ? h("span", {}, label) : null)));
  const themeBtn = h("button", { class: "tn icon", type: "button", onClick: () => { pref = nextTheme(isDark() ? "dark" : "light"); writeTheme(localStorage, pref); applyTheme(pref); paintTheme(); } });
  const live = h("span", { class: "live" });
  bar.replaceChildren(brand, crumbs, chip, nav, themeBtn, live);

  const isDark = () => resolveTheme(pref, sysDark.matches) === "dark";
  function paintTheme() {
    const dark = isDark();
    themeBtn.replaceChildren(icon(dark ? "sun" : "moon", 16, "currentColor", 1.75));
    themeBtn.title = dark ? "Light mode" : "Dark mode";
    themeBtn.setAttribute("aria-label", themeBtn.title);
    themeBtn.setAttribute("aria-pressed", String(dark));
  }
  sysDark.addEventListener("change", paintTheme);
  paintTheme();

  function drawCrumbs() {
    const n = stack.length;
    crumbs.replaceChildren(...stack.flatMap((b, i) => [
      i ? icon("chevron-right", 12, "currentColor", 2) : null,
      h("button", { type: "button", class: `${i === n - 1 ? "cur" : ""} ${i < n - 2 ? "far" : ""}`, onClick: () => { if (i !== n - 1) truncate(i + 1); } }, b.crumb || b.name),
    ]).filter(Boolean));
    nav.querySelectorAll(".tn[data-nav]").forEach((t) => t.classList.toggle("on", !!stack[1] && stack[1].name === t.dataset.nav));
  }

  function segments(hw) {
    return hw.segments.map((seg) => h("span", { title: `${seg.name} · ${seg.gb} GB`, style: { width: `${seg.pct}%`, background: seg.name === "other" || seg.name === "in use" ? "var(--chrome-muted)" : colorOf(seg.name) } }));
  }
  function drawChip() {
    const hw = hardwareView(store.metrics && store.metrics.gpus, system.crew);
    if (store.error) chip.replaceChildren(icon("cpu", 14), h("span", {}, "daemon offline"));
    else if (!hw.present) chip.replaceChildren(icon("cpu", 14), h("span", {}, store.metrics ? "no GPU" : "…"));
    else chip.replaceChildren(icon("cpu", 14), h("span", { class: "gname" }, hw.short), h("span", { class: "vbar" }, segments(hw)), h("b", {}, hw.label));
    live.className = `live ${store.error ? "down" : ""}`;
    live.title = store.error ? "daemon not answering" : `daemon running · ${location.host}`;
    brand.firstChild.className = `dot ${store.error ? "down" : ""}`;
    if (popOpen) drawPop();
  }
  function drawPop() {
    const hw = hardwareView(store.metrics && store.metrics.gpus, system.crew);
    const legend = hw.segments.length
      ? hw.segments.flatMap((seg) => [h("i", { style: { background: seg.name === "other" || seg.name === "in use" ? "var(--faint)" : colorOf(seg.name) } }), h("span", { class: "trunc" }, seg.name), h("span", {}, `${seg.gb} GB`)])
      : [h("span", {}), h("span", {}, "nothing loaded"), h("span", {})];
    pop.replaceChildren(
      h("div", { class: "pop-head" }, hueSquare("cpu", "var(--hue-models)"), h("div", { class: "pop-title" }, h("b", {}, hw.present ? hw.name : "No NVIDIA GPU found"),
        h("div", { class: "mono muted small" }, hw.present ? `${store.info && store.info.max_version ? `MAX ${store.info.max_version} · ` : ""}${hw.totalGb} GB VRAM` : "models run on the CPU, or not at all"))),
      hw.present ? h("div", { class: "pop-body" },
        h("div", { class: "h3" }, "VRAM", h("span", { class: "r mono" }, `${hw.usedGb} / ${hw.totalGb} GB · ${hw.freeGb} free`)),
        h("div", { class: "vbar" }, segments(hw)), h("div", { class: "legend" }, legend)) : null,
      h("div", { class: "pop-foot" }, h("span", { class: "cli mono" }, "mxc status")));
  }
  function setPop(open) {
    popOpen = open;
    pop.hidden = !open;
    chip.setAttribute("aria-expanded", String(open));
    if (open) drawPop();
  }
  document.addEventListener("click", (e) => { if (popOpen && !e.target.closest("#hwpop")) setPop(false); });
  // capture phase: Esc closes the popover first, and only then the newest blade
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && popOpen) { e.preventDefault(); e.stopImmediatePropagation(); setPop(false); chip.focus(); } }, true);

  onStackChange(drawCrumbs);
  onData(drawChip);
  drawCrumbs();
  drawChip();
}
