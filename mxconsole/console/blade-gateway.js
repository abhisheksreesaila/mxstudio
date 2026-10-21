// mxconsole, the Gateway blade: LiteLLM's status, its routes (model name -> upstream), restart, the log tail and
// the CLI twins. Data: GET /admin/network and /admin/gateway (polled), GET /admin/litellm/log, POST /admin/litellm/restart.
import { h, icon, api, store, onData, toast, fmt, clock, appLabel, copyText } from "./core.js";
import { register, push, stack } from "./blades.js";

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
/** Recent requests to /v1 (GET /admin/requests, newest first) as table rows. `~` marks tokens counted from streamed chunks. */
export function requestRows(requests, n = 12) {
  return (requests || []).slice(0, n).map((r) => ({
    at: r.ts, app: r.app, model: r.model || "—", route: r.route, status: r.status, ok: r.status < 400,
    tokens: r.completion_tokens === null || r.completion_tokens === undefined ? "—" : `${fmt(r.prompt_tokens)} → ${r.estimated ? "~" : ""}${fmt(r.completion_tokens)}`,
    ttft: r.ttft_ms === null || r.ttft_ms === undefined ? "—" : `${fmt(r.ttft_ms)} ms`, duration: `${fmt(r.duration_ms)} ms`,
  }));
}
/** The last n lines of a log, without the trailing blank. */
export function tailLines(text, n = LOG_LINES) {
  const lines = String(text || "").replace(/\s+$/, "").split("\n");
  return lines.length === 1 && lines[0] === "" ? [] : lines.slice(-n);
}

// -- DOM -----------------------------------------------------------------------------------------------
const st = (on, word) => h("span", { class: `ox-st ${on ? "run" : ""}` }, h("i"), word);
const twin = (cmd) => h("div", { class: "src" }, cmd,
  h("button", { class: "cp", type: "button", "aria-label": `Copy ${cmd}`, onClick: async () => toast((await copyText(cmd)) ? "Copied" : "Could not reach the clipboard") }, "copy"));
const note = (text) => h("div", { class: "src note" }, text);
const empty = (ic, title, text, action) => h("div", { class: "empty" }, h("div", { class: "ring" }, icon(ic, 20)), h("b", {}, title), text, action ? h("div", {}, action) : null);
/** A card: title (+ a badge), the body, then its source lines. `flush` lets a table run edge to edge. */
const card = (title, badge, body, flush, ...src) => h("section", { class: "card" },
  h("div", { class: "card-h" }, h("h3", {}, title), h("span", { class: "sp" }), badge ? h("span", { class: "badge" }, badge) : null),
  h("div", { class: flush ? "card-b ox-flush" : "card-b" }, body), ...src);

register("gateway", (blade) => {
  let net = null, gw = null, failed = null, busy = false;
  const timers = [];

  const restartBtn = h("button", { class: "btn tonal sm", type: "button", disabled: true, title: "Restarts Caddy and LiteLLM and rotates the internal key" }, icon("refresh-cw", 13), "Restart");
  const status = h("div", { class: "gw-slot" });
  const routes = h("div", { class: "gw-slot" });
  const guards = h("div", { class: "gw-slot" });
  const recent = h("div", { class: "gw-slot" });
  const logPre = h("pre", { class: "gw-log mono", tabindex: "0", "aria-label": "LiteLLM log" }, "Loading…");
  const logBox = card("LiteLLM log", "last lines", logPre, false, note("refreshed every few seconds"));
  let logText = null;

  function drawStatus() {
    const v = statusView(net);
    restartBtn.disabled = busy || !v.litellm;
    restartBtn.title = v.litellm ? "Restarts Caddy and LiteLLM and rotates the internal key" : "LiteLLM is not running: start it with `mxc expose`";
    if (failed && !net) { status.replaceChildren(card("Status", "", h("div", { class: "ox-fail", role: "alert" }, icon("triangle-alert", 15), `Could not load the gateway: ${failed}`), false, twin("mxc status"))); return; }
    if (!net) { status.replaceChildren(card("Status", "", h("div", { class: "skel" }), false, twin("mxc status"))); return; }
    const info = store.info || {};
    status.replaceChildren(card("Status", "", [
      h("dl", { class: "ox-dl" },
        h("dt", {}, "LiteLLM"), h("dd", {}, st(v.litellm, v.litellm ? `running · :${v.port}` : "off")),
        h("dt", {}, "Caddy (HTTPS)"), h("dd", {}, st(v.caddy, v.caddy ? "running" : "off")),
        h("dt", {}, "Local endpoint"), h("dd", {}, info.endpoint || "—"),
        h("dt", {}, "LAN endpoint"), h("dd", {}, v.lan || "off")),
      !v.litellm ? h("p", { class: "ox-note" }, "LiteLLM is off, so only the daemon's own /v1 answers. The routes below apply once ", h("code", { class: "mono" }, "mxc expose"), " starts it.") : null], false, twin("mxc status")));
  }
  function drawRoutes() {
    if (!net) { routes.replaceChildren(); return; }
    const { daemon, rows } = routesView(net);
    routes.replaceChildren(card("Routes", "", rows.length ? h("div", { class: "ox-scroll" }, h("table", { class: "tbl" },
        h("thead", {}, h("tr", {}, h("th", {}, "Model name"), h("th", {}, "Upstream model"), h("th", {}, "max serve"), h("th", { class: "num" }, "Req · 1 min"))),
        h("tbody", {}, rows.map((r) => h("tr", {}, h("td", { class: "mono" }, r.name), h("td", { class: "mono" }, r.upstream),
          h("td", { class: "mono" }, r.port ? `:${r.port}` : r.state === "starting" ? "starting" : "idle"), h("td", { class: "num" }, fmt(r.recent))))))) :
        empty("route", "No routes yet", "Add a model and it gets a route.", h("button", { class: "btn fill sm", type: "button", onClick: () => push("add", {}, stack.indexOf(blade)) }, icon("plus", 13, "currentColor", 2), "Add a model")),
      true, note(rows.length ? `every route goes to ${daemon}` : "a route appears for each model you add")));
  }
  function drawGuards() {
    const lines = safeguardLines(gw);
    guards.replaceChildren(...(lines.length ? [card("Safeguards", "counted today", h("dl", { class: "ox-dl" }, lines.map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])), false, twin("mxc status"))] : []));
  }
  function drawRecent() {
    const rows = requestRows(store.requests);
    recent.replaceChildren(card("Recent requests", "newest first · kept for 7 days", rows.length ? h("div", { class: "ox-scroll" }, h("table", { class: "tbl" },
        h("thead", {}, h("tr", {}, ["Time", "App", "Model", "Route", "Status", "Tokens in → out", "TTFT", "Duration"].map((c) => h("th", {}, c)))),
        h("tbody", {}, rows.map((r) => h("tr", {}, h("td", { class: "mono" }, clock(r.at)), h("td", {}, appLabel(r.app)), h("td", { class: "mono" }, r.model), h("td", { class: "mono" }, r.route),
          h("td", {}, h("span", { class: `ox-st mono ${r.ok ? "run" : "bad"}` }, h("i"), String(r.status))), h("td", { class: "mono" }, r.tokens), h("td", { class: "mono" }, r.ttft), h("td", { class: "mono" }, r.duration))))))
        : store.requests ? empty("activity", "No requests yet", "They show up here once an app calls /v1.", h("a", { class: "btn fill sm", href: "/docs" }, icon("book-open", 13), "Open the API reference"))
          : h("div", { class: "skel" }), true, twin("mxc requests --limit 200")));
  }
  function drawLog(text, failedWith) {
    const key = failedWith ? `!${failedWith}` : text;
    if (key === logText) return;
    logText = key;
    if (failedWith) { logPre.textContent = `Could not read the LiteLLM log: ${failedWith}`; return; }
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
    drawLog(l.status === "fulfilled" ? l.value : "", l.status === "rejected" ? l.reason.message : null);
    drawStatus(); drawRoutes(); drawGuards();
  }

  restartBtn.addEventListener("click", async () => {
    busy = true; drawStatus();
    toast("Restarting the gateway (Caddy + LiteLLM)… it can take up to a minute.", 6000);
    try { await api("POST", "/admin/litellm/restart"); if (blade.alive) toast("Gateway restarted."); }
    catch (e) { if (blade.alive) toast(`Could not restart the gateway: ${e.message}`, 7000); }
    busy = false;
    if (blade.alive) await load();
  });

  return {
    title: "Gateway", crumb: "Gateway", sub: "LiteLLM · one endpoint routes to every model",
    icon: "route", hue: "var(--hue-gateway)", side: 640, min: 520,
    tools: restartBtn,
    mount(body) {
      blade.el.classList.add("oq-flat");
      body.replaceChildren(h("div", { class: "ox-pad" }, status, routes, recent, guards, logBox,
        card("API reference", "", [h("p", { class: "ox-note" }, "Base URL, auth, every endpoint and a request you can copy."),
          h("a", { class: "btn fill sm gw-open", href: "/docs" }, icon("book-open", 13), "Open the API reference")], false, twin("mxc console api")),
        h("section", { class: "card" }, h("div", { class: "card-h" }, h("h3", {}, "From the terminal")), twin("mxc status"), twin("mxc expose"), twin("mxc expose --stop"))));
      drawStatus(); drawRoutes(); drawRecent(); drawGuards();
      timers.push(onData(drawRecent));
      load();
      // a timeout chain, not an interval: the next poll starts only after the last one finished (no pile-up)
      const next = () => { if (blade.alive) timers.push(setTimeout(async () => { await load(); next(); }, POLL)); };
      next();
      blade.onClose = () => timers.forEach((t) => (typeof t === "function" ? t() : clearTimeout(t)));
    },
  };
});
