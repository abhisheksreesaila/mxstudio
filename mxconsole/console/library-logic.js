// omxconsole, the library's rules (F-055): what a search matches, what each group holds, how labels change.
// Pure functions, no DOM, so they run under node. library.js draws them; F-056's switcher reuses both.
// A group is a string key: "all", "running", "kind:<kind>" or "label:<name>" (a label called "chat" is not the kind chat).
// State is {labels: [names in your order], models: [{name, state, kind, labels}]}; every change returns a new state.

export const KINDS = [["chat", "Chat"], ["coder", "Coder"], ["vision", "Vision"], ["embeddings", "Embeddings"], ["image", "Image"]];
export const MAX_LABEL = 40;

const RANK = { ready: 0, starting: 1, crashed: 2 };
const rank = (m) => (m.state in RANK ? RANK[m.state] : 3);
export const isRunning = (m) => m.state === "ready";

/** Running first, then starting, crashed, the rest; by name within each. */
export function byState(a, b) {
  return rank(a) - rank(b) || String(a.name).localeCompare(String(b.name));
}

export function inGroup(m, group) {
  if (group === "all" || !group) return true;
  if (group === "running") return isRunning(m);
  if (group.startsWith("kind:")) return m.kind === group.slice(5);
  if (group.startsWith("label:")) return (m.labels || []).includes(group.slice(6));
  return false;
}

/** Every word must match: plain words the name (or repo) or the start of the kind; `#word` the start of a label. */
export function matchesQuery(m, q) {
  const words = String(q || "").trim().toLowerCase().split(/\s+/).filter(Boolean);
  const name = `${m.name} ${m.repo_id || ""}`.toLowerCase();
  return words.every((w) => (w.startsWith("#")
    ? (m.labels || []).some((l) => l.startsWith(w.slice(1)))
    : name.includes(w) || String(m.kind || "").startsWith(w)));
}

export function visibleModels(models, { group = "all", q = "" } = {}) {
  return models.filter((m) => inGroup(m, group) && matchesQuery(m, q)).sort(byState);
}

export function counts(models, labels) {
  return {
    all: models.length, running: models.filter(isRunning).length,
    kinds: Object.fromEntries(KINDS.map(([k]) => [k, models.filter((m) => m.kind === k).length])),
    labels: Object.fromEntries(labels.map((l) => [l, models.filter((m) => (m.labels || []).includes(l)).length])),
  };
}

// -- labelling -------------------------------------------------------------------------------------
const picked = (models, names) => models.filter((m) => names.includes(m.name));

/** Does this label sit on all, some or none of the ticked models? (A chip is on, ringed or off.) */
export function bulkState(models, names, label) {
  const on = picked(models, names).filter((m) => (m.labels || []).includes(label)).length;
  return on === 0 ? "none" : on === names.length ? "all" : "some";
}

const mapModels = (state, fn) => ({ ...state, models: state.models.map(fn) });

export const added = (state, names, label) => mapModels(state, (m) => (names.includes(m.name) && !(m.labels || []).includes(label) ? { ...m, labels: [...(m.labels || []), label] } : m));
export const removed = (state, names, label) => mapModels(state, (m) => (names.includes(m.name) && (m.labels || []).includes(label) ? { ...m, labels: m.labels.filter((l) => l !== label) } : m));

/** Tap a chip: add the label to every ticked model unless they all have it already, then remove it. */
export function toggleOn(state, names, label) {
  const action = bulkState(state.models, names, label) === "all" ? "remove" : "add";
  return { action, state: (action === "add" ? added : removed)(state, names, label) };
}
export const created = (state, name) => (state.labels.includes(name) ? state : { ...state, labels: [...state.labels, name] });
export const renamed = (state, from, to) => ({
  ...mapModels(state, (m) => ((m.labels || []).includes(from) ? { ...m, labels: m.labels.map((l) => (l === from ? to : l)) } : m)),
  labels: state.labels.map((l) => (l === from ? to : l)),
});
export const deleted = (state, name) => ({
  ...mapModels(state, (m) => ((m.labels || []).includes(name) ? { ...m, labels: m.labels.filter((l) => l !== name) } : m)),
  labels: state.labels.filter((l) => l !== name),
});
export const ordered = (state, names) => ({ ...state, labels: names });

/** What the server accepts as a label: lower case letters, digits and hyphens, up to 40. Spaces become hyphens. */
export function cleanLabel(raw) {
  return String(raw || "").toLowerCase().trim().replace(/\s+/g, "-").replace(/[^a-z0-9-]/g, "").replace(/-+/g, "-").replace(/^-|-$/g, "").slice(0, MAX_LABEL).replace(/-$/, "");
}

/** Move `name` to sit before `before` (or to the end when `before` is null). */
export function reorder(names, name, before) {
  const rest = names.filter((n) => n !== name);
  const at = before === null || before === undefined ? rest.length : rest.indexOf(before);
  rest.splice(at < 0 ? rest.length : at, 0, name);
  return rest;
}

/** A row dragged from a ticked set takes the whole set; an unticked row goes alone. */
export const dragNames = (dragged, ticked) => (ticked.includes(dragged) ? [...ticked] : [dragged]);

/** Undo a delete: the label goes back at its old place on the models that had it. */
export function restoredLabel(state, name, index, names) {
  const labels = state.labels.includes(name) ? state.labels : [...state.labels.slice(0, index), name, ...state.labels.slice(index)];
  return added({ ...state, labels }, names, name);
}
/** Undo a reorder: your labels follow the old order; labels made since go last. */
export function restoredOrder(state, previous) {
  const known = state.labels.filter((l) => previous.includes(l)).sort((a, b) => previous.indexOf(a) - previous.indexOf(b));
  return { ...state, labels: [...known, ...state.labels.filter((l) => !previous.includes(l))] };
}

/** Apply a change at once and send it. When the daemon refuses, undo just that change (`undo` is its inverse, run on
 *  the state as it is by then, so other changes and polls that landed meanwhile stay) and say why. */
export async function optimistic({ get, set, next, undo, request, onError }) {
  set(next(get()));
  try {
    await request();
    return true;
  } catch (e) {
    set(undo(get()));
    onError(e.message);
    return false;
  }
}
