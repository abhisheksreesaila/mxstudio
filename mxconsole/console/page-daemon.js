// Daemon, "the crew": the daemon, Caddy, LiteLLM and each `max serve`, with pid, port, memory, uptime and a
// heartbeat. Actions are the safe ones only (stop or load a model, restart LiteLLM, stop exposing): never "kill a pid".
import { h, s, icon, api, store, toast, openDialog, uptime, fmt, colorOf, HUE } from "./core.js";
import { loadModel, stopModel } from "./actions.js";

const EKG = "M 0 20 L 40 20 L 48 6 L 56 34 L 64 20 L 120 20 L 128 8 L 136 32 L 144 20 L 200 20";
const COLOUR = { daemon: HUE.daemon, caddy: HUE.network, litellm: HUE.gateway };

export function daemonPage(ctx) {
  const host = h("div", {});
  ctx.body.replaceChildren(host);
  let data = null, failed = null, busy = false;

  async function restartLitellm() {
    busy = true; draw();
    toast("Restarting the gateway (Caddy + LiteLLM)… it can take up to a minute.", 6000);
    try { await api("POST", "/admin/litellm/restart"); toast("Gateway restarted."); }
    catch (e) { toast(`Could not restart the gateway: ${e.message}`, 7000); }
    busy = false; await load();
  }
  function stopExposing() {
    const { dlg, close } = openDialog(
      h("h2", {}, "Stop exposing mxconsole?"),
      h("p", { class: "hint" }, "Caddy and LiteLLM stop. Other devices on your network lose access; this machine keeps working."),
      h("div", { class: "row" }, h("button", { class: "btn", type: "button", onClick: () => dlg.close() }, "Cancel"),
        h("button", { class: "btn danger", type: "button", onClick: async () => {
          close();
          try { await api("POST", "/admin/expose/stop"); toast("Stopped exposing."); } catch (e) { toast(`Could not stop: ${e.message}`, 7000); }
          await load();
        } }, "Stop exposing")));
  }

  const beat = (colour, alive, i) => h("span", { class: alive ? "beat alive" : "beat", style: { "--c": colour, "animation-delay": `${i * 0.3}s` } });
  const ekg = (colour, alive, i) => s("svg", { viewBox: "0 0 200 40", class: alive ? "ekg alive" : "ekg", "aria-hidden": "true" },
    s("path", { d: EKG, fill: "none", stroke: colour, "stroke-width": 2, style: { "animation-delay": `${i * 0.3}s` } }));
  const act = (label, ic, fn, cls = "btn sm") => h("button", { class: cls, type: "button", disabled: busy, onClick: fn }, icon(ic, 13), label);

  function row(c, i) {
    const alive = c.state === "running" || c.state === "ready";
    const colour = c.kind === "model" ? colorOf(c.name) : COLOUR[c.kind];
    const title = c.kind === "model" ? `max serve · ${c.name}` : c.name;
    const where = [c.pid ? `pid ${c.pid}` : alive ? "pid unknown" : "not running", c.port ? `:${c.port}` : null].filter(Boolean).join(" · ");
    let action = null;
    if (c.kind === "model" && alive) action = act("Stop", "square", () => stopModel(c.name).then(load));
    if (c.kind === "litellm" && alive) action = act("Restart gateway (Caddy + LiteLLM, rotates the internal key)", "refresh-cw", restartLitellm);
    return h("div", { class: alive ? "crew-row" : "crew-row off" },
      beat(colour, alive, i),
      h("div", { class: "who" }, h("div", { class: "mono n" }, title), h("div", { class: "mono w" }, where)),
      ekg(colour, alive, i),
      h("span", { class: "mono mem", title: "Resident memory of the process. GPU memory can't be read from the process, so it isn't shown." }, c.rss_mb === null || c.rss_mb === undefined ? "—" : c.rss_mb >= 1024 ? `${fmt(c.rss_mb / 1024, 1)} GB` : `${fmt(c.rss_mb)} MB`),
      h("span", { class: "mono up" }, alive ? uptime(c.uptime_seconds) : c.state),
      h("span", { class: "act" }, action));
  }

  function draw() {
    ctx.setSub("background processes keeping things running · the same list as mxc ps");
    const exposed = data && data.crew.some((c) => (c.kind === "caddy" || c.kind === "litellm") && c.state === "running");
    ctx.actions.replaceChildren(...(exposed ? [act("Stop exposing", "square", stopExposing, "btn")] : []));
    if (failed) { host.replaceChildren(h("div", { class: "panel empty" }, h("h2", {}, "Could not load the crew"), h("p", {}, failed))); return; }
    if (!data) { host.replaceChildren(h("div", { class: "panel empty" }, h("p", {}, "Loading…"))); return; }
    const running = new Set(data.crew.filter((c) => c.kind === "model").map((c) => c.name));
    const idle = (store.models || []).filter((m) => !running.has(m.name));
    const d = data.crew.find((c) => c.kind === "daemon");
    host.replaceChildren(
      ...[
      h("div", { class: "section-title" }, "The crew", h("span", { class: "sub" }, `MXC_HOME ${data.home}`)),
      h("section", { class: "panel crew" }, data.crew.map(row)),
      h("p", { class: "hint sys-note" }, "Memory is each process's resident memory. GPU memory isn't shown: it can't be read per process."),
      idle.length ? h("div", { class: "section-title sys-gap" }, "Not running", h("span", { class: "sub" }, "registered models, loaded on their first request")) : null,
      idle.length ? h("section", { class: "panel crew" }, idle.map((m) => h("div", { class: "crew-row off" },
        h("span", { class: "beat" }), h("div", { class: "who" }, h("div", { class: "mono n" }, `max serve · ${m.name}`), h("div", { class: "mono w" }, "idle")),
        h("span"), h("span"), h("span"), h("span", { class: "act" }, act("Load", "play", () => loadModel(m.name).then(load)))))) : null,
      h("div", { class: "cli-note mono" }, h("span", {}, "$ mxc ps"), h("span", {}, "$ mxc stop --all"), d && d.pid ? h("span", {}, `daemon pid ${d.pid}`) : null)].filter(Boolean));
  }
  async function load() {
    try { data = await api("GET", "/admin/system"); failed = null; } catch (e) { failed = e.message; }
    draw();
  }
  load();
  const timer = setInterval(load, 3000);
  return { update() {}, destroy() { clearInterval(timer); } };
}
