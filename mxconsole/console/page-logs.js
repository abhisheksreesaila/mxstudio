// Logs: the commands people reach for, and a log viewer (daemon, Caddy, LiteLLM, each model).
// Log text only ever goes in as a text node.
import { h, icon, api, copyButton } from "./core.js";

const COMMANDS = [
  ["mxc status", "One screen: daemon, endpoints, models, keys, MAX version"],
  ["mxc ps", "The crew: pid, port, memory and uptime of every background process"],
  ["mxc ls --json", "Every model and its state, for scripts and agents"],
  ["mxc logs <model> -f", "Follow a model's server log"],
  ["mxc metrics --range 1h", "Requests, errors, latency and GPUs over the last hour"],
  ["mxc requests --limit 50", "The last 50 requests, newest first"],
  ["mxc keys new <app>", "Make an API key for an app (shown once)"],
  ["mxc expose", "Serve your models on the LAN over HTTPS"],
  ["mxc stop <model>", "Unload one model now"],
  ["mxc stop --all", "Unload every model and stop the daemon"],
];
const MAX_LINES = 2000;
const logUrl = (id) => (id.startsWith("model:") ? `/admin/models/${encodeURIComponent(id.slice(6))}/log` : `/admin/${id}/log?bytes=32000`);

export function logsPage(ctx) {
  const host = h("div", {});
  ctx.body.replaceChildren(host);
  ctx.setSub("the commands you will need, and the logs of everything MX Console runs");

  const cmdTable = h("section", { class: "panel scroll-x" }, h("table", { class: "grid sys-table" },
    h("thead", {}, h("tr", {}, h("th", {}, "Command"), h("th", {}, "What it does"), h("th", {}, ""))),
    h("tbody", {}, COMMANDS.map(([cmd, why]) => h("tr", {},
      h("td", { class: "mono sys-cmd" }, h("span", { class: "p" }, "$ "), cmd), h("td", { class: "muted" }, why),
      h("td", { class: "r" }, copyButton(() => cmd, "Copy", "btn sm")))))));

  // -- viewer ------------------------------------------------------------------------------------
  let sources = [], current = null, text = "", error = "", follow = true, timer = 0, token = 0;
  const picker = h("select", { class: "select", "aria-label": "Log source" });
  const filter = h("input", { class: "input", type: "search", placeholder: "Filter lines", "aria-label": "Filter lines", spellcheck: "false" });
  const followBtn = h("button", { class: "switch", type: "button", role: "switch", "aria-checked": "true", "aria-label": "Follow the log" });
  const pre = h("pre", { class: "sys-log mono", tabindex: "0", "aria-label": "Log output" });
  const meta = h("div", { class: "hint mono" });

  const render = () => {
    const needle = filter.value.trim().toLowerCase();
    let lines = text.split("\n");
    if (lines.length > MAX_LINES) lines = lines.slice(-MAX_LINES);
    const total = lines.length;
    if (needle) lines = lines.filter((l) => l.toLowerCase().includes(needle));
    pre.textContent = error ? error : lines.length ? lines.join("\n") : text ? "No line matches the filter." : "Nothing in this log yet.";
    meta.textContent = error ? "" : `${needle ? `${lines.length} of ${total}` : total} lines · the last 32 KB at most${follow ? " · refreshing every 2 s" : ""}`;
    if (follow) pre.scrollTop = pre.scrollHeight;
  };
  const fetchLog = async () => {
    if (!current) return;
    const mine = ++token;
    try { const t = await api("GET", logUrl(current)); if (mine === token) { text = typeof t === "string" ? t : ""; error = ""; } }
    catch (e) { if (mine === token) error = `Could not read this log: ${e.message}`; }
    if (mine === token) render();
  };
  const schedule = () => { clearInterval(timer); if (follow) timer = setInterval(fetchLog, 2000); };
  picker.addEventListener("change", () => { current = picker.value; text = ""; fetchLog(); });
  filter.addEventListener("input", render);
  followBtn.addEventListener("click", () => {
    follow = followBtn.getAttribute("aria-checked") !== "true";
    followBtn.setAttribute("aria-checked", String(follow));
    schedule(); render();
  });

  const viewer = h("section", { class: "panel sys-viewer" },
    h("div", { class: "sys-viewer-bar" }, picker,
      h("div", { class: "search" }, icon("search", 14), filter),
      h("label", { class: "sys-follow" }, followBtn, "Follow"),
      h("button", { class: "btn sm", type: "button", onClick: fetchLog }, icon("refresh-cw", 14), "Refresh")),
    pre, meta);
  host.replaceChildren(
    h("div", { class: "section-title" }, "Commands", h("span", { class: "sub" }, "copy one into a terminal")), cmdTable,
    h("div", { class: "section-title sys-gap" }, "Log viewer", h("span", { class: "sub" }, "text only · daemon, Caddy, LiteLLM and every model")), viewer,
    h("div", { class: "cli-note mono" }, h("span", {}, "$ mxc logs <model> -f")));
  pre.textContent = "Loading…";

  (async () => {
    try {
      sources = (await api("GET", "/admin/system")).logs;
    } catch (e) { pre.textContent = `Could not list the logs: ${e.message}`; return; }
    if (!sources.length) { pre.textContent = "No logs yet. Start a model with: mxc serve <model>"; return; }
    picker.replaceChildren(...sources.map((src) => h("option", { value: src.id }, src.id.startsWith("model:") ? `model · ${src.label}` : src.label)));
    current = sources[0].id;
    picker.value = current;
    await fetchLog();
    schedule();
  })();
  return { update() {}, destroy() { clearInterval(timer); token++; } };
}
