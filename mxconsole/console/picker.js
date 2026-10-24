// omxconsole, the model picker (F-053): the model's name in the top bar opens a plain list of your models. Picking one keeps the
// tab you are on (routes.pickRoute) and the model page swaps it in place. A menu under the name on desktop, a bottom sheet on a
// phone. Deliberately simple: F-056 replaces it with the wide switcher (search, groups, grid), so nothing else depends on it.
import { h, icon, store, onData, colorOf } from "./core.js";
import { go, currentRoute } from "./pages.js";
import { pickRoute } from "./routes.js";

const WORD = { ready: "running", starting: "starting", crashed: "crashed" };
let open = null;  // { close } while the list is showing

/** Close the picker if it is open (also called when the route changes under it). */
export function closePicker() {
  if (open) open.close();
}

export function openPicker(anchor) {
  if (open) { open.close(); return; }
  const route = currentRoute();
  if (route.page !== "model") return;
  const host = document.querySelector(".app") || document.body;
  const list = h("div", { class: "plist", role: "listbox", "aria-label": "Your models" });
  const scrim = h("div", { class: "scrim2" });
  const panel = h("div", { class: "pmenu", role: "dialog", "aria-label": "Pick a model" },
    h("div", { class: "grab2" }),
    h("div", { class: "ptop" }, h("b", {}, "Your models"), h("span", { class: "stay" }, "stays on ", h("b", {}, route.tab))), list);

  function paint() {
    const models = store.models || [];
    list.replaceChildren(...(models.length ? models.map((m) => {
      const on = m.name === route.model;
      return h("button", { type: "button", class: `pr${on ? " on" : ""}`, role: "option", "aria-selected": String(on), "data-pick": m.name,
        onClick: () => { close(); if (!on) go(pickRoute(route, m.name)); } },
      h("span", { class: `mdot ${WORD[m.state] || "stopped"}`, style: { "--mc": colorOf(m.name) } }), h("b", { class: "mono" }, m.name),
      h("span", { class: "v" }, WORD[m.state] || "stopped"), on ? icon("check", 14, "currentColor", 2) : null);
    }) : [h("div", { class: "empty" }, store.models === null ? "Loading…" : "No models yet.")]));
  }
  function key(e) {
    if (e.key === "Escape") { e.preventDefault(); close(); return; }
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    const rows = [...list.querySelectorAll(".pr")];
    if (!rows.length) return;
    e.preventDefault();
    const at = rows.indexOf(document.activeElement);
    rows[(at + (e.key === "ArrowDown" ? 1 : -1) + rows.length) % rows.length].focus();
  }
  const off = onData(paint);
  function close() {
    open = null;
    off();
    document.removeEventListener("keydown", key);
    panel.classList.remove("open"); scrim.classList.remove("on");
    setTimeout(() => { panel.remove(); scrim.remove(); }, 260);
    if (anchor && anchor.isConnected) anchor.focus();
  }
  open = { close };
  scrim.addEventListener("click", close);
  document.addEventListener("keydown", key);
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
