// Gateway, "what's protecting you": six safeguards as solid illustrations, each with the one counter the
// daemon can really know. What Caddy or the start-up code enforces says so instead of inventing a number.
import { h, s, icon, api, fmt, HUE } from "./core.js";

const W = "#ffffff";
const rect = (x, y, width, height, rx, fill, extra) => s("rect", { x, y, width, height, rx, fill, ...extra });
const circle = (cx, cy, r, fill) => s("circle", { cx, cy, r, fill });
// Solid, friendly shapes in two tones, built as SVG nodes (never an HTML string).
const SHAPES = {
  lock: (c) => [rect(18, 40, 64, 48, 12, c), s("path", { d: "M 32 42 V 32 a 18 18 0 0 1 36 0 V 42", fill: "none", stroke: c, "stroke-width": 9, "stroke-linecap": "round" }),
    circle(50, 62, 7, W), rect(47, 64, 6, 12, 3, W)],
  key: (c) => [circle(34, 50, 20, c), circle(34, 50, 8, W), rect(48, 44, 40, 12, 6, c), rect(72, 52, 9, 16, 4, c)],
  gate: (c) => [rect(14, 20, 14, 64, 6, c), rect(72, 20, 14, 64, 6, c), rect(30, 40, 40, 8, 4, c, { opacity: 0.5 }), rect(30, 56, 40, 8, 4, c, { opacity: 0.5 }), circle(50, 28, 9, c)],
  broom: (c) => [rect(46, 10, 8, 46, 4, c, { transform: "rotate(25 50 50)" }), s("path", { d: "M 30 58 L 66 58 L 74 88 L 22 88 Z", fill: c }),
    s("path", { d: "M 32 70 H 68 M 30 78 H 70", stroke: W, "stroke-width": 3 })],
  house: (c) => [s("path", { d: "M 16 48 L 50 18 L 84 48 V 86 H 16 Z", fill: c }), rect(40, 58, 20, 28, 4, W), circle(50, 40, 5, W)],
  safe: (c) => [rect(16, 20, 68, 64, 12, c), circle(50, 52, 16, W), circle(50, 52, 6, c), rect(24, 84, 10, 6, 2, c), rect(66, 84, 10, 6, 2, c)],
};
export function illustration(kind, colour) {
  return s("svg", { viewBox: "0 0 100 100", width: 76, height: 76, "aria-hidden": "true" }, SHAPES[kind](colour));
}

const plural = (n, one, many) => `${fmt(n)} ${n === 1 ? one : many}`;
function safeguards(g) {
  return [
    ["lock", HUE.daemon, "Encrypted on your LAN", "HTTPS with mxconsole's own certificate authority. Trust it once per device.",
      g.tls.on ? { text: `HTTPS on · ${g.tls.endpoint}`, on: true } : { text: "LAN exposure is off: nothing listens beyond this machine", on: true }],
    ["key", HUE.keys, "One key per app", "Revoke one app without touching the others. Keys are stored only as hashes.",
      { text: `${g.keys.active} active · ${g.keys.revoked} revoked · ${plural(g.unauthorized_today, "request", "requests")} refused today (401)`, on: true }],
    ["gate", HUE.api, "Only four doors open", "/v1/models, chat, completions and embeddings. Everything else is a 404.",
      { text: `${plural(g.unknown_routes_today, "request", "requests")} to other /v1 paths from this machine today (Caddy answers unknown LAN paths itself)`, on: true }],
    ["broom", HUE.metrics, "Sneaky paths swept away", "Requests like /x/../v1/models are refused before routing.",
      { text: "enforced by Caddy (it answers before the daemon sees them)", on: null }],
    ["house", HUE.overview, "Admin stays home", "The console and admin routes answer this machine only, never the LAN.",
      { text: `${plural(g.admin_refused_today, "refusal", "refusals")} today`, on: true }],
    ["safe", HUE.models, "No secrets lying around", "LiteLLM gets a minimal environment. Your shell's provider keys never leak in.",
      { text: "enforced when LiteLLM starts", on: null }],
  ];
}

export function gatewayPage(ctx) {
  const host = h("div", {});
  ctx.body.replaceChildren(host);
  ctx.actions.replaceChildren(h("a", { class: "btn", href: "#daemon" }, icon("server", 15), "The crew"), h("a", { class: "btn", href: "#logs" }, icon("scroll-text", 15), "Logs"));
  let data = null, failed = null;

  const draw = () => {
    ctx.setSub(data ? `counters for today · refusals and unknown-route counts restart with the daemon (since ${new Date(data.counters_since * 1000).toLocaleTimeString([], { hour12: false })})`
      : "who gets in, how, and who's on duty");
    if (failed) { host.replaceChildren(h("div", { class: "panel empty" }, h("h2", {}, "Could not load the safeguards"), h("p", {}, failed))); return; }
    if (!data) { host.replaceChildren(h("div", { class: "panel empty" }, h("p", {}, "Loading…"))); return; }
    host.replaceChildren(
      h("div", { class: "section-title" }, "What's protecting you", h("span", { class: "sub" }, "six safeguards")),
      h("section", { class: "guards" }, safeguards(data).map(([kind, colour, title, body, counter]) =>
        h("div", { class: "panel guard", style: { "--c": colour } },
          h("div", { class: "art" }, illustration(kind, colour)),
          h("div", { class: "txt" }, h("div", { class: "t" }, title), h("div", { class: "b" }, body),
            h("div", { class: counter.on === null ? "ctr mono quiet" : "ctr mono" }, icon(counter.on === null ? "shield-check" : "check", 12, "currentColor", 2.4), counter.text))))),
      h("div", { class: "cli-note mono" }, h("span", {}, "$ mxc status"), h("span", {}, "$ mxc keys ls"), h("span", {}, "$ mxc requests --limit 50")));
  };
  const load = async () => { try { data = await api("GET", "/admin/gateway"); failed = null; } catch (e) { failed = e.message; } draw(); };
  load();
  const timer = setInterval(load, 3000);
  return { update() {}, destroy() { clearInterval(timer); } };
}
