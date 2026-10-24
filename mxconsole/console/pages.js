// omxconsole, pages (ADR 0005): one page at a time, chosen by the URL hash (routes.js). Each page is a blade factory
// (blade-*.js) mounted full width, so the existing screens keep their insides until F-053 and F-055 replace them.
// While this is running the blade engine does not stack: a blade's push() becomes "go to that page".
import { h, icon, store, onData } from "./core.js";
import { makeBlade, setNavigator, stack } from "./blades.js";
import { parseRoute, hashFor, routeForBlade, backUsesHistory, updatesInPlace } from "./routes.js";

const WAIT_FOR_MODELS_MS = 4000;
const listeners = new Set();
let host = null, live = null, route = parseRoute("");

export const currentRoute = () => route;
/** Called with the route after every page change (and once now); returns an unsubscribe function. */
export function onRoute(fn) {
  listeners.add(fn);
  fn(route);
  return () => listeners.delete(fn);
}

/** Which blade shows a route. A model is one page whatever its tab (model-page.js shows the tab). */
export function bladeFor(r) {
  return r.page === "model" ? { name: "model", opts: { id: r.model, tab: r.tab } } : { name: r.page, opts: {} };
}

const decode = (v) => { try { return decodeURIComponent(v); } catch (_) { return v; } };
const sameHash = (a, b) => decode(a).replace(/^#\/?/, "") === decode(b).replace(/^#\/?/, "");

/** Go to a route: one history entry, so the browser's Back returns here. */
export function go(r) {
  const hash = hashFor(r);
  if (!sameHash(location.hash, hash)) location.hash = hash;
}

function closeLive() {
  if (!live) return;
  live.alive = false;
  try { if (live.onClose) live.onClose(); } catch (e) { console.error(`page "${live.name}" failed to close cleanly`, e); }
  live.el.remove();
  live = null;
  stack.length = 0;
}

function modelsLoaded() {
  if (store.models || store.error) return Promise.resolve();
  return new Promise((resolve) => {
    const done = () => { off(); clearTimeout(t); resolve(); };
    const off = onData(() => { if (store.models || store.error) done(); });
    const t = setTimeout(done, WAIT_FOR_MODELS_MS);
  });
}

/** Back on a page that has a parent: the browser's Back when we came from inside the console, else the parent page. */
function backButton(parent) {
  return h("button", { class: "btn text sm back", type: "button", "data-act": "back", "aria-label": "Back", onClick: () => (backUsesHistory(history.state) ? history.back() : go(parent)) },
    icon("chevron-left", 15, "currentColor", 2), "Back");
}

function render(r) {
  if (live && live.name === "model" && live.go && updatesInPlace(route, r)) {  // another tab or another model: the page stays, no flash
    route = r;
    live.go(r);
    listeners.forEach((fn) => fn(route));
    return;
  }
  closeLive();
  route = r;
  const { name, opts } = bladeFor(r);
  const { blade, def } = makeBlade(name, opts, false);
  blade.el.classList.add("page");
  blade.mode = "full";
  if (r.page === "add") blade.header.prepend(backButton({ page: "home" }));
  live = blade;
  stack.push(blade);
  host.replaceChildren(blade.el);
  blade.el.addEventListener("animationend", () => blade.el.classList.remove("enter"), { once: true });
  if (def.mount) def.mount(blade.body, blade);
  listeners.forEach((fn) => fn(route));
}

/** Read the hash and show its page. A legacy "benchmark of no model in particular" waits for the list and takes the first. */
async function open(hash) {
  let r = parseRoute(hash);
  if (r.redirect) { location.replace(r.redirect); return; }
  if (r.needsModel) {
    await modelsLoaded();
    if (location.hash !== hash) return;  // the person went somewhere else while we waited: that navigation wins
    const first = store.models && store.models[0];
    r = first ? { page: "model", model: first.name, tab: r.tab } : { page: "home" };
  }
  const canon = hashFor(r);
  if (!sameHash(location.hash, canon)) history.replaceState(history.state, "", `${location.pathname}${location.search}${canon}`);  // junk or an old shape: tidy it, no history entry
  render(r);
}

export function startPages(el) {
  host = el;
  setNavigator({
    push: (name, opts) => go(routeForBlade(name, opts)),
    truncate: () => go({ page: "home" }),  // the only caller left is "this model was removed"
  });
  // Stamp every entry: {mxc: 0} = the first one (what is before it is not ours), {mxc: 1} = reached from inside the console.
  // A hashchange to an unstamped entry is a new entry we just navigated to; a stamped one is the browser going back or forward.
  if (history.state === null) history.replaceState({ mxc: 0 }, "");
  window.addEventListener("hashchange", () => {
    if (history.state === null) history.replaceState({ mxc: 1 }, "");
    open(location.hash);
  });
  return open(location.hash);
}
