// mxconsole, the blade engine: blades dock side by side, the newest on the right; older ones fold into
// clickable rails; the breadcrumb and Esc close back. The layout math is pure, in blade-layout.js.
//
// BLADE CONTRACT (what a later ticket's blade-*.js follows):
//
//   import { register } from "./blades.js";
//   register("keys", (blade) => ({
//     title: "API keys",              // string or Node; the header's big text
//     crumb: "API keys",              // the breadcrumb label (defaults to title when that is a string)
//     sub: "one line under the title",// string or Node, optional
//     icon: "key-round", hue: "var(--hue-keys)",     // header icon in its section hue
//     side: 600, narrow: 0, min: 520, wide: false,   // widths in px; wide:true folds everything to its left
//     tools: [node, ...],             // header buttons, optional
//     mount(body, blade) { ... },     // fill `body` (a Node tree built with h()/s(); text as text nodes)
//   }));
//
//   `blade.opts` is what push() was given. `blade.alive` turns false when the blade closes. Optional hooks the
//   factory may set on `blade`: onClose(), onMode(mode) ("full"|"side"|"narrow"|"rail"), redraw(), sync().
//   Open one with push("name", opts, atIndex); close back to n blades with truncate(n).
import { h, icon, reducedMotion } from "./core.js";
import { planLayout } from "./blade-layout.js";

const factories = new Map();
export const stack = [];
let journey = null;
const listeners = new Set();

/** Register a blade type. Later registrations of the same name replace the earlier one. */
export function register(name, factory) {
  factories.set(name, factory);
}
export const isRegistered = (name) => factories.has(name);
/** Called after every push/truncate/layout with the stack; returns an unsubscribe function. */
export function onStackChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
const notify = () => listeners.forEach((fn) => fn(stack));

const asNodes = (v) => (v instanceof Node ? [v] : v === undefined || v === null ? [] : [String(v)]);
const crumbOf = (def) => String(def.crumb ?? (typeof def.title === "string" ? def.title : "")) || "";

/** The little square with an outline icon in the blade's hue (also used by stubs and the hardware popover). */
export function hueSquare(name, hue, size = 28) {
  return h("span", { class: "sq", style: { "--h": hue, width: `${size}px`, height: `${size}px` } }, icon(name, 16, "currentColor", 1.75));
}

export function mountJourney(el) {
  journey = el;
  if (typeof ResizeObserver !== "undefined") new ResizeObserver(() => layout()).observe(el);
  window.addEventListener("resize", layout);
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || e.defaultPrevented || stack.length < 2) return;
    if (document.querySelector("dialog[open]")) return;
    e.preventDefault();
    truncate(stack.length - 1);
  });
}

/** Open a blade. `at` is the index it docks after (default: after the newest); everything to its right closes. */
export function push(name, opts = {}, at = stack.length - 1) {
  const factory = factories.get(name);
  if (!factory) throw new Error(`no blade registered as "${name}"`);
  closeFrom(at + 1);
  const blade = { name, opts, alive: true, mode: null, railAt: 0 };
  const def = factory(blade);
  Object.assign(blade, { crumb: crumbOf(def), wide: !!def.wide, side: def.side || 600, narrow: def.narrow || 0, min: def.min ?? 520 });
  const titleEl = h("div", { class: "t" }), subEl = h("div", { class: "s" });
  const toolsEl = h("div", { class: "tools" }, asNodes(def.tools));
  const closeBtn = stack.length ? h("button", { class: "x", type: "button", "aria-label": "Close", title: "Close (Esc)" }, icon("x", 16)) : null;
  const header = h("header", { class: "bh" }, hueSquare(def.icon || "box", def.hue || "var(--accent)"), h("div", { class: "tt" }, titleEl, subEl), toolsEl, closeBtn);
  const body = h("div", { class: "bb" });
  const el = h("section", { class: `blade b-${name} enter`, "aria-label": blade.crumb || name }, header, body);
  Object.assign(blade, { el, body, titleEl, subEl, toolsEl, header });
  setText(titleEl, def.title);
  setText(subEl, def.sub);
  if (closeBtn) closeBtn.addEventListener("click", (e) => { e.stopPropagation(); truncate(stack.indexOf(blade)); });
  // like Azure: the header of a blade that has others to its right closes those; a rail brings its blade back
  header.addEventListener("click", (e) => {
    if (e.target.closest("button") || e.timeStamp <= blade.railAt) return;
    const i = stack.indexOf(blade);
    if (i < stack.length - 1) truncate(i + 1);
  });
  el.addEventListener("click", (e) => {
    if (!el.classList.contains("rail") || e.timeStamp <= blade.railAt || e.target.closest("[data-id],[data-act]")) return;
    truncate(stack.indexOf(blade) + 1);
  });
  el.addEventListener("animationend", () => el.classList.remove("enter"), { once: true });
  stack.push(blade);
  journey.append(el);
  if (def.mount) def.mount(body, blade);
  layout();
  return blade;
}

function setText(el, value) {
  el.replaceChildren(...asNodes(value));
}
/** Change a blade's header after it opened (e.g. once its data arrives). */
export function setHead(blade, { title, sub, crumb } = {}) {
  if (title !== undefined) setText(blade.titleEl, title);
  if (sub !== undefined) setText(blade.subEl, sub);
  if (crumb !== undefined) blade.crumb = crumb;
  notify();
}

function closeFrom(n) {
  const gone = stack.splice(Math.max(1, n));
  for (const blade of gone.reverse()) {
    blade.alive = false;
    if (blade.onClose) blade.onClose();
    blade.el.remove();
  }
}
/** Keep the first n blades (the home blade always stays) and close the rest. */
export function truncate(n) {
  closeFrom(n);
  layout();
}

export function layout() {
  if (!journey || !stack.length) return;
  const modes = planLayout(stack, journey.clientWidth || window.innerWidth);
  const last = stack.length - 1;
  stack.forEach((blade, i) => {
    const mode = modes[i], prev = blade.mode, el = blade.el;
    blade.mode = mode;
    el.classList.toggle("rail", mode === "rail");
    el.classList.toggle("hidden", mode === "hidden");
    el.classList.toggle("side", mode === "side" || mode === "narrow");
    el.classList.toggle("narrow", mode === "narrow");
    el.classList.toggle("last", i === last);
    const size = mode === "rail" ? { width: "52px", minWidth: "52px", flex: "none" }
      : mode === "narrow" ? { width: `${blade.narrow}px`, minWidth: `${blade.narrow}px`, flex: "none" }
      : mode === "side" ? { width: `${blade.side}px`, minWidth: `${blade.side}px`, flex: "none" }
      : { width: "auto", minWidth: `${modes.includes("hidden") ? 0 : blade.min}px`, flex: "1 1 0" };
    Object.assign(el.style, size);
    el.firstChild.title = mode === "rail" ? `Back to ${blade.crumb}` : mode === "side" || mode === "narrow" ? "Close what's open to the right" : "";
    if (prev !== mode && mode === "rail") blade.railAt = performance.now();
    if (prev !== mode && blade.onMode) blade.onMode(mode);
    if (blade.sync) blade.sync();
  });
  requestAnimationFrame(() => journey.scrollTo({ left: journey.scrollWidth, behavior: reducedMotion() ? "auto" : "smooth" }));
  notify();
}

/** A placeholder body for a blade whose ticket hasn't landed yet. */
export function placeholder(ticket, what) {
  return h("div", { class: "stub" }, h("div", { class: "stub-in" },
    h("div", { class: "stub-tag mono" }, `coming in ${ticket}`),
    h("p", {}, what)));
}
