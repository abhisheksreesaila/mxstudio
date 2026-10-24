// omxconsole, the console's hash routes (ADR 0005, plain pages). Pure: nothing here touches the DOM (main.js applies the result).
//   #/                          My models (home)
//   #/keys  #/gateway  #/security  #/settings    the other places
//   #/add                       Add a model
//   #/models/<name>/<tab>       one model, tab = overview | benchmark | logs | notebook
// Old shapes ("#keys", "#model/<name>", "#bench/<name>", "#network", ...) still open the page they meant.
// The Python twin is report.console_hash(); tests/mxconsole/test_console_routes.py keeps the two in step.

export const PLACES = ["keys", "gateway", "security", "settings"];
export const TABS = ["overview", "benchmark", "logs", "notebook"];
const HOME = { page: "home" };

// Old page name -> where it lives now. null = My models (home); "/docs" = the API reference page.
const LEGACY = {
  overview: null, models: null, metrics: null, activity: null, ports: null, home: null,
  pull: "add", network: "gateway", daemon: "settings", api: "/docs",
};
// Old "<kind>/<model>" routes -> the model tab they mean.
const LEGACY_TAB = { model: "overview", bench: "benchmark", benchmarks: "benchmark", logs: "logs", notebook: "notebook" };

const decode = (s) => { try { return decodeURIComponent(s); } catch (_) { return s; } };

/** "#/models/org/m/logs" -> { page: "model", model: "org/m", tab: "logs" }. Anything unknown is { page: "home" }. */
export function parseRoute(hash) {
  const parts = String(hash || "").replace(/^#\/?/, "").split("/");
  const head = parts[0].toLowerCase(), rest = parts.slice(1);
  if (head === "") return HOME;
  if (head === "models") return modelRoute(rest);
  if (head in LEGACY_TAB) return legacyModel(head, rest);
  if (head in LEGACY) {
    const to = LEGACY[head];
    return to === null ? HOME : to.startsWith("/") ? { redirect: to } : { page: to };
  }
  if (head === "security") return { page: head };  // "#security/<model>" (old links) is the one Security place
  if (head === "add" || PLACES.includes(head)) return rest.length ? HOME : { page: head };
  return HOME;
}

function modelRoute(rest) {
  const last = rest[rest.length - 1];
  const hasTab = rest.length > 1 && TABS.includes(last);
  const name = decode((hasTab ? rest.slice(0, -1) : rest).join("/"));
  if (!name || rest.some((p, i) => p === "" && i < rest.length - 1)) return HOME;
  return { page: "model", model: name, tab: hasTab ? last : "overview" };
}

function legacyModel(head, rest) {
  const tab = LEGACY_TAB[head], name = decode(rest.join("/"));
  if (name) return { page: "model", model: name, tab };
  // no model named: a benchmark opens the first model's (main.js picks it), the others are just home
  return head === "bench" || head === "benchmarks" ? { page: "model", model: null, tab, needsModel: true } : HOME;
}

/** The canonical hash for a route. Slashes in a model name stay slashes (a Hugging Face "org/repo"). */
export function hashFor(route) {
  if (!route || route.page === "home") return "#/";
  if (route.page !== "model") return `#/${route.page}`;
  return `#/models/${encodeURIComponent(route.model).replace(/%2F/gi, "/")}/${route.tab || "overview"}`;
}

/** The route a blade-style push(name, opts) means now that nothing stacks. */
export function routeForBlade(name, opts = {}) {
  const id = opts && opts.id;
  if (name === "home") return HOME;
  if (name === "model" && id) return { page: "model", model: id, tab: "overview" };
  if (id && name in { bench: 1, logs: 1, notebook: 1 }) return { page: "model", model: id, tab: name === "bench" ? "benchmark" : name };
  if (name === "add" || name === "keys" || name === "gateway" || name === "settings" || name === "security") return { page: name };
  return HOME;
}

const NAMES = { home: "My models", keys: "API keys", gateway: "Gateway", security: "Security", settings: "Settings", add: "Add a model" };
const TAB_NAMES = { benchmark: "Benchmark", logs: "Logs", notebook: "Notebook" };

/** The crumbs for a route: [{label, to?, picker?}], the last one being where you are. Pure. */
export function crumbsFor(route) {
  const home = { label: NAMES.home, to: { page: "home" }, home: true };
  if (route.page === "home") return [{ label: NAMES.home }];
  if (route.page === "add") return [home, { label: NAMES.add }];
  if (route.page !== "model") return [{ label: NAMES[route.page] }];
  if (route.tab === "overview") return [home, { label: route.model, picker: true }];
  return [home, { label: route.model, picker: true, to: { ...route, tab: "overview" } }, { label: TAB_NAMES[route.tab] }];
}
