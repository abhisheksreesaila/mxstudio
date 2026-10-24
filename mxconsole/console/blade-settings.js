// mxconsole, the Settings blade: the theme (its only control since F-052), how long an idle model stays loaded,
// the daemon's own info with Stop daemon, and every port and process mxconsole owns.
// Data: store.info (/admin/info), store.models, GET /admin/system (home; the listeners come from system-data.js).
import { h, icon, api, store, onData, toast, dur, uptime, copyText } from "./core.js";
import { register, push, stack } from "./blades.js";
import { currentTheme, setTheme, onTheme } from "./prefs.js";
import { system } from "./system-data.js";

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
/** The ports table: every listener mxconsole owns, from /admin/system (`listeners`). */
export function portRows(listeners) {
  const STATE = { listening: "Listening", off: "Off", starting: "Starting", crashed: "Crashed" };
  return (listeners || []).map((l) => ({
    name: l.name, address: `${l.host}:${l.port ?? "—"}`, reach: l.exposed ? "your LAN" : "this machine",
    state: STATE[l.state] || String(l.state), on: l.state === "listening", talksTo: l.talks_to || "",
  }));
}

// -- DOM -----------------------------------------------------------------------------------------------
const twin = (cmd) => h("div", { class: "src" }, cmd,
  h("button", { class: "cp", type: "button", "aria-label": `Copy ${cmd}`, onClick: async () => toast((await copyText(cmd)) ? "Copied" : "Could not reach the clipboard") }, "copy"));
const card = (title, badge, body, ...src) => h("section", { class: "card" },
  h("div", { class: "card-h" }, h("h3", {}, title), h("span", { class: "sp" }), badge ? h("span", { class: "badge" }, badge) : null), h("div", { class: "card-b" }, body), ...src);

register("settings", (blade) => {
  let home = null, confirming = false, stopping = false;
  const cleanups = [];

  // -- theme: this control calls setTheme() and listens with onTheme() (prefs.js)
  const themeBtns = [["light", "sun", "Light"], ["dark", "moon", "Dark"]].map(([key, ic, label]) =>
    h("button", { type: "button", "data-theme-pick": key, onClick: () => setTheme(key) }, icon(ic, 14), label));
  const seg = h("div", { class: "seg2", role: "group", "aria-label": "Theme" }, h("i"), themeBtns);
  function paintTheme() {
    const now = currentTheme();
    themeBtns.forEach((b) => { const on = b.dataset.themePick === now; b.classList.toggle("on", on); b.setAttribute("aria-pressed", String(on)); });
    requestAnimationFrame(() => window.Omxqua && window.Omxqua.placeThumb(seg));   // the thumb slides under the pressed button
  }

  // -- idle unload and daemon info
  const idle = h("div", {});
  const daemon = h("div", {});
  const stopArea = h("div", { class: "ox-stop" });
  const ports = h("div", {});
  function drawIdle() {
    const rows = idleRows(store.models);
    idle.replaceChildren(
      h("p", { class: "ox-note" }, `A model unloads after ${dur(DEFAULT_KEEP_ALIVE)} without a request unless you say otherwise. There is no single setting for all of them yet: it is kept per model.`),
      rows.length ? h("dl", { class: "ox-dl" }, rows.map((r) => [h("dt", { class: "mono" }, r.name), h("dd", {}, r.text)]))
        : h("div", { class: "empty" }, h("div", { class: "ring" }, icon("box", 20)), h("b", {}, "No models yet"), "Add one and its idle time shows here.",
          h("div", {}, h("button", { class: "btn fill sm", type: "button", onClick: () => push("add", {}, stack.indexOf(blade)) }, icon("plus", 13, "currentColor", 2), "Add a model"))));
  }
  function drawDaemon() {
    const rows = daemonRows(store.info, home, store.infoAt ? (Date.now() - store.infoAt) / 1000 : 0);
    daemon.replaceChildren(rows.length ? h("dl", { class: "ox-dl" }, rows.map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])) : h("div", { class: "skel" }));
  }
  function drawPorts() {
    const rows = portRows(system.listeners);
    ports.replaceChildren(rows.length ? h("div", { class: "ox-scroll" }, h("table", { class: "tbl" },
      h("thead", {}, h("tr", {}, ["Listener", "Address", "Reachable from", "State", "What talks to it"].map((c) => h("th", {}, c)))),
      h("tbody", {}, rows.map((r) => h("tr", {}, h("td", { class: "mono" }, r.name), h("td", { class: "mono" }, r.address), h("td", {}, r.reach),
        h("td", {}, h("span", { class: `ox-st ${r.on ? "run" : ""}` }, h("i"), r.state)), h("td", { class: "ink2" }, r.talksTo)))))) : h("p", { class: "ox-note" }, system.at ? "No listeners reported." : "Loading…"));
  }
  function drawStop() {
    if (stopping) { stopArea.replaceChildren(h("span", { class: "ox-st warn" }, h("i"), "Stopping… the console goes offline in a moment.")); return; }
    stopArea.replaceChildren(confirming
      ? h("div", { class: "ox-warnbox", role: "alert" },
        h("p", {}, "Stop the daemon? Every model unloads and this console goes offline until you run ", h("code", { class: "mono" }, "mxc daemon"), " (or any command that starts it) again."),
        h("div", { class: "ox-row" },
          h("button", { class: "btn danger sm", type: "button", onClick: stop }, icon("square", 13), "Stop daemon"),
          h("button", { class: "btn text sm", type: "button", onClick: () => { confirming = false; drawStop(); } }, "Cancel")))
      : h("button", { class: "btn danger sm", type: "button", onClick: () => { confirming = true; drawStop(); } }, icon("square", 13), "Stop daemon"));
  }
  async function stop() {
    stopping = true; drawStop();
    try { await api("POST", "/admin/shutdown"); toast("Daemon stopping"); }
    catch (e) { stopping = false; confirming = false; drawStop(); toast(`Could not stop the daemon: ${e.message}`, 7000); }
  }

  return {
    title: "Settings", crumb: "Settings", sub: "this browser and this daemon",
    icon: "sliders-horizontal", hue: "var(--hue-settings)", side: 600, min: 520,
    mount(body) {
      blade.el.classList.add("oq-flat");
      body.replaceChildren(h("div", { class: "ox-pad" },
        card("Appearance", "kept in this browser", [seg,
          h("p", { class: "ox-note" }, "Until you pick one, the console follows your system.")],
          h("div", { class: "src note" }, "kept in this browser")),
        card("Idle unload", "per model", idle, twin("mxc set <model> --keep-alive 10m"), twin("mxc set <model> --pin")),
        card("Daemon", "", [daemon, stopArea], twin("mxc stop --all")),
        h("section", { class: "card" },
          h("div", { class: "card-h" }, h("h3", {}, "Ports & processes"), h("span", { class: "sp" }), h("span", { class: "badge" }, "most listen on this machine only")),
          h("div", { class: "card-b ox-flush" }, ports),
          h("p", { class: "ox-note ox-pnote" }, "Apps talk to the daemon, which starts one max serve per model on a loopback port. Only Caddy listens beyond this machine, and only after you make a model secure."),
          twin("mxc ps"))));
      paintTheme(); drawIdle(); drawDaemon(); drawPorts(); drawStop();
      cleanups.push(onData(() => { drawIdle(); drawDaemon(); drawPorts(); }));
      cleanups.push(onTheme(paintTheme));
      api("GET", "/admin/system").then((s) => { if (blade.alive) { home = s.home; drawDaemon(); } }).catch(() => { /* the row just stays out */ });
      blade.onClose = () => cleanups.forEach((fn) => fn());
    },
  };
});
