// Ports: every listener MX Console owns, with its state and what talks to it. Data from /admin/system.
import { h, api, HUE, chip } from "./core.js";

const STATE = { listening: ["Listening", "var(--ok)"], off: ["Off", "var(--faint)"], starting: ["Starting", "var(--warn)"], crashed: ["Crashed", "var(--bad)"] };

export function portsPage(ctx) {
  const host = h("div", {});
  ctx.body.replaceChildren(host);
  let data = null, failed = null;

  const draw = () => {
    ctx.setSub("every port MX Console opens · most are loopback only, so nothing else on your network can reach them");
    if (failed) { host.replaceChildren(h("div", { class: "panel empty" }, h("h2", {}, "Could not load the ports"), h("p", {}, failed))); return; }
    if (!data) { host.replaceChildren(h("div", { class: "panel empty" }, h("p", {}, "Loading…"))); return; }
    const rows = data.listeners.map((l) => {
      const [label, colour] = STATE[l.state] || [l.state, "var(--faint)"];
      return h("tr", {},
        h("td", {}, h("span", { class: "sys-name" }, chip(l.kind === "model" ? "box" : l.kind === "daemon" ? "server" : l.kind === "caddy" ? "shield-check" : "route", HUE.ports, 24, 14), l.name)),
        h("td", { class: "mono" }, `${l.host}:${l.port ?? "—"}`),
        h("td", {}, l.exposed ? h("span", { class: "pill warn" }, "LAN") : h("span", { class: "pill" }, "this machine")),
        h("td", {}, h("span", { class: "sys-state" }, h("span", { class: l.state === "listening" ? "stat-dot live" : "stat-dot", style: { "--c": colour } }), label)),
        h("td", { class: "muted" }, l.talks_to));
    });
    host.replaceChildren(
      h("section", { class: "panel scroll-x" }, h("table", { class: "grid sys-table" },
        h("thead", {}, h("tr", {}, ["Listener", "Address", "Reachable from", "State", "What talks to it"].map((c) => h("th", {}, c)))),
        h("tbody", {}, rows))),
      h("section", { class: "panel sys-explain" },
        h("h2", {}, "How the ports fit together"),
        h("p", {}, "Your apps talk to the daemon. It starts one ", h("span", { class: "mono" }, "max serve"),
          " per model on a random loopback port and forwards each request to it, so a model is never reachable except through the daemon (and its key check)."),
        h("p", {}, "Only Caddy listens beyond this machine, and only after ", h("span", { class: "mono" }, "mxc expose"),
          ". It terminates HTTPS, checks the app key with the daemon, then passes requests to LiteLLM, which calls the daemon again as an internal app.")),
      h("div", { class: "cli-note mono" }, h("span", {}, "$ mxc ps"), h("span", {}, "$ mxc status")));
  };
  const load = async () => { try { data = await api("GET", "/admin/system"); failed = null; } catch (e) { failed = e.message; } draw(); };
  load();
  const timer = setInterval(load, 3000);
  return { update() {}, destroy() { clearInterval(timer); } };
}

