// Activity: the last 200 requests to /v1, and the "coming soon" panels for pages that don't have data yet.
import { h, icon, api, store, fmt, clock, HUE, terminalBox } from "./core.js";

export function activityPage(ctx) {
  const host = h("div", {});
  ctx.body.replaceChildren(host);
  let rows = null, failed = null;
  const draw = () => {
    ctx.setSub("the last 200 requests to /v1 · kept for 7 days · ~ means tokens counted from streamed chunks");
    if (failed) { host.replaceChildren(h("div", { class: "panel empty" }, h("h2", {}, "Could not load requests"), h("p", {}, failed))); return; }
    if (rows === null) { host.replaceChildren(h("div", { class: "panel empty" }, h("p", {}, "Loading…"))); return; }
    if (!rows.length) {
      const base = (store.info && store.info.endpoint) || `${location.origin}/v1`;
      host.replaceChildren(h("div", { class: "panel empty" }, h("h2", {}, "No requests yet"),
        h("p", {}, "Requests to /v1 show up here: which app, which model, tokens and latency."),
        h("div", { class: "code-box" }, h("div", { class: "ln mono" }, h("span", { class: "p" }, "$ "), `curl ${base}/models -H "Authorization: Bearer $MXC_API_KEY"`))));
      return;
    }
    const tokens = (r) => (r.completion_tokens === null ? "—" : `${fmt(r.prompt_tokens)} → ${r.estimated ? "~" : ""}${fmt(r.completion_tokens)}`);
    host.replaceChildren(h("section", { class: "panel scroll-x" }, h("table", { class: "grid" },
      h("thead", {}, h("tr", {}, ["Time", "App", "Model", "Route", "Status", "Tokens in → out", "TTFT", "Duration"].map((c) => h("th", {}, c)))),
      h("tbody", {}, rows.map((r) => h("tr", {}, h("td", { class: "mono" }, clock(r.ts)), h("td", {}, r.app || h("span", { class: "muted" }, "no key")),
        h("td", { class: "mono" }, r.model || "—"), h("td", { class: "mono" }, r.route),
        h("td", { class: `mono ${r.status < 400 ? "ok-text" : "bad-text"}` }, String(r.status)),
        h("td", { class: "mono" }, tokens(r)), h("td", { class: "mono" }, r.ttft_ms === null ? "—" : `${fmt(r.ttft_ms)} ms`), h("td", { class: "mono" }, `${fmt(r.duration_ms)} ms`)))))),
    h("div", { class: "cli-note mono" }, h("span", {}, "$ mxc requests --limit 200")));
  };
  const load = async () => { try { rows = await api("GET", "/admin/requests?limit=200"); failed = null; } catch (e) { failed = e.message; } draw(); };
  load();
  const timer = setInterval(load, 3000);
  return { update() {}, destroy() { clearInterval(timer); } };
}

const SOON = {
  pull: ["download", HUE.pull, "Pulling from the console is on the way: shards raining into a cartridge, with the real download steps. For now, from a terminal:", ["mxc pull qwen2.5-0.5b"]],
  ports: ["git-merge", HUE.ports, "Porting an unsupported architecture will be guided here. For now it starts from a terminal:", ["mxc port <hugging-face-repo>"]],
  benchmarks: ["gauge", HUE.bench, "A drag race between your models (prefill, decode, time to first token) is planned for a later release. Until then, the numbers are on the Metrics page and in:", ["mxc metrics --range 1h"]],
  logs: ["scroll-text", HUE.logs, "Every model's log is on its Logs tab (Models, then a model). From a terminal:", ["mxc logs <model> -f"]],
  gateway: ["route", HUE.gateway, "Safeguard counters and the LiteLLM routes will show here. Today the state of every part is in:", ["mxc status", "mxc expose"]],
  daemon: ["server", HUE.daemon, "Daemon settings will show here. From a terminal:", ["mxc status", "mxc daemon", "mxc stop --all"]],
};

export function soonPage(id) {
  return (ctx) => {
    const [ic, hue, text, cmds] = SOON[id];
    ctx.setSub("coming soon · the command line already does this");
    ctx.body.replaceChildren(h("section", { class: "panel soon" },
      h("div", { class: "sh" }, h("span", { class: "chip40", style: { "--hue": hue } }, icon(ic, 20, "currentColor", 1.8)), h("h2", {}, "Coming soon"), h("span", { class: "tag mono" }, "not built yet")),
      h("p", {}, text), terminalBox(cmds, "in a terminal")));
    return { update() {} };
  };
}
