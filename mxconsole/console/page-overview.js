// Overview: the live strip (one trace per running model), the number strip and the live tiles.
import { h, s, icon, api, store, fmt, tick, colorOf, HUE, reducedMotion, STATE_LABEL, lifecycle, copyButton, appLabel } from "./core.js";
import { traceSvg } from "./charts.js";
import { openPalette } from "./palette.js";

const running = (m) => m.state === "ready" || m.state === "starting" || m.state === "crashed";
const chip30 = (name, hue) => h("span", { class: "chip30", style: { "--hue": hue } }, icon(name, 16, "currentColor", 1.8));

export function overviewPage(ctx) {
  ctx.actions.append(
    h("a", { class: "btn", href: "#network" }, icon("globe", 15), "Expose"),
    h("button", { class: "btn primary", type: "button", onClick: () => openPalette("serve") }, icon("plus", 15), "Serve model"));

  // ---- live strip
  const laneHost = h("div", {});
  const strip = h("section", { class: "panel live-strip", "aria-label": "Live models" },
    h("div", { class: "strip-head" }, h("span", { class: "t" }, "Live"),
      h("span", { class: "s mono" }, "decode tok/s · last 5 min · updates every second"),
      h("a", { class: "ghost", href: "#models" }, "All models", icon("chevron-right", 14))),
    laneHost);
  const lanes = new Map();

  // ---- number strip
  const cell = (label) => {
    const n = h("span", { class: "n num" }, "—"), u = h("span", { class: "u" }), note = h("div", { class: "note mono" });
    const bar = h("div", { class: "bar", hidden: true }, h("div", {}));
    return { el: h("div", { class: "cell" }, h("div", { class: "k" }, label), h("div", { class: "v" }, n, u), bar, note), n, u, note, bar };
  };
  const cells = { decode: cell("Decode"), ttft: cell("First token"), req: cell("Requests"), gpu: cell("GPU memory") };
  const numstrip = h("section", { class: "panel numstrip", "aria-label": "Key numbers" }, ...Object.values(cells).map((c) => c.el));

  // ---- tiles
  const t = {};
  t.modelsFrames = h("div", { class: "cycle" });
  t.pips = h("div", { class: "pips" });
  t.modelsFoot = h("div", { class: "foot mono" });
  t.models = h("a", { class: "panel tile t-models", href: "#models" },
    h("div", { class: "th" }, chip30("box", HUE.models), "Models", h("span", { class: "tag mono" }, "LIVE")), t.modelsFrames, t.pips, t.modelsFoot);
  t.started = h("div", { class: "panel tile t-models", hidden: true },
    h("div", { class: "th" }, chip30("sparkles", HUE.models), "Get started"),
    h("div", { class: "start-steps" }, h("div", { class: "hint" }, "No models yet. Serve one from a terminal; it shows up here as soon as it is registered."),
      h("div", { class: "code-box" }, h("div", { class: "ln mono" }, h("span", { class: "p" }, "$ "), "mxc serve qwen2.5-0.5b")),
      h("div", {}, copyButton(() => "mxc serve qwen2.5-0.5b", "Copy command", "btn"))),
    h("div", { class: "foot mono" }, "first load compiles the model: ~1 minute"));
  t.tickerTag = h("span", { class: "tag mono" });
  t.ticker = h("div", { class: "ticker" });
  t.tickerWrap = h("div", { class: "ticker-wrap" }, t.ticker);
  t.activity = h("a", { class: "panel tile t-activity", href: "#activity" },
    h("div", { class: "th" }, chip30("activity", HUE.activity), "Activity", t.tickerTag), t.tickerWrap);
  t.fastName = h("div", { class: "nm mono" }, "—");
  t.fastBig = h("span", { class: "big num" }, "—");
  t.fastFoot = h("a", { class: "foot", href: "#metrics" }, "See the charts →");
  t.fast = h("div", { class: "panel tile t-fast", style: { "--hue": HUE.bench } },
    h("div", { class: "th" }, chip30("trophy", "#fff"), "Fastest", h("span", { class: "tag mono", style: "color:rgba(255,255,255,.8)" }, "24 h")),
    t.fastName, h("div", {}, t.fastBig, h("span", { class: "big-u" }, "tok/s")), t.fastFoot);
  t.netPath = "M 20 58 C 80 10, 140 10, 200 48 S 300 82, 340 38";
  t.netStage = h("div", { class: "net-stage" });
  t.netTag = h("span", { class: "tag mono" });
  t.netFoot = h("div", { class: "foot mono" });
  t.network = h("a", { class: "panel tile t-network", href: "#network" },
    h("div", { class: "th" }, chip30("globe", HUE.network), "Network", t.netTag), t.netStage, t.netFoot);
  t.gwBig = h("span", { class: "big num" }, "—");
  t.gwFoot = h("div", { class: "foot mono" });
  t.gateway = h("a", { class: "panel tile", href: "#activity" },
    h("div", { class: "th" }, chip30("shield-check", HUE.gateway), "Gateway"),
    h("div", { style: "margin-top:14px" }, t.gwBig, h("span", { class: "big-u" }, "blocked today")), t.gwFoot);
  const tiles = h("section", { class: "tiles" }, t.models, t.started, t.activity, t.fast, t.network, t.gateway);

  ctx.body.replaceChildren(strip, numstrip,
    h("div", { class: "section-title" }, "Live tiles", h("span", { class: "sub" }, "each tile is alive")), tiles);

  // ---- data that isn't in the shared poll
  let day = null;
  const loadDay = async () => { try { day = await api("GET", "/admin/metrics?range=24h"); update(); } catch (_) { /* the banner covers it */ } };
  loadDay();
  const timers = [setInterval(loadDay, 15000)];

  // ---- lanes
  function laneFor(m) {
    let lane = lanes.get(m.name);
    if (lane) return lane;
    const color = colorOf(m.name);
    const dot = h("span", { class: "stat-dot", style: { "--c": color } });
    const traceBox = h("div", {});
    const stats = h("div", { class: "stats" });
    const el = h("div", { class: "lane" }, h("div", { class: "who" }, dot, h("span", { class: "nm mono trunc", title: m.name }, m.name)), traceBox, stats);
    lane = { el, dot, traceBox, stats, color, name: m.name };
    lanes.set(m.name, lane);
    return lane;
  }
  const seriesFor = (name) => (store.series && store.series.models[name]) || [];
  function drawLane(lane, m) {
    const by = (store.metrics && store.metrics.by_model[m.name]) || {};
    lane.dot.className = `stat-dot ${m.state === "ready" ? "live" : m.state}`;
    if (m.state === "starting") {
      lane.traceBox.replaceChildren(h("div", { class: "lane-note" }, h("div", { class: "bar" }), h("span", { class: "mono" }, `compiling graph · ${Math.round(m.starting_seconds || 0)} s`)));
      lane.stats.replaceChildren(h("span", { class: "mono", style: "font-size:12.5px;color:var(--warn)" }, "starting"));
      return;
    }
    if (m.state === "crashed") {
      lane.traceBox.replaceChildren(h("div", { class: "lane-note" }, h("div", { class: "bar idle" }), h("a", { class: "mono", href: `#models/${encodeURIComponent(m.name)}/logs` }, "crashed · see log")));
      lane.stats.replaceChildren(h("span", { class: "mono bad-text", style: "font-size:12.5px" }, "crashed"));
      return;
    }
    const step = (store.series && store.series.bucket_seconds) || 10;
    const points = seriesFor(m.name).map((b) => ({ t: b.t + step / 2, v: b.decode_tps_p50 }));
    const lastVal = [...points].reverse().find((p) => p.v !== null);
    if (!lastVal) {
      lane.traceBox.replaceChildren(h("div", { class: "lane-note" }, h("div", { class: "bar idle" }), h("span", { class: "mono" }, "idle · no streamed requests in 5 min")));
    } else {
      const t1 = Date.now() / 1000;
      lane.traceBox.replaceChildren(traceSvg({ points, t0: t1 - 300, t1, w: Math.max(200, lane.traceBox.clientWidth || 640), h: 44, color: lane.color }));
    }
    const tps = by.decode_tps_p50 ?? (lastVal ? lastVal.v : null);
    lane.stats.replaceChildren(
      h("span", { class: "big num" }, tps === null ? "—" : fmt(tps, tps < 10 ? 1 : 0)), h("span", { class: "u" }, " tok/s"),
      h("span", { class: "meta mono" }, `ttft ${by.ttft_p50_ms === null || by.ttft_p50_ms === undefined ? "—" : fmt(by.ttft_p50_ms) + " ms"} · :${m.port ?? "—"}`));
  }
  function updateLanes() {
    const live = (store.models || []).filter(running);
    for (const [name, lane] of lanes) if (!live.some((m) => m.name === name)) { lane.el.remove(); lanes.delete(name); }
    if (store.models === null) { laneHost.replaceChildren(h("div", { class: "lane-note", style: "padding:14px 20px" }, "Loading…")); return; }
    if (!live.length) {
      laneHost.replaceChildren(h("div", { class: "lane", style: "grid-template-columns:1fr" },
        h("div", { class: "lane-note" }, h("div", { class: "bar idle" }),
          h("span", {}, store.models.length ? "No model is running. The next request, or mxc serve <model>, loads one." : "No models yet. Serve one with: mxc serve qwen2.5-0.5b"))));
      return;
    }
    const nodes = live.map((m) => { const lane = laneFor(m); drawLane(lane, m); return lane.el; });
    if (laneHost.children.length !== nodes.length || nodes.some((n, i) => laneHost.children[i] !== n)) laneHost.replaceChildren(...nodes);
  }

  // ---- number strip
  function updateNumbers() {
    const m = store.error ? null : store.metrics;
    const gpus = (m && m.gpus) || [];
    const used = gpus.reduce((a, g) => a + g.used_mb, 0) / 1024, total = gpus.reduce((a, g) => a + g.total_mb, 0) / 1024;
    tick(cells.decode.n, m ? m.decode_tps_p50 : null, fmt, 0); cells.decode.u.textContent = "tok/s";
    cells.decode.note.textContent = m && m.decode_tps_p50 !== null ? "p50 · last 5 min" : "no streamed requests in 5 min";
    tick(cells.ttft.n, m ? m.ttft_p50_ms : null, fmt, 0); cells.ttft.u.textContent = "ms";
    cells.ttft.note.textContent = m && m.ttft_p95_ms !== null ? `p50 · p95 ${fmt(m.ttft_p95_ms)} ms` : "p50 · time to first token";
    tick(cells.req.n, m ? m.per_minute : null, fmt, 1); cells.req.u.textContent = "/min";
    cells.req.note.textContent = m ? `${fmt(m.requests)} in 5 min · ${(m.error_rate * 100).toFixed(1)}% failed` : "—";
    tick(cells.gpu.n, m && gpus.length ? used : null, fmt, 1); cells.gpu.u.textContent = gpus.length ? `/ ${fmt(total, 0)} GB` : "";
    cells.gpu.bar.hidden = !gpus.length;
    if (gpus.length) cells.gpu.bar.firstChild.style.width = `${Math.min(100, (used / total) * 100).toFixed(0)}%`;
    cells.gpu.note.textContent = gpus.length ? `${gpus.length} × ${gpus[0].name.replace("NVIDIA GeForce ", "")}` : m ? "no NVIDIA GPU found" : "—";
  }

  // ---- tiles
  let frameSig = "", cycle = 0;
  function updateModelsTile() {
    const models = store.models || [];
    t.models.hidden = models.length === 0 && store.models !== null;
    t.started.hidden = !(store.models !== null && models.length === 0);
    const live = models.filter((m) => m.state === "ready" || m.state === "starting");
    const sig = live.map((m) => `${m.name}:${m.state}`).join("|") + `#${models.length}`;
    const by = (n) => (store.metrics && store.metrics.by_model[n]) || {};
    if (sig !== frameSig) {
      frameSig = sig;
      cycle = 0;
      const frames = live.length ? live : [];
      t.modelsFrames.replaceChildren(...(frames.length ? frames.map((m, i) => h("div", { class: i === 0 ? "cycle-frame on" : "cycle-frame", "data-name": m.name },
        h("div", { class: "nm mono" }, h("span", { class: `stat-dot ${m.state === "ready" ? "live" : m.state}`, style: { "--c": colorOf(m.name) } }), m.name),
        h("div", { class: "vv" }, h("span", { class: "n num" }), h("span", { class: "u" })), h("div", { class: "ss mono" }))) :
        [h("div", { class: "cycle-frame on" }, h("div", { class: "nm mono" }, "none running"), h("div", { class: "vv" }, h("span", { class: "n num" }, models.length), h("span", { class: "u" }, "registered")),
          h("div", { class: "ss mono" }, "loads on the first request"))]));
      t.pips.replaceChildren(...models.map((m) => h("span", { style: { background: m.state === "ready" ? colorOf(m.name) : m.state === "starting" ? "var(--warn)" : "var(--card-line)" } })));
    }
    for (const f of t.modelsFrames.children) {
      const m = live.find((x) => x.name === f.dataset.name);
      if (!m) continue;
      const n = f.querySelector(".n"), u = f.querySelector(".u"), ss = f.querySelector(".ss");
      if (m.state === "starting") { n.textContent = "compiling"; u.textContent = ""; ss.textContent = `${Math.round(m.starting_seconds || 0)} s so far`; }
      else { const v = by(m.name).decode_tps_p50; n.textContent = v === null || v === undefined ? "ready" : fmt(v); u.textContent = v === null || v === undefined ? "" : "tok/s"; ss.textContent = lifecycle(m); }
    }
    t.modelsFoot.textContent = `${models.length} registered · ${live.length} running`;
  }
  const cycleTimer = setInterval(() => {
    const frames = [...t.modelsFrames.children];
    if (frames.length < 2 || reducedMotion()) return;
    frames[cycle % frames.length].classList.remove("on");
    cycle += 1;
    frames[cycle % frames.length].classList.add("on");
  }, 3500);
  timers.push(cycleTimer);

  let tickerKey = null;
  function updateTicker() {
    const rows = (store.requests || []).slice(0, 12);
    t.tickerTag.textContent = store.metrics ? `${fmt(store.metrics.per_minute, 1)} req/min` : "";
    const key = rows.length ? rows[0].id : "none";
    if (key === tickerKey) return;
    tickerKey = key;
    if (!rows.length) {
      t.ticker.replaceChildren(h("div", { class: "tick-row mono" }, "No requests yet. Send one with curl and it lands here."));
      t.ticker.style.animation = "none";
      return;
    }
    const row = (r) => h("div", { class: "tick-row mono" },
      h("span", { class: `code ${r.status < 400 ? "ok-text" : "bad-text"}` }, String(r.status)), h("span", {}, appLabel(r.app)),
      h("span", { class: "to" }, `→ ${r.model || "—"}`), h("span", { class: "ms" }, `${fmt(r.duration_ms)} ms`));
    t.ticker.style.animation = "";
    t.ticker.style.setProperty("--dur", `${Math.max(10, rows.length * 1.6)}s`);
    t.ticker.replaceChildren(...rows.map(row), ...rows.map(row));
  }

  function updateFast() {
    const src = day && Object.keys(day.by_model || {}).length ? day : store.metrics;
    const best = Object.entries((src && src.by_model) || {}).filter(([, v]) => v.decode_tps_p50 !== null && v.decode_tps_p50 !== undefined)
      .sort((a, b) => b[1].decode_tps_p50 - a[1].decode_tps_p50)[0];
    t.fastName.textContent = best ? best[0] : "no streamed requests yet";
    tick(t.fastBig, best ? best[1].decode_tps_p50 : null, fmt, 0);
  }

  let netExposed = null;
  function updateNetwork() {
    const info = store.info || {};
    const exposed = !!info.lan_endpoint;
    t.netTag.textContent = exposed ? info.lan_endpoint.replace(/\/v1$/, "").replace(/^https?:\/\//, "") : "not exposed";
    t.netFoot.textContent = exposed ? `TLS · per-app keys · ${fmt(info.active_keys)} active` : "mxc expose opens it to your LAN";
    if (netExposed === exposed) return;
    netExposed = exposed;
    const svg = s("svg", { viewBox: "0 0 360 90", width: 360, height: 90, "aria-hidden": "true" },
      s("path", { d: t.netPath, fill: "none", "stroke-width": 2, "stroke-dasharray": "4 5", style: "stroke:var(--card-line)" }));
    const kids = [svg,
      h("span", { class: "net-ico", style: "left:6px;top:44px" }, icon("laptop", 22)), h("span", { class: "net-ico", style: "right:8px;top:18px" }, icon("server", 22))];
    if (exposed) {
      for (const [i, c] of [HUE.network, "#7c66ff"].entries()) {
        kids.push(h("span", { class: "packet", style: { background: c, offsetPath: `path('${t.netPath}')`, animationDelay: `${i * 1.3}s` } }));
      }
    }
    t.netStage.replaceChildren(h("div", { style: "position:relative;width:360px;height:90px;max-width:100%;margin:0 auto" }, ...kids));
  }

  function update() {
    const info = store.info || {};
    const models = store.models || [];
    ctx.setSub(`mxc@${info.host || "local"} · ${models.filter((m) => m.state === "ready").length} of ${models.length} models running${info.max_version ? ` · MAX ${info.max_version}` : ""}`);
    updateLanes(); updateNumbers(); updateModelsTile(); updateTicker(); updateFast(); updateNetwork();
    t.gwBig.textContent = info.blocked_today === undefined ? "—" : fmt(info.blocked_today);
    t.gwFoot.textContent = `${fmt(info.active_keys)} keys active · 401s since midnight`;
  }

  // the traces scroll along real time, once a second
  timers.push(setInterval(() => { if (!reducedMotion() && !store.error) updateLanes(); }, 1000));
  return { update, destroy: () => timers.forEach(clearInterval) };
}
