// Network: the patch bay. Your apps, Caddy, LiteLLM and the daemon with their real ports, and a jack per
// model with its real port. A cable flows only when its link carried requests in the last minute.
import { h, s, icon, api, store, toast, copyButton, openDialog, terminalBox, colorOf, HUE, ago } from "./core.js";

const CHANGED = "hotfire:network-changed";
const SIZE = { w: 1090 };

export async function exposeDialog() {
  let net = null;
  try { net = await api("GET", "/admin/network"); } catch (e) { toast(`Could not read the network state: ${e.message}`); return; }
  const host = h("input", { class: "input", value: net.default_host, "aria-label": "Hostname clients use", spellcheck: "false" });
  const lite = h("button", { class: "switch", type: "button", role: "switch", "aria-checked": "true", "aria-label": "Route through LiteLLM" });
  lite.addEventListener("click", () => { lite.setAttribute("aria-checked", lite.getAttribute("aria-checked") === "true" ? "false" : "true"); twin(); });
  const err = h("div", { class: "bad-text", style: "font-size:13px", role: "alert" });
  const status = h("div", { class: "hint", role: "status" });
  const go = h("button", { class: "btn primary", type: "button" }, icon("globe", 14), "Expose to the LAN");
  const term = h("div", {});
  const twin = () => term.replaceChildren(terminalBox([`hotfire expose${host.value && host.value !== net.default_host ? ` --host ${host.value}` : ""}${lite.getAttribute("aria-checked") === "true" ? "" : " --no-litellm"}`]));
  host.addEventListener("input", twin);
  twin();
  const { dlg, close } = openDialog(
    h("h2", {}, "Expose Hotfire to your LAN?"),
    h("div", { class: "warnbox" }, icon("triangle-alert", 16), h("div", {},
      "Other devices on this network will be able to reach your models at ", h("span", { class: "mono" }, `https://${net.default_host}:${net.ports.caddy}/v1`),
      ", with an app key. Hotfire starts Caddy (HTTPS from its own local CA, every key checked) and LiteLLM. Each device trusts the CA once.")),
    h("label", { class: "field" }, "Hostname clients use", host),
    h("div", { style: "display:flex;align-items:center;gap:10px;font-size:13.5px" }, lite, "Route through LiteLLM"),
    term, err, status,
    h("div", { class: "row" }, h("button", { class: "btn", type: "button", onClick: () => dlg.close() }, "Cancel"), go));
  go.addEventListener("click", async () => {
    go.disabled = true; err.textContent = "";
    status.textContent = "Starting… LiteLLM can take up to a minute to boot.";
    try {
      const res = await api("POST", "/admin/expose", { host: host.value.trim(), litellm: lite.getAttribute("aria-checked") === "true" });
      toast(`Exposed at ${res.lan_endpoint || res.host}. Trust the CA on each device once.`, 6000);
      window.dispatchEvent(new Event(CHANGED));
      close();
    } catch (e) {
      err.textContent = e.message; status.textContent = ""; go.disabled = false;
    }
  });
}

export async function stopExpose() {
  try {
    await api("POST", "/admin/expose/stop");
    toast("Stopped exposing. Hotfire is on this machine only again.");
  } catch (e) { toast(`Could not stop: ${e.message}`, 7000); }
  window.dispatchEvent(new Event(CHANGED));
}

export function networkPage(ctx) {
  const actions = ctx.actions;
  const host = h("div", {});
  ctx.body.replaceChildren(host);
  let net = null, failed = null, sig = "";

  const node = (x, y, w, hgt, { title, lines, icon: ic, color, on, state, lock }) => h("div", { class: on ? "node" : "node off", style: { left: `${x}px`, top: `${y}px`, width: `${w}px`, height: `${hgt}px`, "--c": color } },
    h("div", { class: "nh" }, h("span", { class: "chip26" }, icon(ic, 14, "currentColor", 1.8)), h("span", {}, title), lock ? icon("lock", 13, "currentColor", 2) : null,
      h("span", { class: on ? "st on mono" : "st mono" }, state)),
    h("div", { class: "ns mono" }, lines.map((l, i) => [i ? h("br") : null, l])));

  function cable(d, color, mode) { // mode: flow | idle | off
    if (mode === "off") return [s("path", { d, class: "cable-off" })];
    const base = s("path", { d, class: "cable-base", style: { "--c": color } });
    return mode === "flow" ? [base, s("path", { d, class: "cable-flow", style: { "--c": color } })] : [base];
  }
  const curve = (x1, y1, x2, y2) => { const mid = (x1 + x2) / 2; return `M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`; };

  function draw() {
    if (failed) { host.replaceChildren(h("div", { class: "panel empty" }, h("h2", {}, "Could not read the network state"), h("p", {}, failed))); return; }
    if (!net) { host.replaceChildren(h("div", { class: "panel empty" }, h("p", {}, "Loading…"))); return; }
    const { expose: ex, ports, models, apps } = net;
    const exposed = net.exposed, lite = ex.litellm, recent = net.recent_requests > 0;
    ctx.setSub(`${exposed ? `exposed at ${net.lan_endpoint || "your LAN"}` : "this machine only"} · daemon :${ports.daemon} · ${models.filter((m) => m.state === "ready").length} of ${models.length} models loaded`);

    actions.replaceChildren(
      ...(net.ca_exists ? [copyButton(() => net.ca_path, "Copy CA path")] : []),
      exposed ? h("button", { class: "btn", type: "button", onClick: stopExpose }, icon("square", 14), "Stop exposing")
        : h("button", { class: "btn primary", type: "button", onClick: exposeDialog }, icon("globe", 15), "Expose"));

    const shown = models.slice(0, 6);
    const n = shown.length;
    const H = Math.max(560, 250 + n * 84 + 24);
    const clients = apps.length ? apps.slice(0, 4).map((a) => ({ title: a.app, lines: [`key: ${a.app}`, `${ago(a.last_ts)} · ${a.requests} in 24 h`], icon: "bot" }))
      : [{ title: "your apps", lines: ["any OpenAI client", "one key per app"], icon: "laptop" }];
    const CH = 90, GAP = 24, CW = 190, MID = 270;
    const startY = MID - (clients.length * CH + (clients.length - 1) * GAP) / 2;
    const caddy = { x: 290, y: MID - 58, w: 200, h: 116 }, lite_ = { x: 540, y: MID - 58, w: 200, h: 116 }, daemon = { x: 800, y: 50, w: 210, h: 116 };
    const cables = [], boxes = [];
    const into = daemon.y + 22; // where a cable from the left enters the daemon
    clients.forEach((c, i) => {
      const y = startY + i * (CH + GAP);
      boxes.push(node(20, y, CW, CH, { ...c, color: HUE.network, on: true, state: "" }));
      if (exposed) cables.push(...cable(curve(20 + CW, y + CH / 2, caddy.x, MID - 30 + (clients.length > 1 ? (i * 60) / (clients.length - 1) : 30)), HUE.network, recent ? "flow" : "idle"));
      else cables.push(...cable(curve(20 + CW, y + CH / 2, daemon.x, into), HUE.daemon, recent ? "flow" : "off"));
    });
    if (exposed) {
      cables.push(...cable(curve(caddy.x + caddy.w, MID, lite_.x, MID), HUE.gateway, lite ? (recent ? "flow" : "idle") : "off"));
      cables.push(...cable(curve(lite_.x + lite_.w, MID, daemon.x, into), HUE.daemon, lite ? (recent ? "flow" : "idle") : "off"));
      if (!lite) cables.push(...cable(`M ${caddy.x + caddy.w / 2} ${caddy.y} C ${caddy.x + caddy.w / 2} 70, 700 ${into}, ${daemon.x} ${into}`, HUE.daemon, recent ? "flow" : "idle"));
    }
    boxes.push(
      node(caddy.x, caddy.y, caddy.w, caddy.h, { title: "Caddy", icon: "shield-check", color: HUE.gateway, on: exposed, state: exposed ? "running" : "stopped", lock: true,
        lines: [`:${ports.caddy} · HTTPS, own CA`, "checks every key", "only 4 routes pass"] }),
      node(lite_.x, lite_.y, lite_.w, lite_.h, { title: "LiteLLM", icon: "route", color: HUE.gateway, on: exposed && lite, state: exposed && lite ? "running" : exposed ? "not used" : "stopped",
        lines: [`127.0.0.1:${ports.litellm}`, "routes by model name", "master key, local only"] }),
      node(daemon.x, daemon.y, daemon.w, daemon.h, { title: "daemon", icon: "server", color: HUE.daemon, on: true, state: "running",
        lines: [`127.0.0.1:${ports.daemon}`, "loads on demand", "unloads when idle"] }));
    shown.forEach((m, i) => {
      const jy = 250 + i * 84, color = colorOf(m.name), live = m.state === "ready";
      boxes.push(h("div", { class: live ? "jack" : "jack cold", style: { left: `${daemon.x}px`, top: `${jy}px`, "--c": color } },
        h("span", { class: "ring" }), h("div", { style: "min-width:0" }, h("div", { class: "jn mono trunc", title: m.name }, m.name),
          h("div", { class: "jp mono" }, m.port ? ["127.0.0.1:", h("span", { style: { color, fontWeight: 500 } }, String(m.port))] : m.state === "starting" ? "starting…" : "not loaded · no port"))));
      // a bus down the left of the jacks: lower jacks turn further out and start higher, so no cable crosses another
      const y1 = daemon.y + 54 + (n - 1 - i) * 10, busX = daemon.x - 26 - i * 8, y2 = jy + 35;
      const d = `M ${daemon.x} ${y1} H ${busX + 10} Q ${busX} ${y1} ${busX} ${y1 + 10} V ${y2 - 10} Q ${busX} ${y2} ${busX + 10} ${y2} H ${daemon.x}`;
      cables.push(...cable(d, color, live ? (m.recent > 0 ? "flow" : "idle") : "off"));
    });
    const svg = s("svg", { class: "cables", viewBox: `0 0 ${SIZE.w} ${H}`, width: SIZE.w, height: H, "aria-hidden": "true" }, ...cables);
    const stage = h("div", { class: "bay-stage", style: { height: `${H}px` } }, svg,
      h("div", { class: "zone mono", style: "left:20px;top:24px" }, "Your apps"),
      h("div", { class: "zone mono", style: "left:290px;top:24px" }, `This machine · ${net.host}`), ...boxes);

    const facts = h("div", { class: "net-facts" },
      fact("Local endpoint", `http://127.0.0.1:${ports.daemon}/v1`, `http://127.0.0.1:${ports.daemon}/v1`),
      fact("LAN endpoint", net.lan_endpoint || "not exposed", net.lan_endpoint),
      fact("Local CA", net.ca_exists ? net.ca_path : "created the first time you expose", net.ca_exists ? net.ca_path : null));
    host.replaceChildren(facts, h("section", { class: "panel bay" }, stage),
      h("div", { class: "bay-legend mono" }, h("span", {}, "moving dots = requests in the last minute"), h("span", {}, "each model gets its own loopback port, chosen free at start"),
        h("span", {}, "loopback and LAN requests can't be told apart"), h("span", { class: "end" }, "$ hotfire status · hotfire expose")));
  }
  const fact = (k, v, copyValue) => h("div", { class: "panel" }, h("div", { class: "k" }, k),
    h("div", { class: "v" }, h("span", { class: "t mono", title: v }, v), copyValue ? copyButton(() => copyValue, "Copy", "ghost") : null));

  async function load() {
    try { net = await api("GET", "/admin/network"); failed = null; } catch (e) { failed = e.message; }
    // redraw only when something visible changed, so the cables' dash animation isn't reset every poll
    const next = JSON.stringify(failed ? failed : [net.expose, net.ports, net.models.map((m) => [m.name, m.state, m.port, m.recent > 0]), net.apps.map((a) => a.app), net.recent_requests > 0, net.lan_endpoint, net.ca_exists]);
    if (next !== sig) { sig = next; draw(); }
  }
  window.addEventListener(CHANGED, load);
  const timer = setInterval(load, 3000);
  load();
  return { update() {}, destroy() { clearInterval(timer); window.removeEventListener(CHANGED, load); } };
}
