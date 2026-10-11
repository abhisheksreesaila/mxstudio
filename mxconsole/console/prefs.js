// mxconsole, per-browser preferences. Pure: the storage is passed in, and a blocked storage never throws.
export const VIEW_KEY = "mxc-view";
export const THEME_KEY = "mxc-theme";

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
