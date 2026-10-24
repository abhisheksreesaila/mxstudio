// mxconsole, the blade engine: blades dock side by side, the newest on the right; older ones fold into
// clickable rails; the breadcrumb and Esc close back. The layout math is pure, in blade-layout.js; the
// stack rules (open, close, Esc) are pure too, in blade-stack.js.
//
// BLADE CONTRACT (what a later ticket's blade follows)
//
//   1. A blade lives in exactly one JS file and one CSS file: blade-<name>.js and blade-<name>.css (both already
//      exist as stubs, linked from index.html and allowlisted in gateway.py). Do not add other files: every new
//      file needs an allowlist edit in gateway.py, which is how parallel tickets collide.
//
//   2. Register it by name in that file:
//
//        import { register } from "./blades.js";
//        register("keys", (blade) => ({
//          title: "API keys",              // string or Node; the header's big text
//          crumb: "API keys",              // breadcrumb label (defaults to title when that is a string)
//          sub: "one line under the title",// string or Node, optional
//          icon: "key-round", hue: "var(--hue-keys)",     // header icon (icons.js) in its section hue
//          side: 600, narrow: 0, min: 520, wide: false,   // widths in px; wide:true folds everything to its left
//          tools: [node, ...],             // header buttons, optional
//          mount(body, blade) { ... },     // fill `body` with a Node tree built by h()/s(); data as text nodes
//        }));
//
//   3. `blade.opts` is what push() was given. `blade.alive` turns false once the blade closes.
//
//   4. A blade opens a child next to itself with  push(name, opts, stack.indexOf(blade))  (everything right of it
//      closes first). push(name, opts, 0) opens next to My models. truncate(n) keeps the first n blades.
//
//   5. Everything started in mount (onData subscriptions, intervals, timeouts, fetch loops, AbortControllers,
//      document listeners) must be torn down in blade.onClose. onData() returns its unsubscribe function:
//          const off = onData(draw);  blade.onClose = off;
//      The engine calls onClose exactly once, newest blade first, and survives one that throws.
//
//   6. Optional hooks set on `blade`: onClose(), onMode(mode) ("full"|"side"|"narrow"|"rail"|"hidden"), redraw(), sync().
import { h, icon, reducedMotion } from "./core.js";
import { planLayout, spendable, PHONE_W } from "./blade-layout.js";
import { closeFrom, openAt, escapeTarget } from "./blade-stack.js";

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

/** One Node, a (nested) list of Nodes, a string, or nothing → a list for replaceChildren. */
export const asNodes = (v) => (Array.isArray(v) ? v.flatMap(asNodes) : v instanceof Node ? [v] : v === undefined || v === null ? [] : [String(v)]);
const crumbOf = (def) => String(def.crumb ?? (typeof def.title === "string" ? def.title : "")) || "";

/** The little square with an outline icon in the blade's hue (also used by stubs and the hardware popover). */
export function hueSquare(name, hue, size = 28) {
  return h("span", { class: "sq", style: { "--h": hue, width: `${size}px`, height: `${size}px` } }, icon(name, 16, "currentColor", 1.75));
}

// F-052: pages.js now shows one blade at a time (makeBlade + setNavigator). mountJourney, layout, onStackChange and the
// stacking half of push/truncate are no longer used by the console; F-057 deletes them with blade-stack.js and blade-layout.js.
export function mountJourney(el) {
  journey = el;
  if (typeof ResizeObserver !== "undefined") new ResizeObserver(() => layout()).observe(el);
  window.addEventListener("resize", layout);
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || e.defaultPrevented) return;
    const to = escapeTarget(stack.length);
    if (to === null || document.querySelector("dialog[open]")) return;
    e.preventDefault();
    truncate(to);
  });
}

/** Pages (pages.js) take over navigation: while a navigator is set, push/truncate ask it to go somewhere instead of stacking. */
let navigator = null;
export function setNavigator(nav) { navigator = nav; }

/** Build a blade's element and header from its factory, without docking it anywhere. `closable` adds the x button. */
export function makeBlade(name, opts = {}, closable = false) {
  const factory = factories.get(name);
  if (!factory) throw new Error(`no blade registered as "${name}"`);
  const blade = { name, opts, alive: true, mode: null, railAt: 0 };
  const def = factory(blade);
  Object.assign(blade, { crumb: crumbOf(def), wide: !!def.wide, side: def.side || 600, narrow: def.narrow || 0, min: def.min ?? 520 });
  const titleEl = h("div", { class: "t" }), subEl = h("div", { class: "s" });
  const toolsEl = h("div", { class: "tools" }, asNodes(def.tools));
  const closeBtn = closable ? h("button", { class: "x", type: "button", "aria-label": "Close", title: "Close (Esc)" }, icon("x", 16, "currentColor", 2)) : null;
  const header = h("header", { class: "bh" }, hueSquare(def.icon || "box", def.hue || "var(--accent)", 36), h("div", { class: "tt" }, titleEl, subEl), toolsEl, closeBtn);
  const body = h("div", { class: "bb" });
  const el = h("section", { class: `blade oq-flat b-${name} enter`, "aria-label": blade.crumb || name }, header, body);
  Object.assign(blade, { el, body, titleEl, subEl, toolsEl, header });
  setText(titleEl, def.title);
  setText(subEl, def.sub);
  return { blade, def, closeBtn };
}

/** Open a blade. `at` is the index it docks after (default: after the newest); everything to its right closes. */
export function push(name, opts = {}, at = stack.length - 1) {
  if (navigator) return navigator.push(name, opts);
  const { blade, def, closeBtn } = makeBlade(name, opts, stack.length > 0);
  const { el, header, body } = blade;
  const back = () => { const i = stack.indexOf(blade); if (i < stack.length - 1) truncate(i + 1); };
  if (closeBtn) closeBtn.addEventListener("click", (e) => { e.stopPropagation(); truncate(stack.indexOf(blade)); });
  // like Azure: the header of a blade that has others to its right closes those; a rail brings its blade back
  header.addEventListener("click", (e) => {
    if (e.target.closest("button") || e.timeStamp <= blade.railAt) return;
    back();
  });
  header.addEventListener("keydown", (e) => {
    if (e.target === header && (e.key === "Enter" || e.key === " ") && blade.mode === "rail") { e.preventDefault(); back(); }
  });
  el.addEventListener("click", (e) => {
    if (!el.classList.contains("rail") || e.timeStamp <= blade.railAt || e.target.closest("[data-id],[data-act]")) return;
    back();
  });
  el.addEventListener("animationend", () => el.classList.remove("enter"), { once: true });
  openAt(stack, blade, at, (gone) => gone.el.remove());
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

/** Keep the first n blades (the home blade always stays) and close the rest. */
export function truncate(n) {
  if (navigator) return navigator.truncate(n);
  closeFrom(stack, n, (gone) => gone.el.remove());
  layout();
}

export function layout() {
  if (!journey || !stack.length) return;
  const width = journey.clientWidth || window.innerWidth, room = spendable(width, stack.length);
  const modes = planLayout(stack, room, PHONE_W - (width - room));
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
    // a folded blade is a button: focusable, and Enter brings it back
    if (mode === "rail") { blade.header.setAttribute("tabindex", "0"); blade.header.setAttribute("role", "button"); blade.header.setAttribute("aria-label", `Back to ${blade.crumb}`); }
    else { blade.header.removeAttribute("tabindex"); blade.header.removeAttribute("role"); blade.header.removeAttribute("aria-label"); }
    blade.header.title = mode === "rail" ? `Back to ${blade.crumb}` : mode === "side" || mode === "narrow" ? "Close what's open to the right" : "";
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
