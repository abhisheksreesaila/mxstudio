// mxconsole, the Model blade (F-025): a model's own resource blade. A menu on the left (Overview inside; Security,
// Benchmark, Logs and Notebook open their blades next to this one), a command bar, and the Overview boxes.
// Data: the polled store (models, metrics, series, info) + /admin/system crew. Everything is updated in place, so
// focus and hover survive the 3 s poll. Only this file and blade-model.css change for it.
import { h, s, icon, store, onData, fmt, dur, colorOf, api, refresh, toast, copyButton, copyText } from "./core.js";
import { register, push, truncate, stack, setHead } from "./blades.js";
import { modelView } from "./blade-home.js";
import { hardwareView } from "./hardware.js";
import { system } from "./system-data.js";

const HUE = { overview: "var(--hue-notebook)", security: "var(--hue-keys)", bench: "var(--hue-bench)", logs: "var(--hue-logs)", notebook: "var(--hue-notebook)",
  start: "var(--ok)", stop: "var(--warn)", restart: "var(--warn)", copy: "var(--accent)", keys: "var(--hue-keys)" };
const MENU = [["overview", "Overview", "box", null], ["security", "Security", "lock", "security"], ["bench", "Benchmark", "gauge", "bench"],
  ["logs", "Logs", "scroll-text", "logs"], ["notebook", "Notebook", "book-open", "notebook"]];
const SOURCE = { hf: "Hugging Face", max: "Modular MAX catalog" };

// -- pure ------------------------------------------------------------------------------------------
/** The mxc command that does what a button does (every action shows it). */
export function cliTwin(action, name) {
  return ({ start: `mxc serve ${name}`, stop: `mxc stop ${name}`, restart: `mxc stop ${name} && mxc serve ${name}`, rm: `mxc rm ${name}`,
    secure: "mxc expose", bench: "mxc bench", notebook: `mxc learn ${name}`, logs: `mxc logs ${name}`, copy: "", security: "mxc expose" })[action] ?? "";
}

/** The command bar for a state ("ready" | "starting" | "cold" | "crashed"). Remove is always last and is the danger one. */
export function commandsFor(state, secure) {
  const c = (key, label, ic, hue, danger = false) => ({ key, label, icon: ic, hue: hue || null, danger, primary: false });
  const power = state === "ready" ? [c("stop", "Stop", "square", HUE.stop), c("restart", "Restart", "rotate-cw", HUE.restart)]
    : state === "starting" ? [c("stop", "Stop", "square", HUE.stop)]
    : state === "crashed" ? [c("restart", "Restart", "rotate-cw", HUE.restart)]
    : [c("start", "Start", "play", HUE.start)];
  power[0].primary = true;  // one filled verb per command bar: the power one
  return [...power,
    ...(secure ? [] : [c("secure", "Make it secure", "lock", HUE.security)]),
    c("bench", "Run benchmark", "gauge", HUE.bench), c("notebook", "Open notebook", "book-open", HUE.notebook),  // Logs: in the left menu, so the bar fits one line
    c("copy", "Copy endpoint", "copy", HUE.copy), c("rm", "Remove", "trash-2", null, true)];
}

/** What the Tokens card says when there is nothing to chart: never "no traffic" beside a request count. */
export function quietNote(state, requests) {
  if (requests > 0 && state !== "running")
    return { title: "Not loaded right now", text: `${requests} requests in the last 5 min; it loads again on the next one.` };
  return { title: "No traffic yet", text: "Start it, or call it: it starts on the first request." };
}

/** The curl that calls this model. The key is read from $MXC_API_KEY, never shown. */
export function curlFor(url, name) {
  return `curl ${url}/chat/completions \\\n  -H "Authorization: Bearer $MXC_API_KEY" \\\n  -H "Content-Type: application/json" \\\n  -d '{"model":"${name}","messages":[{"role":"user","content":"hi"}]}'`;
}

/** This model's VRAM against the others and the card. totalGb is null without a GPU. */
export function fitView(name, hw, crew) {
  const rows = (crew || []).filter((c) => /^model:/.test(c.id || "") && c.vram_gb > 0).map((c) => ({ name: c.id.slice(6), gb: Math.round(c.vram_gb * 10) / 10 }));
  const mine = rows.find((r) => r.name === name);
  const others = rows.filter((r) => r.name !== name);
  const total = hw.present ? hw.totalGb : null;
  const thisGb = mine ? mine.gb : 0;
  const othersGb = Math.round(others.reduce((a, r) => a + r.gb, 0) * 10) / 10;
  const pct = (gb) => (total ? (gb / total) * 100 : 0);
  return { totalGb: total, thisGb, othersGb, freeGb: total === null ? null : Math.round((total - hw.usedGb) * 10) / 10,
    thisPct: pct(thisGb), others: others.map((r) => ({ ...r, pct: pct(r.gb) })) };
}

/** Everything the Overview shows for one model. ctx: {metrics, series, info, system(crew), endpoint}. */
export function overviewView(m, ctx) {
  const v = modelView(m, ctx);
  const hw = hardwareView(ctx.metrics && ctx.metrics.gpus, ctx.system);
  const fit = fitView(m.name, hw, ctx.system);
  const secure = v.access === "secure";
  const url = v.endpoint;
  const unload = m.pinned ? "never (pinned)" : m.keep_alive ? `after ${dur(m.keep_alive)} idle` : "—";
  const vram = fit.thisGb ? `${fit.thisGb} GB${fit.totalGb ? ` · ${Math.round(fit.thisPct)}% of ${fit.totalGb} GB` : ""}` : "—";
  return {
    name: m.name, state: v.stateKey, stateLabel: v.stateLabel, crashed: v.crashed, secure,
    essentials: [["Repository", v.repo || "—"], ["Architecture", v.arch], ["Source", SOURCE[m.source] || m.source || "—"], ["Unloads", unload],
      ["Endpoint", url], ["Access", secure ? "HTTPS on your LAN, with a key" : "localhost only"], ["GPU memory", vram], ["State", v.note ? `${v.stateLabel} · ${v.note}` : v.stateLabel]],
    tokens: { tps: v.tps, ttft: v.ttft, requests: v.requests, points: v.points },
    call: { url, secure, keyHint: "$MXC_API_KEY", curl: curlFor(url, m.name) },
    fit,
  };
}

// -- pieces ----------------------------------------------------------------------------------------
function areaChart(points, color) {
  const w = 600, hgt = 84, pts = points.length > 1 ? points : [0, 0];
  const hi = Math.max(...pts), lo = Math.min(...pts, 0);
  const xy = pts.map((v, i) => [(i / (pts.length - 1)) * w, hgt - 4 - ((v - lo) / (hi - lo || 1)) * (hgt - 10)]);
  const line = xy.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const live = points.length > 1;
  return s("svg", { class: "area", viewBox: `0 0 ${w} ${hgt}`, preserveAspectRatio: "none", role: "img", "aria-label": live ? "Tokens per second, last 5 minutes" : "No recent traffic" },
    live ? s("polygon", { points: `0,${hgt} ${line} ${w},${hgt}`, fill: color, opacity: "0.12" }) : null,
    s("polyline", { points: line, fill: "none", stroke: live ? color : "var(--faint)", "stroke-width": "1.8", "stroke-linejoin": "round", "stroke-linecap": "round", "vector-effect": "non-scaling-stroke" }));
}
const dd = () => h("dd", {});
/** An omxqua card: heading, body, and its `mxc` source line (the command that shows the same thing). */
const card = (title, source, ...kids) => h("section", { class: "card" }, h("div", { class: "card-h" }, h("h3", {}, title)), h("div", { class: "card-b" }, ...kids), h("div", { class: "src" }, source));
const cliLine = (text) => h("div", { class: "cli mono" }, text);
const stat = (value, label) => h("div", {}, value, h("span", {}, label));

register("model", (blade) => {
  const name = blade.opts.id;
  return {
    title: name,
    crumb: name, sub: "Model", icon: "box", hue: colorOf(name), side: 660, narrow: 470, min: 620,
    mount(body) {
      const idx = () => stack.indexOf(blade);
      const child = () => stack[idx() + 1];
      const ctxNow = () => ({ metrics: store.metrics, series: store.series, info: store.info, system: system.crew, endpoint: (store.info && store.info.endpoint) || `${location.origin}/v1` });
      let removing = false, essigPrev = "", crashSig = "", busy = false, confirmTimer = 0, bar = null, barSig = "", subSig = "", gone = false;
      const open = (blade2, opts = {}) => push(blade2, { id: name, ...opts }, idx());

      // -- skeleton, built once -----------------------------------------------------------------
      const menuBtns = new Map();
      const menu = h("nav", { class: "rmenu", "aria-label": name },
        MENU.map(([key, label, ic, target]) => {
          const b = h("button", { type: "button", "data-menu": key, title: label, style: { "--h": HUE[key] }, onClick: () => (target ? (child() && child().name === target ? null : open(target)) : truncate(idx() + 1)) },
            icon(ic, 16, "currentColor", 1.75), h("span", { class: "ml" }, label), target ? icon("chevron-right", 13, "currentColor", 2) : null);
          menuBtns.set(key, b);
          return b;
        }), h("div", { class: "grp" }, "Command line"), h("div", { class: "twins" }));
      const twins = menu.querySelector(".twins");
      const barHost = h("div", { class: "cmdbar", role: "toolbar", "aria-label": `${name} actions` });
      const crash = h("div", { class: "span" });
      const dds = Array.from({ length: 8 }, dd);
      const accessLink = h("button", { class: "lnk", type: "button", onClick: () => open("security") }, "make it secure");
      const essentials = card("Essentials", "mxc ls --json", h("dl", { class: "ess" }, ["Repository", "Architecture", "Source", "Unloads", "Endpoint", "Access", "GPU memory", "State"].flatMap((l, i) => [h("dt", {}, l), dds[i]])));
      const mTps = h("b", {}), mTtft = h("b", {}), mReq = h("b", {}), chart = h("div", { class: "chart" });
      const tokens = card("Tokens · live", "mxc metrics --json", h("div", { class: "stats" }, stat(mTps, "tok/s"), stat(mTtft, "first token"), stat(mReq, "requests · 5 min")), chart);
      const urlTag = h("span", { class: "tag" }), urlText = h("span", { class: "u" });
      const urlRow = h("div", { class: "urlrow" }, urlTag, urlText, h("button", { class: "ib", type: "button", "aria-label": "Copy URL", title: "Copy URL", onClick: async () => toast((await copyText(urlText.textContent)) ? "Endpoint copied" : "Could not reach the clipboard") }, icon("copy", 14)));
      const curlText = h("pre", { class: "curl mono" });
      const call = card("Call it", "mxc keys new <app>", urlRow,
        h("div", { class: "keyrow" }, h("span", { class: "klabel" }, "API key"), h("code", { class: "kval mono" }, "$MXC_API_KEY"),
          h("button", { class: "lnk", type: "button", onClick: () => open("keys") }, "manage keys")),
        h("div", { class: "code" }, copyButton(() => curlText.textContent, "copy", "cp"), curlText));
      const secTitle = h("b", {}), secText = h("span", {});
      const secIcon = h("span", { class: "sq", style: { "--h": HUE.security, width: "36px", height: "36px" } });
      const secBtn = h("button", { class: "btn sm", type: "button", onClick: () => open("security", { run: !currentSecure }) });
      let currentSecure = false;
      const secBadge = h("span", { class: "badge" }), fitNum = h("span", { class: "r mono" }), fitBar = h("div", { class: "fitbar" }), fitCap = h("div", { class: "fitcap" });
      const security = h("section", { class: "card" },
        h("div", { class: "card-h" }, h("h3", {}, "Security"), h("span", { class: "sp" }), secBadge),
        h("div", { class: "card-b" }, h("div", { class: "secsum" }, secIcon, h("div", { class: "tx" }, secTitle, secText, h("div", { class: "secact" }, secBtn))),
          h("div", { class: "h3 fit-h" }, "Hardware fit", fitNum), fitBar, fitCap),
        h("div", { class: "src" }, "mxc expose"));
      const grid = h("div", { class: "ovgrid" }, crash, essentials, tokens, call, security);
      const content = h("div", { class: "rcontent" }, barHost, grid);
      body.replaceChildren(h("div", { class: "rwrap" }, menu, content));

      // -- actions -------------------------------------------------------------------------------
      async function act(key, label, fn, okMsg) {
        if (busy) return;
        busy = true; paintBar();
        try { await fn(); toast(okMsg); } catch (e) { toast(`Could not ${label} ${name}: ${e.message}`); }
        busy = false;
        await refresh();
      }
      const path = `/admin/models/${encodeURIComponent(name)}`;
      const post = (p) => () => api("POST", `${path}/${p}`);
      const ACTIONS = {
        start: () => act("start", "start", post("load"), `Starting ${name} · $ ${cliTwin("start", name)}`),
        stop: () => act("stop", "stop", post("stop"), `Stopped ${name} · $ ${cliTwin("stop", name)}`),
        restart: () => act("restart", "restart", async () => { await api("POST", `${path}/stop`); await api("POST", `${path}/load`); }, `Restarting ${name} · $ ${cliTwin("restart", name)}`),
        secure: () => open("security", { run: true }),
        bench: () => open("bench", { run: true }),
        notebook: () => open("notebook"),
        logs: () => open("logs"),
        copy: async () => {
          const url = (store.info && store.info.lan_endpoint) || ctxNow().endpoint;
          toast((await copyText(url)) ? `Endpoint copied · ${url}` : "Could not reach the clipboard: select the text and copy it");
        },
        rm: () => removeModel(),
      };
      let confirming = false;
      function removeModel() {
        const btn = barHost.querySelector('[data-cmd="rm"]');
        if (!confirming) {  // the second click confirms
          confirming = true; paintConfirm();
          clearTimeout(confirmTimer);
          confirmTimer = setTimeout(() => { confirming = false; paintConfirm(); }, 5000);
          return;
        }
        clearTimeout(confirmTimer);
        confirming = false;
        removing = true; paintBar();
        api("DELETE", path).then(() => {
          gone = true;
          truncate(1);
          toast(`Removed ${name} · $ ${cliTwin("rm", name)}`);
          refresh();
        }).catch((e) => { toast(`Could not remove ${name}: ${e.message}`); removing = false; paintBar(); paintConfirm(); });
      }
      function paintConfirm() {
        const btn = barHost.querySelector('[data-cmd="rm"]');
        if (!btn) return;
        const m = currentModel();
        const gb = m ? overviewView(m, ctxNow()).fit.thisGb : 0;
        btn.lastChild.textContent = confirming ? `Click again to remove${gb ? ` (${gb} GB)` : ""}` : "Remove";
        btn.classList.toggle("armed", confirming);
      }
      function paintBar() {
        barHost.querySelectorAll("button").forEach((b) => { b.disabled = busy || removing; });
      }
      const currentModel = () => (store.models || []).find((m) => m.name === name);

      // -- draw: update text and attributes in place; rebuild the command bar only when its set of buttons changes
      function buildBar(cmds) {
        const nodes = [];
        cmds.forEach((c) => {
          nodes.push(h("button", { class: `cb btn sm ${c.primary ? "fill" : c.danger ? "danger" : "tonal"}`, type: "button", "data-cmd": c.key, "aria-label": c.label, title: cliTwin(c.key, name) ? `$ ${cliTwin(c.key, name)}` : c.label,
            style: c.hue ? { "--c": c.hue } : null, onClick: () => ACTIONS[c.key]() }, icon(c.icon, 14, "currentColor", 1.75), h("span", { class: "bl" }, c.label)));
        });
        barHost.replaceChildren(...nodes);
        confirming = false;
      }
      function draw() {
        if (!blade.alive || gone) return;
        const m = currentModel();
        if (store.models === null || !m) {
          const msg = store.error && store.models === null ? "Daemon not answering" : store.models === null ? "Loading…" : "This model is not registered any more.";
          if (grid.dataset.empty !== msg) { grid.dataset.empty = msg; barHost.replaceChildren(); crash.replaceChildren(); grid.classList.add("blank"); essentials.hidden = tokens.hidden = call.hidden = security.hidden = true; crash.replaceChildren(h("div", { class: "empty" }, msg)); }
          return;
        }
        if (grid.dataset.empty) { delete grid.dataset.empty; grid.classList.remove("blank"); essentials.hidden = tokens.hidden = call.hidden = security.hidden = false; crash.replaceChildren(); crashSig = ""; }
        const v = overviewView(m, ctxNow());
        const sub = `Model · ${m.repo_id || m.source || ""}`;
        if (sub !== subSig) { subSig = sub; setHead(blade, { sub }); }
        // command bar
        const cmds = commandsFor(m.state, v.secure);
        const sig = cmds.map((c) => c.key).join();
        if (sig !== barSig) { barSig = sig; buildBar(cmds); }
        paintBar(); paintConfirm();
        // the crashed callout (its button is focusable: rebuild only when the flag flips)
        if (v.crashed !== (crashSig === "1")) {
          crashSig = v.crashed ? "1" : "0";
          crash.replaceChildren(...(v.crashed ? [h("div", { class: "attn", role: "alert" }, icon("triangle-alert", 16), h("span", {}, h("b", { class: "mono" }, name), " crashed. Its log says why."), h("span", { class: "grow" }),
            h("button", { class: "btn tonal sm", type: "button", onClick: () => open("logs") }, icon("scroll-text", 13), "View logs"),
            h("button", { class: "btn fill sm", type: "button", onClick: () => ACTIONS.restart() }, icon("rotate-cw", 13), "Restart"))] : []));
        }
        // essentials
        const essSig = JSON.stringify(v.essentials);
        if (essSig !== essigPrev) {
          essigPrev = essSig;
          v.essentials.forEach(([, val], i) => dds[i].replaceChildren(...(i === 7 ? [h("span", { class: `state ${v.state}` }, h("span", { class: "sd" }), val)] : [val]), ...(i === 5 && !v.secure ? [" · ", accessLink] : [])));
        }
        // tokens
        mTps.textContent = v.tokens.tps === null ? "—" : fmt(v.tokens.tps);
        mTtft.replaceChildren(...(v.tokens.ttft === null ? ["—"] : [fmt(v.tokens.ttft), h("small", {}, " ms")]));
        mReq.textContent = v.state === "running" || v.tokens.requests ? fmt(v.tokens.requests) : "0";
        chart.replaceChildren(v.tokens.points.length > 1 ? areaChart(v.tokens.points, colorOf(name))
          : ((q) => h("div", { class: "nodata" }, h("b", {}, q.title), q.text))(quietNote(v.state, v.tokens.requests || 0)));
        // call it
        currentSecure = v.secure;
        urlRow.classList.toggle("secure", v.secure);
        urlTag.textContent = v.secure ? "https · lan" : "local";
        urlText.textContent = v.call.url;
        curlText.textContent = v.call.curl;
        // security + fit
        secIcon.style.setProperty("--h", v.secure ? "var(--ok)" : HUE.security);
        secIcon.replaceChildren(icon(v.secure ? "lock" : "unlock", 16, "currentColor", 1.75));
        secTitle.textContent = v.secure ? "Secure on your network" : "Localhost only";
        secText.textContent = v.secure ? "HTTPS with a key. Other devices on your Wi-Fi can call it." : "Only this machine can call it. One click adds a certificate, a key and HTTPS.";
        secBtn.className = `btn sm ${v.secure ? "tonal" : "fill"}`;
        secBadge.className = `badge ${v.secure ? "ok" : "warn"}`;
        secBadge.replaceChildren(h("i", {}), v.secure ? "secure" : "localhost only");
        secBtn.replaceChildren(icon("lock", 14, "currentColor", 1.75), v.secure ? "View security" : "Make it secure");
        const f = v.fit;
        fitNum.textContent = f.thisGb ? `${f.thisGb} GB` : "";
        fitBar.replaceChildren(...f.others.map((o) => h("i", { title: `${o.name} · ${o.gb} GB`, class: "o", style: { width: `${o.pct}%` } })), ...(f.thisGb ? [h("i", { title: `${name} · ${f.thisGb} GB`, style: { width: `${f.thisPct}%`, background: colorOf(name) } })] : []));
        fitCap.replaceChildren(h("span", {}, f.totalGb === null ? "no GPU found" : `others ${f.othersGb} GB`), h("span", {}, f.totalGb === null ? "" : `free ${f.freeGb} GB of ${f.totalGb} GB`));
        // command-line twins for this state
        const twin = [m.state === "ready" || m.state === "starting" ? cliTwin("stop", name) : cliTwin("start", name), cliTwin("rm", name)].join("|");
        if (twins.dataset.sig !== twin) { twins.dataset.sig = twin; twins.replaceChildren(...twin.split("|").map(cliLine)); }
      }
      const sync = () => {
        const c = child();
        menuBtns.forEach((b, key) => b.classList.toggle("on", c ? MENU.find((m) => m[0] === key)[3] === c.name : key === "overview"));
      };
      blade.sync = sync;
      blade.redraw = draw;
      blade.onClose = (() => { const off = onData(draw); return () => { off(); clearTimeout(confirmTimer); }; })();
      draw();
      sync();
    },
  };
});
