// Metrics: four bold colour blocks, "What you should care about" (plain rules, no model), and charts per model.
import { h, icon, api, fmt, colorOf, HUE, store, assignColours } from "./core.js";
import { lineChart, stackedBars } from "./charts.js";
import { stopModel } from "./actions.js";

const RANGES = [["5m", "5m"], ["1h", "1h"], ["24h", "24h"], ["7d", "7d"]];
const RANGE_WORDS = { "5m": "5 minutes", "1h": "hour", "24h": "24 hours", "7d": "7 days" };

export function metricsPage(ctx) {
  ctx.actions.append(h("a", { class: "btn", href: "#activity" }, icon("activity", 15), "Activity"));
  let range = "1h", data = null, failed = null, insightRows = null;
  try { const saved = localStorage.getItem("hotfire-range"); if (RANGES.some(([r]) => r === saved)) range = saved; } catch (_) { /* no storage: default range */ }

  const seg = h("div", { class: "seg", role: "group", "aria-label": "Range" }, RANGES.map(([r, label]) => h("button", { type: "button", "aria-pressed": r === range ? "true" : "false", "data-r": r,
    onClick: () => { range = r; try { localStorage.setItem("hotfire-range", r); } catch (_) { /* fine */ } seg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", b.dataset.r === r ? "true" : "false")); load(); } }, label)));
  const modelCount = h("span", { class: "hint mono" });
  const host = h("div", {});
  ctx.body.replaceChildren(h("div", { class: "range-row" }, seg, modelCount), host);

  const block = (k, n, u, hue) => h("div", { class: "block", style: { "--hue": hue } }, h("div", { class: "k" }, k), h("div", { class: "n num" }, n), h("div", { class: "u mono" }, u));
  const pillSev = (sev) => (sev === "bad" ? HUE.bench : "#e09d00");

  function insightRow(i) {
    const act = i.action && i.action.stop ? h("button", { class: "btn", type: "button", onClick: async () => { await stopModel(i.action.stop); loadInsights(); } }, i.action.label) : null;
    return h("div", { class: "insight" },
      h("span", { class: "chip30", style: { "--hue": pillSev(i.severity), display: "inline-flex", width: "30px", height: "30px", borderRadius: "8px", alignItems: "center", justifyContent: "center", background: `color-mix(in srgb, ${pillSev(i.severity)} 12%, transparent)`, color: pillSev(i.severity), flexShrink: 0 } }, icon(i.icon, 15, "currentColor", 1.8)),
      h("div", { class: "bd" }, h("div", { class: "tt" }, i.title), h("div", { class: "dd" }, i.detail), h("div", { class: "cli mono" }, `$ ${i.cli}`)), act);
  }

  function draw() {
    if (failed) { host.replaceChildren(h("div", { class: "panel empty" }, h("h2", {}, "Could not load metrics"), h("p", {}, failed))); return; }
    if (!data) { host.replaceChildren(h("div", { class: "panel empty" }, h("p", {}, "Loading…"))); return; }
    const { summary: m, series } = data;
    const names = Object.keys(series.models);
    assignColours(store.models);
    ctx.setSub(`last ${RANGE_WORDS[range]} · p50 per ${series.bucket_seconds >= 3600 ? `${series.bucket_seconds / 3600} h` : series.bucket_seconds >= 60 ? `${series.bucket_seconds / 60} min` : `${series.bucket_seconds} s`} · streamed requests`);
    modelCount.textContent = `${names.length} model${names.length === 1 ? "" : "s"} with traffic`;
    const failedPct = m.requests ? (m.error_rate * 100).toFixed(1) : "0.0";
    const blocks = h("section", { class: "blocks" },
      block("Decode", m.decode_tps_p50 === null ? "—" : fmt(m.decode_tps_p50), "tok/s p50", HUE.models),
      block("First token", m.ttft_p50_ms === null ? "—" : fmt(m.ttft_p50_ms), m.ttft_p95_ms === null ? "ms p50" : `ms p50 · p95 ${fmt(m.ttft_p95_ms)}`, HUE.pull),
      block("Requests", fmt(m.requests), `last ${RANGE_WORDS[range]} · ${fmt(m.per_minute, 1)}/min`, HUE.metrics),
      block("Failed", `${failedPct}%`, `${fmt(m.errors)} request${m.errors === 1 ? "" : "s"}`, HUE.bench));

    const rows = insightRows || [];
    const care = h("section", { class: "panel care on-card" },
      h("div", { class: "care-head" }, icon("sparkles", 15, HUE.metrics), h("span", { class: "t" }, "What you should care about"),
        h("span", { class: "s mono" }, insightRows === null ? "checking…" : rows.length ? `${rows.length} thing${rows.length === 1 ? "" : "s"}` : "")),
      ...(insightRows === null ? [] : rows.length ? rows.map(insightRow)
        : [h("div", { class: "insight none" }, icon("circle-check", 18, "var(--ok)", 1.8), "Nothing needs you right now")]));

    const mk = (key) => names.map((n) => ({ name: n, color: colorOf(n), points: series.models[n].map((b) => ({ t: b.t, v: b[key] })) }));
    const legend = () => h("div", { class: "legend mono" }, names.map((n) => h("span", {}, h("i", { style: { "--c": colorOf(n) } }), n)));
    const totals = series.buckets.map((t, i) => ({ t, ok: names.reduce((a, n) => a + series.models[n][i].ok, 0), failed: names.reduce((a, n) => a + series.models[n][i].errors, 0) }));
    const card = (title, unit, extra, chart, cls = "") => h("section", { class: `panel chart-card on-card ${cls}` }, h("div", { class: "ch" }, h("span", { class: "t" }, title), h("span", { class: "u mono" }, unit), extra), chart);
    const charts = h("div", { class: "charts" },
      card("Decode throughput", "tok/s p50", legend(), lineChart({ series: mk("decode_tps_p50"), buckets: series.buckets, range, unit: "tok/s" })),
      card("Time to first token", "ms p50", legend(), lineChart({ series: mk("ttft_p50_ms"), buckets: series.buckets, range, unit: "ms" })),
      card("Requests", "per bucket", h("div", { class: "legend mono" }, h("span", {}, h("i", { class: "sq", style: { "--c": "#0090ff" } }), "ok"), h("span", {}, h("i", { class: "sq", style: { "--c": "#e5484d" } }), "failed")),
        stackedBars({ rows: totals, range }), "wide"));

    const per = Object.entries(m.by_model || {});
    const table = per.length ? h("section", { class: "panel scroll-x", style: "margin-top:16px" }, h("table", { class: "grid" },
      h("thead", {}, h("tr", {}, ["Model", "Requests", "Failed", "TTFT p50", "TTFT p95", "Decode p50"].map((c, i) => h("th", { class: i ? "r" : "" }, c)))),
      h("tbody", {}, per.map(([n, v]) => h("tr", {}, h("td", {}, h("span", { style: "display:inline-flex;align-items:center;gap:8px" }, h("span", { class: "stat-dot", style: { "--c": colorOf(n), background: colorOf(n) } }), h("span", { class: "mono" }, n))),
        h("td", { class: "r num" }, fmt(v.requests)), h("td", { class: "r num" }, fmt(v.errors)),
        h("td", { class: "r num" }, v.ttft_p50_ms === null ? "—" : `${fmt(v.ttft_p50_ms)} ms`), h("td", { class: "r num" }, v.ttft_p95_ms === null ? "—" : `${fmt(v.ttft_p95_ms)} ms`),
        h("td", { class: "r num" }, v.decode_tps_p50 === null ? "—" : `${fmt(v.decode_tps_p50)} tok/s`)))))) : null;
    host.replaceChildren(...[blocks, care, charts, table].filter(Boolean), h("div", { class: "cli-note mono" }, h("span", {}, `$ hotfire metrics --range ${range}`), h("span", {}, "$ hotfire requests --limit 50")));
  }

  async function load() {
    try {
      const [summary, series] = await Promise.all([api("GET", `/admin/metrics?range=${range}`), api("GET", `/admin/series?range=${range}`)]);
      data = { summary, series }; failed = null;
    } catch (e) { failed = e.message; }
    draw();
  }
  async function loadInsights() {
    try { insightRows = await api("GET", "/admin/insights"); } catch (_) { insightRows = []; }
    draw();
  }
  load();
  loadInsights();
  const timer = setInterval(() => { load(); loadInsights(); }, 10000);
  return { update() {}, destroy() { clearInterval(timer); } };
}
