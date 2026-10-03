// Hotfire Mission Control, charts: oscilloscope traces, line charts, stacked bars. Plain SVG, no library.
import { h, s, fmt } from "./core.js";

let patternId = 0;
const themed = (el, prop, value) => { el.style.setProperty(prop, value); return el; }; // CSSOM: theme variables follow the theme switch

/** A dotted grid as one SVG pattern (cheap, and it follows the theme). */
export function dotGrid(w, hgt, step = 16) {
  const id = `dots${++patternId}`;
  return [
    s("defs", {}, s("pattern", { id, width: step, height: step, patternUnits: "userSpaceOnUse" },
      themed(s("circle", { cx: step - 0.5, cy: step - 0.5, r: 0.9 }), "fill", "var(--dotgrid)"))),
    s("rect", { width: w, height: hgt, fill: `url(#${id})` }),
  ];
}

/** Polyline path over points with gaps (null values); a lone point comes back in `dots`. */
function segments(pts, maxGap = 0) {
  let d = "";
  const dots = [];
  let run = [], gap = 0;
  const flush = () => {
    if (run.length === 1) dots.push(run[0]);
    else if (run.length > 1) d += run.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`).join(" ");
    run = [];
  };
  for (const p of pts) {
    if (p !== null) { run.push(p); gap = 0; continue; }
    gap += 1;
    if (gap > maxGap) flush(); // a longer silence than maxGap buckets breaks the line: no data is not zero
  }
  flush();
  return { d, dots };
}

/**
 * A live trace: dotted grid, the line, and a breathing "now" dot on the latest value.
 * points: [{t, v}] (v null = no data). t0..t1 is the time window mapped onto the width.
 */
export function traceSvg({ points, t0, t1, w, h: hgt, color, pad = 4, sw = 1.6, max, grid = true, step = 16, dot = true, cls = "trace" }) {
  const top = max || Math.max(1, ...points.map((p) => p.v || 0)) * 1.15;
  const X = (t) => pad + ((t - t0) / (t1 - t0)) * (w - 2 * pad);
  const Y = (v) => hgt - pad - (Math.min(v, top) / top) * (hgt - 2 * pad);
  const xy = points.map((p) => (p.v === null || p.v === undefined || p.t < t0 ? null : [X(p.t), Y(p.v)]));
  const { d, dots } = segments(xy, 6);
  const svg = s("svg", { viewBox: `0 0 ${w} ${hgt}`, class: cls, "aria-hidden": "true" });
  if (grid) svg.append(...dotGrid(w, hgt, step));
  if (d) svg.append(s("path", { d, fill: "none", stroke: color, "stroke-width": sw, "stroke-linejoin": "round", "stroke-linecap": "round", "vector-effect": "non-scaling-stroke" }));
  for (const [x, y] of dots) svg.append(s("circle", { cx: x, cy: y, r: 2.2, fill: color }));
  const last = [...xy].reverse().find(Boolean);
  if (dot && last) {
    const halo = s("circle", { class: "now-dot", cx: last[0], cy: last[1], r: 7, fill: color, opacity: 0.14 });
    halo.style.animationDelay = `-${((performance.now() / 1000) % 2).toFixed(2)}s`; // a redrawn dot keeps the breathing in phase
    svg.append(halo, s("circle", { cx: last[0], cy: last[1], r: 3, fill: color }));
  }
  return svg;
}

/** A tiny trace without grid or dot, for table rows. */
export function sparkline(points, t0, t1, color, w = 90, hgt = 22) {
  return traceSvg({ points, t0, t1, w, h: hgt, color, pad: 2, sw: 1.4, grid: false, dot: false, cls: "mini" });
}

// -- bigger charts ------------------------------------------------------------------------------
function niceMax(v) {
  if (!v || v <= 0) return 1;
  const e = 10 ** Math.floor(Math.log10(v));
  return [1, 2, 2.5, 5, 10].map((m) => m * e).find((x) => x >= v) || 10 * e;
}
function timeLabel(t, range) {
  const d = new Date(t * 1000);
  if (range === "7d") return d.toLocaleDateString([], { weekday: "short", hour: "2-digit", hour12: false }).replace(",", "");
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", ...(range === "5m" ? { second: "2-digit" } : {}), hour12: false });
}

/**
 * Mounts a chart that redraws to its container's width. draw(width) returns {svg, at(index)->tooltip lines, count, xOf}.
 */
function mount(draw) {
  const box = h("div", { class: "chart-box" });
  let state = null;
  const tip = h("div", { class: "tip mono", hidden: true });
  const render = () => {
    const w = Math.max(240, Math.floor(box.clientWidth));
    state = draw(w);
    box.replaceChildren(state.svg, tip);
  };
  const watcher = new ResizeObserver(() => {
    if (!box.isConnected) { watcher.disconnect(); return; } // the page redrew: let this chart go
    if (box.clientWidth) render();
  });
  watcher.observe(box);
  box.addEventListener("pointermove", (e) => {
    if (!state || !state.count) return;
    const rect = box.getBoundingClientRect();
    const i = Math.max(0, Math.min(state.count - 1, Math.round(((e.clientX - rect.left - state.padL) / (rect.width - state.padL - state.padR)) * (state.count - 1))));
    const lines = state.at(i);
    tip.replaceChildren(...lines.map((l) => h("div", {}, l)));
    tip.hidden = false;
    const x = state.padL + (i / Math.max(1, state.count - 1)) * (rect.width - state.padL - state.padR);
    tip.style.left = `${Math.min(rect.width - tip.offsetWidth - 4, Math.max(0, x + 10))}px`;
    state.guide.setAttribute("x1", x); state.guide.setAttribute("x2", x); state.guide.style.opacity = "1";
  });
  box.addEventListener("pointerleave", () => { tip.hidden = true; if (state) state.guide.style.opacity = "0"; });
  requestAnimationFrame(() => { if (box.clientWidth) render(); });
  return box;
}

function frame(w, hgt, padL, padR, padT, padB, top, unit, labelsT, range) {
  const svg = s("svg", { viewBox: `0 0 ${w} ${hgt}`, width: w, height: hgt, role: "img" });
  const plotH = hgt - padT - padB;
  for (const f of [0, 0.5, 1]) {
    const y = padT + plotH * (1 - f);
    svg.append(themed(s("line", { x1: padL, x2: w - padR, y1: y, y2: y, "stroke-width": 1, "stroke-dasharray": f ? "2 4" : "" }), "stroke", "var(--card-line-2)"));
    svg.append(s("text", { class: "ax", x: padL - 6, y: y + 3.5, "text-anchor": "end" }, fmt(top * f, top < 10 ? 1 : 0)));
  }
  const n = labelsT.length;
  labelsT.forEach((t, i) => svg.append(s("text", { class: "ax", x: padL + (i / Math.max(1, n - 1)) * (w - padL - padR), y: hgt - 6, "text-anchor": i === 0 ? "start" : i === n - 1 ? "end" : "middle" }, timeLabel(t, range))));
  const guide = themed(s("line", { y1: padT, y2: padT + plotH, "stroke-width": 1, x1: 0, x2: 0 }), "stroke", "var(--faint)");
  guide.style.opacity = "0";
  svg.append(guide);
  return { svg, guide, plotH };
}

/** Lines per series over time. series: [{name, color, points:[{t, v}]}] */
export function lineChart({ series, buckets, range, unit, height = 180 }) {
  const all = series.flatMap((x) => x.points.map((p) => p.v).filter((v) => v !== null && v !== undefined));
  if (!all.length) return h("div", { class: "chart-empty" }, "No data in this range yet");
  const top = niceMax(Math.max(...all));
  return mount((w) => {
    const padL = 38, padR = 8, padT = 8, padB = 22;
    const labels = [0, 1, 2, 3, 4].map((i) => buckets[Math.min(buckets.length - 1, Math.round((i * (buckets.length - 1)) / 4))]);
    const { svg, guide, plotH } = frame(w, height, padL, padR, padT, padB, top, unit, labels, range);
    const X = (i) => padL + (i / Math.max(1, buckets.length - 1)) * (w - padL - padR);
    const Y = (v) => padT + plotH * (1 - Math.min(v, top) / top);
    const maxGap = Math.ceil(buckets.length / 6); // quiet stretches shorter than this stay joined; longer ones show as gaps
    for (const line of series) {
      const xy = line.points.map((p, i) => (p.v === null || p.v === undefined ? null : [X(i), Y(p.v)]));
      const { d } = segments(xy, maxGap);
      if (d) svg.insertBefore(s("path", { d, fill: "none", stroke: line.color, "stroke-width": 1.6, "stroke-linejoin": "round", "stroke-linecap": "round", opacity: 0.9 }), guide);
      for (const pt of xy) if (pt) svg.insertBefore(s("circle", { cx: pt[0], cy: pt[1], r: 2.3, fill: line.color }), guide);
    }
    return { svg, guide, count: buckets.length, padL, padR,
      at: (i) => [timeLabel(buckets[i], range), ...series.map((x) => `${x.name}  ${x.points[i].v === null ? "—" : fmt(x.points[i].v, 1)} ${unit}`)] };
  });
}

/** Stacked bars, ok on the bottom and failed on top. rows: [{t, ok, failed}] */
export function stackedBars({ rows, range, height = 180 }) {
  const total = rows.map((r) => r.ok + r.failed);
  if (!total.some((v) => v > 0)) return h("div", { class: "chart-empty" }, "No requests in this range yet");
  const top = niceMax(Math.max(...total));
  return mount((w) => {
    const padL = 38, padR = 8, padT = 8, padB = 22;
    const labels = [0, 1, 2, 3, 4].map((i) => rows[Math.min(rows.length - 1, Math.round((i * (rows.length - 1)) / 4))].t);
    const { svg, guide, plotH } = frame(w, height, padL, padR, padT, padB, top, "", labels, range);
    const slot = (w - padL - padR) / rows.length, bw = Math.max(1.5, slot * 0.7);
    rows.forEach((r, i) => {
      const x = padL + i * slot + (slot - bw) / 2;
      const okH = (r.ok / top) * plotH, badH = (r.failed / top) * plotH;
      if (r.ok) svg.insertBefore(s("rect", { x, y: padT + plotH - okH, width: bw, height: okH, fill: "#0090ff", rx: 1.5 }), guide);
      if (r.failed) svg.insertBefore(s("rect", { x, y: padT + plotH - okH - badH, width: bw, height: badH, fill: "#e5484d", rx: 1.5 }), guide);
    });
    return { svg, guide, count: rows.length, padL: padL + slot / 2, padR: padR + slot / 2,
      at: (i) => [timeLabel(rows[i].t, range), `ok  ${fmt(rows[i].ok)}`, `failed  ${fmt(rows[i].failed)}`] };
  });
}
