// omxconsole, the model picker (F-053): the model's name in the top bar opens a plain list of your models. Picking one keeps the
// tab you are on (routes.pickRoute) and the model page swaps it in place. A menu under the name on desktop, a bottom sheet when the
// page is narrow. A modal dialog: Tab stays inside, Esc, the scrim, a pick or any route change closes it and focus goes back to the
// crumb. Deliberately simple: F-056 replaces it with the wide switcher (search, groups, grid), so nothing else depends on it.
import { h, icon, store, onData, colorOf } from "./core.js";
import { go, currentRoute, onRoute } from "./pages.js";
import { pickRoute } from "./routes.js";

const WORD = { ready: "running", starting: "starting", crashed: "crashed" };
/** The same cut as model-page.css's phone rules (a container of 760 px or less): the picker is a sheet when the page is. */
export const SHEET_MAX = 760;
export const usesSheet = (pageWidth) => pageWidth <= SHEET_MAX;
let open = null;  // { close } while the list is showing

const DEFAULT_ENV = { currentRoute, onRoute, go };

export function openPicker(anchor, env = DEFAULT_ENV) {
  if (open) { open.close(); return; }
  const route = env.currentRoute();
  if (route.page !== "model") return;
  const host = document.querySelector(".app") || document.body;
  const widthNow = () => (document.querySelector(".model-page .bb") || host).getBoundingClientRect().width;
  const sheet = usesSheet(widthNow());
  const list = h("div", { class: "plist", role: "listbox", "aria-label": "Your models" });
  const scrim = h("div", { class: "scrim2" });
  const panel = h("div", { class: `pmenu${sheet ? " sheet" : ""}`, role: "dialog", "aria-modal": "true", "aria-label": "Pick a model" },
    h("div", { class: "grab2" }),
    h("div", { class: "ptop" }, h("b", {}, "Your models"), h("span", { class: "stay" }, "stays on ", h("b", {}, route.tab))), list);

  function paint() {
    const models = store.models || [];
    list.replaceChildren(...(models.length ? models.map((m) => {
      const on = m.name === route.model;
      return h("button", { type: "button", class: `pr${on ? " on" : ""}`, role: "option", "aria-selected": String(on), "data-pick": m.name,
        onClick: () => { close(); if (!on) env.go(pickRoute(route, m.name)); } },
      h("span", { class: `mdot ${WORD[m.state] || "stopped"}`, style: { "--mc": colorOf(m.name) } }), h("b", { class: "mono" }, m.name),
      h("span", { class: "v" }, WORD[m.state] || "stopped"), on ? icon("check", 14, "currentColor", 2) : null);
    }) : [h("div", { class: "empty" }, store.models === null ? "Loading…" : "No models yet.")]));
  }
  function key(e) {
    if (e.key === "Escape") { e.preventDefault(); close(); return; }
    const forward = e.key === "ArrowDown" || (e.key === "Tab" && !e.shiftKey), back = e.key === "ArrowUp" || (e.key === "Tab" && e.shiftKey);
    if (!forward && !back) return;
    e.preventDefault();  // the dialog is modal: focus moves between its rows and never leaves
    const rows = [...list.querySelectorAll(".pr")];
    if (!rows.length) return;
    const at = rows.indexOf(document.activeElement);
    rows[(at + (forward ? 1 : -1) + rows.length) % rows.length].focus();
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
    setTimeout(() => { panel.remove(); scrim.remove(); }, 260);
    if (anchor && anchor.isConnected) anchor.focus();
  }
  const handle = { close };
  open = handle;
  scrim.addEventListener("click", close);
  document.addEventListener("keydown", key);
  // Stale only when the menu/sheet choice flips. A phone's keyboard or URL bar changes the height and fires resize too;
  // that must not close the list (F-056 puts a search box in it).
  function onResize() { if (usesSheet(widthNow()) !== sheet) close(); }
  window.addEventListener("resize", onResize);
  const box = anchor.getBoundingClientRect(), app = host.getBoundingClientRect();
  panel.style.setProperty("--px", `${Math.max(8, box.left - app.left)}px`);
  panel.style.setProperty("--py", `${box.bottom - app.top + 6}px`);
  paint();
  host.append(scrim, panel);
  requestAnimationFrame(() => {
    panel.classList.add("open"); scrim.classList.add("on");
    (list.querySelector(".pr.on") || list.querySelector(".pr"))?.focus();
  });
}
