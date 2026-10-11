// mxconsole, the console's hash routes: "#keys", "#model/<name>", "#bench/<name>" open blades; the old page names
// (overview, models, daemon, ...) still work. Pure: nothing here touches the DOM (main.js applies the result).
// The Python twin is report.console_hash(); tests/mxconsole/test_console_routes.py keeps the two in step.

// Old page name -> where it lives now. null = My models (home); "/docs" = the API reference page.
export const LEGACY = {
  overview: null, models: null, metrics: null, activity: null, ports: null, home: null,
  pull: "add", network: "gateway", daemon: "settings", benchmarks: "bench", api: "/docs",
};
const WITH_MODEL = new Set(["model", "security", "bench", "logs", "notebook"]);
const ALONE = new Set(["keys", "add", "gateway", "settings"]);

const decode = (s) => { try { return decodeURIComponent(s); } catch (_) { return s; } };

/**
 * "#bench/tiny" -> { steps: [{name:"model", opts:{id:"tiny"}}, {name:"bench", opts:{id:"tiny"}}] }.
 * Home is always open, so [] means "just My models". A route that needs a model but names none comes back as
 * { needsModel: "bench" } and main.js picks the first model (or stays home). "/docs" comes back as { redirect }.
 */
export function parseHash(hash) {
  const raw = String(hash || "").replace(/^#\/?/, "");
  const [head = "", ...rest] = raw.split("/");
  const id = decode(rest.join("/"));
  let name = head.toLowerCase();
  if (name in LEGACY) {
    const to = LEGACY[name];
    if (to === null) return { steps: [] };
    if (to.startsWith("/")) return { steps: [], redirect: to };
    name = to;
  }
  if (ALONE.has(name)) return { steps: [{ name, opts: {} }] };
  if (WITH_MODEL.has(name)) {
    if (!id) return name === "logs" || name === "model" ? { steps: [] } : { steps: [], needsModel: name };
    const model = { name: "model", opts: { id } };
    return { steps: name === "model" ? [model] : [model, { name, opts: { id } }] };
  }
  return { steps: [] };
}

/** The canonical hash for what is open: the newest blade after My models. "" when only My models is open. */
export function hashFor(stack) {
  const top = stack.length > 1 ? stack[stack.length - 1] : null;
  if (!top) return "";
  const id = top.opts && top.opts.id;
  return id ? `${top.name}/${encodeURIComponent(id).replace(/%2F/gi, "/")}` : top.name;
}
