// mxconsole, the Settings blade: the theme (mirrors the top-bar toggle), how long an idle model stays loaded,
// and the daemon's own info with Stop daemon. Data: store.info (/admin/info), store.models, GET /admin/system (home).
import { h, icon, api, store, onData, toast, dur, uptime, copyText } from "./core.js";
import { register } from "./blades.js";
import { readTheme, writeTheme, resolveTheme, safeStorage } from "./prefs.js";

// Models registered without --keep-alive unload after this long (store.DEFAULT_KEEP_ALIVE). There is no
// daemon-wide setting route: the value is per model, set with `mxc set`.
export const DEFAULT_KEEP_ALIVE = 300;

// -- pure ----------------------------------------------------------------------------------------------
/** The idle-unload line for each model. */
export function idleRows(models) {
  return (models || []).map((m) => ({ name: m.name, text: m.pinned ? "pinned: never unloads" : `after ${dur(m.keep_alive)} idle` }));
}
/** The daemon info rows. `elapsed` is seconds since the info was fetched, so uptime keeps counting between polls. */
export function daemonRows(info, home, elapsed = 0) {
  if (!info) return [];
  return [
    ["Host", info.host], ["Endpoint", info.endpoint], ["LAN endpoint", info.lan_endpoint || "off: this machine only"],
    ["MAX version", info.max_version || "—"], ["Uptime", uptime((info.uptime_seconds ?? 0) + elapsed)],
    ["Active keys", String(info.active_keys ?? "—")], home ? ["MXC_HOME", home] : null,
  ].filter(Boolean);
}
/** The theme in force: the explicit choice, else the system's. */
export const effectiveTheme = (pref, systemDark) => resolveTheme(pref, systemDark);

// -- DOM -----------------------------------------------------------------------------------------------
const twin = (cmd) => h("div", { class: "st-twin" }, h("span", { class: "cli" }, cmd),
  h("button", { class: "ib", type: "button", "aria-label": `Copy ${cmd}`, title: "Copy", onClick: async () => toast((await copyText(cmd)) ? "Copied" : "Could not reach the clipboard") }, icon("copy", 14)));

register("settings", (blade) => {
  const sysDark = window.matchMedia("(prefers-color-scheme: dark)");
  let home = null, confirming = false, stopping = false;
  const cleanups = [];

  // -- theme: the top bar owns the toggle; this mirrors it. Clicking the real toggle keeps its state in step.
  const seg = h("div", { class: "st-seg", role: "group", "aria-label": "Theme" });
  const current = () => effectiveTheme(readTheme(safeStorage()) || document.documentElement.dataset.theme || null, sysDark.matches);
  function setTheme(want) {
    if (current() === want) return;
    const toggle = document.querySelector("#topbar button.tn.icon[aria-pressed]");
    if (toggle) toggle.click();
    else { writeTheme(safeStorage(), want); document.documentElement.dataset.theme = want; }
    paintTheme();
  }
  function paintTheme() {
    const now = current();
    seg.replaceChildren(...[["light", "sun", "Light"], ["dark", "moon", "Dark"]].map(([key, ic, label]) =>
      h("button", { type: "button", "aria-pressed": String(now === key), onClick: () => setTheme(key) }, icon(ic, 14), label)));
  }

  // -- idle unload and daemon info
  const idle = h("div", {});
  const daemon = h("div", {});
  const stopArea = h("div", { class: "st-stop" });
  function drawIdle() {
    const rows = idleRows(store.models);
    idle.replaceChildren(
      h("p", { class: "st-note" }, `A model unloads after ${dur(DEFAULT_KEEP_ALIVE)} without a request unless you say otherwise. There is no single setting for all of them yet: it is kept per model.`),
      rows.length ? h("dl", { class: "st-kv" }, rows.map((r) => [h("dt", { class: "mono" }, r.name), h("dd", {}, r.text)])) : h("p", { class: "muted st-note" }, "No models yet."),
      twin("mxc set <model> --keep-alive 10m"), twin("mxc set <model> --pin"));
  }
  function drawDaemon() {
    const rows = daemonRows(store.info, home, store.infoAt ? (Date.now() - store.infoAt) / 1000 : 0);
    daemon.replaceChildren(rows.length ? h("dl", { class: "st-kv" }, rows.map(([k, v]) => [h("dt", {}, k), h("dd", { class: "mono" }, v)])) : h("p", { class: "muted st-note" }, "Loading…"));
  }
  function drawStop() {
    if (stopping) { stopArea.replaceChildren(h("span", { class: "muted" }, "Stopping… the console goes offline in a moment.")); return; }
    stopArea.replaceChildren(confirming
      ? h("div", { class: "st-confirm", role: "alert" },
        h("p", {}, "Stop the daemon? Every model unloads and this console goes offline until you run ", h("code", { class: "mono" }, "mxc daemon"), " (or any command that starts it) again."),
        h("div", { class: "st-row" },
          h("button", { class: "btn danger", type: "button", onClick: stop }, icon("square", 13), "Stop daemon"),
          h("button", { class: "btn ghost", type: "button", onClick: () => { confirming = false; drawStop(); } }, "Cancel")))
      : h("button", { class: "btn danger", type: "button", onClick: () => { confirming = true; drawStop(); } }, icon("square", 13), "Stop daemon"));
  }
  async function stop() {
    stopping = true; drawStop();
    try { await api("POST", "/admin/shutdown"); toast("Daemon stopping"); }
    catch (e) { stopping = false; confirming = false; drawStop(); toast(`Could not stop the daemon: ${e.message}`, 7000); }
  }

  const section = (title, hint, ...content) => h("section", { class: "st-sec" }, h("div", { class: "h3" }, title, hint ? h("span", { class: "r muted" }, hint) : null), ...content);

  return {
    title: "Settings", crumb: "Settings", sub: "this browser and this daemon",
    icon: "sliders-horizontal", hue: "var(--hue-settings)", side: 600, min: 520,
    mount(body) {
      body.replaceChildren(h("div", { class: "st" },
        section("Appearance", "kept in this browser", seg,
          h("p", { class: "st-note" }, "The sun and moon button in the top bar does the same. Until you pick one, the console follows your system.")),
        section("Idle unload", "per model", idle),
        section("Daemon", "", daemon, stopArea, twin("mxc stop --all"))));
      paintTheme(); drawIdle(); drawDaemon(); drawStop();
      cleanups.push(onData(() => { drawIdle(); drawDaemon(); }));
      const mo = new MutationObserver(paintTheme);
      mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
      sysDark.addEventListener("change", paintTheme);
      cleanups.push(() => mo.disconnect(), () => sysDark.removeEventListener("change", paintTheme));
      api("GET", "/admin/system").then((s) => { if (blade.alive) { home = s.home; drawDaemon(); } }).catch(() => { /* the row just stays out */ });
      blade.onClose = () => cleanups.forEach((fn) => fn());
    },
  };
});
