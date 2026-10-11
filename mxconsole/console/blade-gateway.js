// mxconsole, the Gateway blade: LiteLLM's status, its routes (model name -> upstream), restart, the log tail and
// the CLI twins. Data: GET /admin/network and /admin/gateway (polled), GET /admin/litellm/log, POST /admin/litellm/restart.
import { h, icon, api, store, toast, fmt, copyText } from "./core.js";
import { register } from "./blades.js";

const POLL = 4000;
const LOG_BYTES = 8000, LOG_LINES = 60;

// -- pure ----------------------------------------------------------------------------------------------
/** The route table: each registered model is a LiteLLM route to the daemon's /v1 (see expose.render_litellm_config). */
export function routesView(net) {
  const daemon = net && net.ports ? `http://127.0.0.1:${net.ports.daemon}/v1` : null;
  const rows = ((net && net.models) || []).map((m) => ({
    name: m.name, upstream: `openai/${m.name}`, state: m.state, port: m.port || null, recent: m.recent || 0,
  }));
  return { daemon, rows };
}
/** What is on duty: LiteLLM and Caddy, from the expose status. */
export function statusView(net) {
  const ex = (net && net.expose) || {};
  return { litellm: !!ex.litellm, caddy: !!ex.caddy, lan: (net && net.lan_endpoint) || null, port: net && net.ports ? net.ports.litellm : null };
}
/** The safeguards the daemon can count (today), as short lines. */
export function safeguardLines(gw) {
  if (!gw) return [];
  const n = (v, one, many) => `${fmt(v)} ${v === 1 ? one : many}`;
  return [
    ["Encrypted on your LAN", gw.tls.on ? `HTTPS on · ${gw.tls.endpoint}` : "off: nothing listens beyond this machine"],
    ["One key per app", `${gw.keys.active} active · ${gw.keys.revoked} revoked · ${n(gw.unauthorized_today, "request", "requests")} refused today (401)`],
    ["Only four doors open", `${n(gw.unknown_routes_today, "request", "requests")} to other /v1 paths today`],
    ["Admin stays home", `${n(gw.admin_refused_today, "refusal", "refusals")} today`],
  ];
}
/** The last n lines of a log, without the trailing blank. */
export function tailLines(text, n = LOG_LINES) {
  const lines = String(text || "").replace(/\s+$/, "").split("\n");
  return lines.length === 1 && lines[0] === "" ? [] : lines.slice(-n);
}

// -- DOM -----------------------------------------------------------------------------------------------
const dot = (on) => h("span", { class: `gw-dot ${on ? "on" : ""}` });
const twin = (cmd) => h("div", { class: "gw-twin" }, h("span", { class: "cli" }, cmd),
  h("button", { class: "ib", type: "button", "aria-label": `Copy ${cmd}`, title: "Copy", onClick: async () => toast((await copyText(cmd)) ? "Copied" : "Could not reach the clipboard") }, icon("copy", 14)));

register("gateway", (blade) => {
  let net = null, gw = null, failed = null, busy = false;
  const timers = [];

  const restartBtn = h("button", { class: "btn dark sm", type: "button", disabled: true, title: "Restarts Caddy and LiteLLM and rotates the internal key" }, icon("refresh-cw", 13), "Restart");
  const status = h("section", { class: "gw-sec" });
  const routes = h("section", { class: "gw-sec" });
  const guards = h("section", { class: "gw-sec" });
  const logPre = h("pre", { class: "gw-log mono", tabindex: "0", "aria-label": "LiteLLM log" }, "Loading…");
  const logBox = h("section", { class: "gw-sec" }, h("div", { class: "h3" }, "LiteLLM log", h("span", { class: "r muted" }, "last lines, refreshed every few seconds")), logPre);
  let logText = null;

  function drawStatus() {
    const v = statusView(net);
    restartBtn.disabled = busy || !v.litellm;
    restartBtn.title = v.litellm ? "Restarts Caddy and LiteLLM and rotates the internal key" : "LiteLLM is not running: start it with `mxc expose`";
    if (failed && !net) { status.replaceChildren(h("div", { class: "gw-fail" }, `Could not load the gateway: ${failed}`)); return; }
    if (!net) { status.replaceChildren(h("div", { class: "muted gw-pad" }, "Loading…")); return; }
    const info = store.info || {};
    status.replaceChildren(
      h("div", { class: "h3" }, "Status"),
      h("dl", { class: "gw-kv" },
        h("dt", {}, "LiteLLM"), h("dd", {}, dot(v.litellm), v.litellm ? `running · :${v.port}` : "off"),
        h("dt", {}, "Caddy (HTTPS)"), h("dd", {}, dot(v.caddy), v.caddy ? "running" : "off"),
        h("dt", {}, "Local endpoint"), h("dd", { class: "mono" }, info.endpoint || "—"),
        h("dt", {}, "LAN endpoint"), h("dd", { class: "mono" }, v.lan || "off")),
      !v.litellm ? h("p", { class: "gw-off" }, "LiteLLM is off, so only the daemon's own /v1 answers. The routes below apply once ", h("code", { class: "mono" }, "mxc expose"), " starts it.") : null);
  }
  function drawRoutes() {
    if (!net) { routes.replaceChildren(); return; }
    const { daemon, rows } = routesView(net);
    routes.replaceChildren(
      h("div", { class: "h3" }, "Routes", h("span", { class: "r muted mono" }, rows.length ? `every route goes to ${daemon}` : "")),
      rows.length ? h("div", { class: "gw-table" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", {}, "Model name"), h("th", {}, "Upstream model"), h("th", {}, "max serve"), h("th", { class: "num" }, "Req · 1 min"))),
        h("tbody", {}, rows.map((r) => h("tr", {}, h("td", { class: "mono" }, r.name), h("td", { class: "mono" }, r.upstream),
          h("td", { class: "mono" }, r.port ? `:${r.port}` : r.state === "starting" ? "starting" : "idle"), h("td", { class: "num mono" }, fmt(r.recent))))))) : h("div", { class: "muted gw-pad" }, "No models yet: add one and it gets a route."));
  }
  function drawGuards() {
    const lines = safeguardLines(gw);
    guards.replaceChildren(...(lines.length ? [h("div", { class: "h3" }, "Safeguards", h("span", { class: "r muted" }, "counted today")),
      h("dl", { class: "gw-kv wide" }, lines.map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]))] : []));
  }
  function drawLog(text) {
    if (text === logText) return;
    logText = text;
    const lines = tailLines(text);
    const atEnd = logPre.scrollHeight - logPre.scrollTop - logPre.clientHeight < 24;
    logPre.textContent = lines.length ? lines.join("\n") : "No LiteLLM log yet: it appears once `mxc expose` starts LiteLLM.";
    if (atEnd) logPre.scrollTop = logPre.scrollHeight;
  }

  async function load() {
    const [n, g, l] = await Promise.allSettled([api("GET", "/admin/network"), api("GET", "/admin/gateway"), api("GET", `/admin/litellm/log?bytes=${LOG_BYTES}`)]);
    if (!blade.alive) return;
    if (n.status === "fulfilled") { net = n.value; failed = null; } else failed = n.reason.message;
    if (g.status === "fulfilled") gw = g.value;
    drawLog(l.status === "fulfilled" ? l.value : "");
    drawStatus(); drawRoutes(); drawGuards();
  }

  restartBtn.addEventListener("click", async () => {
    busy = true; drawStatus();
    toast("Restarting the gateway (Caddy + LiteLLM)… it can take up to a minute.", 6000);
    try { await api("POST", "/admin/litellm/restart"); if (blade.alive) toast("Gateway restarted."); }
    catch (e) { toast(`Could not restart the gateway: ${e.message}`, 7000); }
    busy = false;
    if (blade.alive) await load();
  });

  return {
    title: "Gateway", crumb: "Gateway", sub: "LiteLLM · one endpoint routes to every model",
    icon: "route", hue: "var(--hue-gateway)", side: 640, min: 520,
    tools: [restartBtn],
    mount(body) {
      body.replaceChildren(h("div", { class: "gw" }, status, routes, guards, logBox,
        h("div", { class: "gw-sec" }, h("div", { class: "h3" }, "From the terminal"), twin("mxc status"), twin("mxc expose"), twin("mxc expose --stop"))));
      drawStatus(); drawRoutes(); drawGuards();
      load();
      timers.push(setInterval(load, POLL));
      blade.onClose = () => timers.forEach(clearInterval);
    },
  };
});
