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
  const narrow = window.matchMedia("(max-width: 640px)");
  const onNarrow = () => { sig = ""; load(); };
  narrow.addEventListener("change", onNarrow);

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

  // phone width: one column. apps, Caddy, LiteLLM, daemon top to bottom, then the jacks hanging off a bus on the left
  function drawStacked(net, shown) {
    const { expose: ex, ports, apps } = net, exposed = net.exposed, lite = ex.litellm, recent = net.recent_requests > 0;
    const W = 322, NH = 100, GAP = 30, JH = 64, JGAP = 12, JX = 44, n = shown.length;
    const app = apps.length ? { title: `${apps.length} app${apps.length > 1 ? "s" : ""}`, lines: [apps.slice(0, 3).map((a) => a.app).join(", "), `${ago(apps[0].last_ts)} · ${apps[0].requests} in 24 h`], icon: "bot" }
      : { title: "your apps", lines: ["any OpenAI client", "one key per app"], icon: "laptop" };
    const col = [];
    col.push({ k: "apps", ...app, color: HUE.network, on: true, state: "" });
    if (exposed) {
      col.push({ k: "caddy", title: "Caddy", icon: "shield-check", color: HUE.gateway, on: true, state: "running", lock: true, lines: [`:${ports.caddy} · HTTPS, own CA`, "checks every key", "only 4 routes pass"] });
      col.push({ k: "lite", title: "LiteLLM", icon: "route", color: HUE.gateway, on: lite, state: lite ? "running" : "not used", lines: [`127.0.0.1:${ports.litellm}`, "routes by model name", "master key, local only"] });
    }
    col.push({ k: "daemon", title: "daemon", icon: "server", color: HUE.daemon, on: true, state: "running", lines: [`127.0.0.1:${ports.daemon}`, "loads on demand", "unloads when idle"] });
    const cables = [], boxes = [];
    col.forEach((c, i) => {
      const y = 16 + i * (NH + GAP);
      boxes.push(node(0, y, W, NH, c));
      if (i) {
        const flowing = recent ? "flow" : "idle";
        const mode = !exposed ? (recent ? "flow" : "off") : (c.k === "lite" || (c.k === "daemon" && !lite)) && !lite ? "off" : flowing;
        cables.push(...cable(`M ${W / 2} ${y - GAP} V ${y}`, c.k === "daemon" ? HUE.daemon : HUE.gateway, mode));
      }
    });
    const dBottom = 16 + (col.length - 1) * (NH + GAP) + NH;
    shown.forEach((m, i) => {
      const jy = dBottom + GAP + i * (JH + JGAP), color = colorOf(m.name), live = m.state === "ready", starting = m.state === "starting";
      const bx = 6 + (n - 1 - i) * 4, cy = jy + JH / 2;
      boxes.push(h("div", { class: live ? "jack" : starting ? "jack starting" : "jack cold", style: { left: `${JX}px`, top: `${jy}px`, width: `${W - JX}px`, height: `${JH}px`, "--c": color } },
        h("span", { class: "ring" }), h("div", { style: "min-width:0" }, h("div", { class: "jn mono trunc", title: m.name }, m.name),
          h("div", { class: "jp mono" }, m.port ? ["127.0.0.1:", h("span", { style: { color, fontWeight: 500 } }, String(m.port))] : starting ? "starting…" : "not loaded"))));
      cables.push(...cable(`M ${bx} ${dBottom - NH / 2 + i * 2} V ${cy - 10} Q ${bx} ${cy} ${bx + 10} ${cy} H ${JX}`, color, live ? (m.recent > 0 ? "flow" : "idle") : "off"));
    });
    const H = dBottom + GAP + n * (JH + JGAP) + 4;
    const svg = s("svg", { class: "cables", viewBox: `0 0 ${W} ${H}`, width: W, height: H, "aria-hidden": "true" }, ...cables);
    const stage = h("div", { class: "bay-stage", style: { height: `${H}px`, width: `${W}px` } }, svg, ...boxes);
    ctx.setSub(`${exposed ? `exposed at ${net.lan_endpoint || "your LAN"}` : "this machine only"} · daemon :${ports.daemon} · ${net.models.filter((m) => m.state === "ready").length} of ${net.models.length} models loaded`);
    host.replaceChildren(factsRow(net), h("section", { class: "panel bay" }, stage));
  }

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

    const shown = models.slice(0, 8);
    const n = shown.length;
    if (narrow.matches) { drawStacked(net, shown); return; }
    const JH = 70, JGAP = 12; // a jack and the space between jacks
    const H = Math.max(500, 120 + n * (JH + JGAP));
    const MID = H / 2;
    const clients = apps.length ? apps.slice(0, 4).map((a) => ({ title: a.app, lines: [`key: ${a.app}`, `${ago(a.last_ts)} · ${a.requests} in 24 h`], icon: "bot" }))
      : [{ title: "your apps", lines: ["any OpenAI client", "one key per app"], icon: "laptop" }];
    const CH = 90, GAP = 24, CW = 154;
    const startY = MID - (clients.length * CH + (clients.length - 1) * GAP) / 2;
    // four columns, every box centred on the same line: apps, Caddy, LiteLLM, daemon; the model jacks fan out to the right
    const caddy = { x: 204, y: MID - 58, w: 186, h: 116 }, lite_ = { x: 426, y: MID - 58, w: 186, h: 116 }, daemon = { x: 648, y: MID - 58, w: 180, h: 116 };
    const jackX = 892, jackW = 190;
    const cables = [], boxes = [];
    const into = MID; // where a cable from the left enters the daemon
    clients.forEach((c, i) => {
      const y = startY + i * (CH + GAP);
      boxes.push(node(16, y, CW, CH, { ...c, color: HUE.network, on: true, state: "" }));
      if (exposed) cables.push(...cable(curve(16 + CW, y + CH / 2, caddy.x, MID - 30 + (clients.length > 1 ? (i * 60) / (clients.length - 1) : 30)), HUE.network, recent ? "flow" : "idle"));
      else cables.push(...cable(curve(16 + CW, y + CH / 2, daemon.x, into - 30 + (clients.length > 1 ? (i * 60) / (clients.length - 1) : 30)), HUE.daemon, recent ? "flow" : "off"));
    });
    if (exposed) {
      cables.push(...cable(curve(caddy.x + caddy.w, MID, lite_.x, MID), HUE.gateway, lite ? (recent ? "flow" : "idle") : "off"));
      cables.push(...cable(curve(lite_.x + lite_.w, MID, daemon.x, into), HUE.daemon, lite ? (recent ? "flow" : "idle") : "off"));
      // without LiteLLM, Caddy goes straight to the daemon, arching over the LiteLLM box
      if (!lite) cables.push(...cable(`M ${caddy.x + caddy.w / 2} ${caddy.y} C ${caddy.x + caddy.w / 2} ${caddy.y - 50}, ${daemon.x + daemon.w / 2} ${daemon.y - 50}, ${daemon.x + daemon.w / 2} ${daemon.y}`, HUE.daemon, recent ? "flow" : "idle"));
    }
    boxes.push(
      node(caddy.x, caddy.y, caddy.w, caddy.h, { title: "Caddy", icon: "shield-check", color: HUE.gateway, on: exposed, state: exposed ? "running" : "stopped", lock: true,
        lines: [`:${ports.caddy} · HTTPS, own CA`, "checks every key", "only 4 routes pass"] }),
      node(lite_.x, lite_.y, lite_.w, lite_.h, { title: "LiteLLM", icon: "route", color: HUE.gateway, on: exposed && lite, state: exposed && lite ? "running" : exposed ? "not used" : "stopped",
        lines: [`127.0.0.1:${ports.litellm}`, "routes by model name", "master key, local only"] }),
      node(daemon.x, daemon.y, daemon.w, daemon.h, { title: "daemon", icon: "server", color: HUE.daemon, on: true, state: "running",
        lines: [`127.0.0.1:${ports.daemon}`, "loads on demand", "unloads when idle"] }));
    const top = MID - (n * JH + (n - 1) * JGAP) / 2;
    shown.forEach((m, i) => {
      const jy = top + i * (JH + JGAP), color = colorOf(m.name), live = m.state === "ready", starting = m.state === "starting";
      boxes.push(h("div", { class: live ? "jack" : starting ? "jack starting" : "jack cold", style: { left: `${jackX}px`, top: `${jy}px`, width: `${jackW}px`, height: `${JH}px`, "--c": color } },
        h("span", { class: "ring" }), h("div", { style: "min-width:0" }, h("div", { class: "jn mono trunc", title: m.name }, m.name),
          h("div", { class: "jp mono" }, m.port ? ["127.0.0.1:", h("span", { style: { color, fontWeight: 500 } }, String(m.port))] : starting ? "starting…" : "not loaded"))));
      cables.push(...cable(curve(daemon.x + daemon.w, MID, jackX, jy + JH / 2), color, live ? (m.recent > 0 ? "flow" : "idle") : "off"));
    });
    const svg = s("svg", { class: "cables", viewBox: `0 0 ${SIZE.w} ${H}`, width: SIZE.w, height: H, "aria-hidden": "true" }, ...cables);
    const stage = h("div", { class: "bay-stage", style: { height: `${H}px`, width: `${SIZE.w}px` } }, svg,
      h("div", { class: "zone mono", style: "left:16px;top:24px" }, "Your apps"),
      h("div", { class: "zone mono", style: "left:204px;top:24px" }, `This machine · ${net.host}`), ...boxes);

    host.replaceChildren(factsRow(net), h("section", { class: "panel bay" }, stage), h("div", { class: "bay-hint mono" }, "scroll →"),
      h("div", { class: "bay-legend mono" }, h("span", {}, "moving dots = requests in the last minute"), h("span", {}, "each model gets its own loopback port, chosen free at start"),
        h("span", {}, "loopback and LAN requests can't be told apart"), h("span", { class: "end" }, "$ hotfire status · hotfire expose")));
  }
  const factsRow = (net) => h("div", { class: "net-facts" },
    fact("Local endpoint", `http://127.0.0.1:${net.ports.daemon}/v1`, `http://127.0.0.1:${net.ports.daemon}/v1`),
    fact("LAN endpoint", net.lan_endpoint || "not exposed", net.lan_endpoint),
    fact("Local CA", net.ca_exists ? net.ca_path : "created the first time you expose", net.ca_exists ? net.ca_path : null));
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
  return { update() {}, destroy() { clearInterval(timer); window.removeEventListener(CHANGED, load); narrow.removeEventListener("change", onNarrow); } };
}
