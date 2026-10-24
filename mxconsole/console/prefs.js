// mxconsole, per-browser preferences. Pure: the storage is passed in, and a blocked storage never throws.
export const VIEW_KEY = "mxc-view";
export const THEME_KEY = "mxc-theme";
export const RECENT_KEY = "mxc-recent";

/** window.localStorage, or null when storage is blocked (reading it can throw): every caller must accept null. */
export function safeStorage() {
  try { return window.localStorage || null; } catch (_) { return null; }
}

function read(storage, key) {
  try { return storage.getItem(key); } catch (_) { return null; }
}
function write(storage, key, value) {
  try { storage.setItem(key, value); } catch (_) { /* storage blocked: the choice just won't be remembered */ }
}

/** My models shows a list by default; "cards" is the other choice. */
export function readView(storage) {
  return read(storage, VIEW_KEY) === "cards" ? "cards" : "list";
}
export function writeView(storage, view) {
  write(storage, VIEW_KEY, view === "cards" ? "cards" : "list");
}

/** null until the toggle has been used: then the system decides. (Older versions stored "contrast": ignored.) */
export function readTheme(storage) {
  const saved = read(storage, THEME_KEY);
  return saved === "light" || saved === "dark" ? saved : null;
}
export function writeTheme(storage, theme) {
  if (theme === "light" || theme === "dark") write(storage, THEME_KEY, theme);
}
export const resolveTheme = (pref, systemDark) => pref || (systemDark ? "dark" : "light");
export const nextTheme = (current) => (current === "dark" ? "light" : "dark");

// -- the one place the theme is changed ---------------------------------------------------------------
// The top-bar toggle and Settings both call setTheme(); anything that draws the theme listens with onTheme().
export const THEME_EVENT = "mxc:theme";
const systemIsDark = () => { try { return window.matchMedia("(prefers-color-scheme: dark)").matches; } catch (_) { return false; } };

/** Put a theme on the page: "light" or "dark" is explicit, null follows the system. */
export function applyTheme(pref) {
  if (pref === "light" || pref === "dark") document.documentElement.dataset.theme = pref;
  else delete document.documentElement.dataset.theme;
}
/** The theme in force right now: what the page shows (even when storage is blocked), else the remembered choice, else the system's. */
export function currentTheme() {
  const shown = document.documentElement.dataset.theme;
  return resolveTheme(shown === "light" || shown === "dark" ? shown : readTheme(safeStorage()), systemIsDark());
}
/** Choose a theme ("light" | "dark"): remember it, apply it, and tell every listener. */
export function setTheme(theme) {
  if (theme !== "light" && theme !== "dark") return;
  const storage = safeStorage();
  if (storage) writeTheme(storage, theme);
  applyTheme(theme);
  window.dispatchEvent(new CustomEvent(THEME_EVENT, { detail: theme }));
}
/** Run fn whenever the theme changes (by setTheme, or by the system while no choice is saved). Returns the unsubscribe. */
export function onTheme(fn) {
  const mq = window.matchMedia("(prefers-color-scheme: dark)");
  window.addEventListener(THEME_EVENT, fn);
  mq.addEventListener("change", fn);
  return () => { window.removeEventListener(THEME_EVENT, fn); mq.removeEventListener("change", fn); };
}

/** The last models visited, newest first (the switcher's Recent). Anything that is not a list of names reads as empty. */
export function readRecent(storage) {
  try { const v = JSON.parse(read(storage, RECENT_KEY) || "[]"); return Array.isArray(v) ? v.filter((n) => typeof n === "string") : []; } catch (_) { return []; }
}
export function writeRecent(storage, names) {
  write(storage, RECENT_KEY, JSON.stringify(names));
}
