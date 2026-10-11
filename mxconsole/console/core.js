// mxconsole, shared pieces: DOM helpers, admin API calls, polling, formatting. (The theme lives in prefs.js.)
// Every piece of data (model names, logs, request fields) goes in as a text node, never as HTML.
import { icon } from "./icons.js";

export const ADMIN = { "X-Mst-Admin": "1" };
export const POLL_MS = 3000;
const SVG_NS = "http://www.w3.org/2000/svg";

// Model signal colours, in load order (design system), and one hue per menu section.
export const SIGNAL = ["#7c66ff", "#0090ff", "#12a594", "#f76b15", "#e5484d"];
export const HUE = {
  overview: "#0090ff", activity: "#7c66ff", models: "#8e4ec6", pull: "#12a594", ports: "#ad7f58", keys: "#e09d00",
  network: "#00a2c7", api: "#3e63dd", metrics: "#e93d82", bench: "#e5484d", logs: "#697177", gateway: "#f76b15", daemon: "#30a46c",
};

// -- DOM ---------------------------------------------------------------------------------------
export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  apply(el, attrs);
  append(el, children);
  return el;
}
export function s(tag, attrs, ...children) {
  const el = document.createElementNS(SVG_NS, tag);
  apply(el, attrs);
  append(el, children);
  return el;
}
function apply(el, attrs) {
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v === null || v === undefined) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === "class") el.setAttribute("class", v);
    else if (k === "style") { // CSSOM, which the console's CSP allows (style="" attributes it blocks)
      if (typeof v === "string") el.style.cssText = v;
      else for (const [p, val] of Object.entries(v)) el.style.setProperty(p.startsWith("--") ? p : p.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`), String(val));
    } else el.setAttribute(k, v === true ? "" : String(v));
  }
}
function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}
export const $ = (id) => document.getElementById(id);
export const clear = (el) => el.replaceChildren();
export { icon };

/** An app name as shown in lists. Names starting with "_" or "console" are mxconsole's own traffic: the
 *  benchmark gets the gauge icon, muted. Both still count in the gauges, because the GPU really was busy. */
export function appLabel(name) {
  if (!name) return h("span", { class: "muted" }, "no key");
  if (name === "_bench") return h("span", { class: "muted app-internal" }, icon("gauge", 13, "currentColor", 1.8), " benchmark");
  if (name === "console") return h("span", { class: "muted app-internal" }, "console");
  return name;
}

/** A hue chip: tinted square with a Lucide icon in the section's colour. */
export function chip(name, hue, size = 24, iconSize = 15) {
  return h("span", { class: "hue-chip", style: { "--hue": hue, width: `${size}px`, height: `${size}px` } }, icon(name, iconSize, "currentColor", 1.8));
}

// -- admin API ---------------------------------------------------------------------------------
export async function api(method, path, body) {
  const res = await fetch(path, {
    method, headers: { ...ADMIN, ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let message = `HTTP ${res.status}`, body = null, code = null;
    try { body = await res.json(); message = body.error.message; code = body.error.code ?? null; } catch (_) { /* keep the status */ }
    const err = new Error(message);
    err.status = res.status;
    if (body !== null) err.body = body;   // the parsed JSON (e.g. {error, port: job} on a 409)
    if (code !== null) err.code = code;   // the OpenAI-shape error.code
    throw err;
  }
  const type = res.headers.get("content-type") || "";
  return type.includes("json") ? res.json() : res.text();
}

// -- shared data, polled ------------------------------------------------------------------------
export const store = {
  models: null,      // null = still loading
  info: null, infoAt: 0,
  metrics: null,     // last 5 minutes, all models + gpus
  series: null,      // last 5 minutes in 10 s buckets, per model
  requests: null,    // last 40
  error: null,
};
const bus = new EventTarget();
export const onData = (fn) => { const f = () => fn(); bus.addEventListener("data", f); return () => bus.removeEventListener("data", f); };
export const emitData = () => bus.dispatchEvent(new Event("data"));

export async function refresh() {
  const wasDown = !!store.error;
  try {
    const [models, metrics, series, requests] = await Promise.all([
      api("GET", "/admin/models"), api("GET", "/admin/metrics"), api("GET", "/admin/series?range=5m"), api("GET", "/admin/requests?limit=40"),
    ]);
    Object.assign(store, { models, metrics, series, requests, error: null });
    if (!store.info || wasDown || Date.now() - store.infoAt > 15000) {
      store.info = await api("GET", "/admin/info");
      store.infoAt = Date.now();
    }
  } catch (e) {
    store.error = e;
  }
  emitData();
}
export function startPolling() {
  refresh();
  setInterval(refresh, POLL_MS);
}

let colours = null;
/** A model keeps one signal colour everywhere: assigned the first time it is seen running, in load order.
 * The assignment is remembered in this browser, so a reload doesn't reshuffle the colours. */
export function colorOf(name) {
  if (colours === null) {
    colours = new Map();
    try { for (const [k, v] of JSON.parse(localStorage.getItem("mxc-colours") || "[]")) colours.set(k, v); } catch (_) { /* no storage: assign afresh */ }
  }
  if (!colours.has(name)) {
    colours.set(name, colours.size % SIGNAL.length);
    try { localStorage.setItem("mxc-colours", JSON.stringify([...colours])); } catch (_) { /* fine */ }
  }
  return SIGNAL[colours.get(name)];
}
// -- formatting --------------------------------------------------------------------------------
export const fmt = (v, digits = 0) => (v === null || v === undefined ? "—" : Number(v).toLocaleString(undefined, { maximumFractionDigits: digits }));
export function dur(sec) {
  sec = Math.round(sec);
  if (sec >= 3600 && sec % 3600 === 0) return `${sec / 3600}h`;
  if (sec >= 60 && sec % 60 === 0) return `${sec / 60}m`;
  if (sec >= 60) return `${Math.floor(sec / 60)}m ${sec % 60}s`;
  return `${sec}s`;
}
export function uptime(seconds) {
  if (seconds === null || seconds === undefined) return "—";
  const d = Math.floor(seconds / 86400), hh = Math.floor((seconds % 86400) / 3600), mm = Math.floor((seconds % 3600) / 60);
  return d ? `${d}d ${hh}h` : hh ? `${hh}h ${mm}m` : `${mm}m ${Math.floor(seconds % 60)}s`;
}
export function ago(ts) {
  if (!ts) return "never";
  const sec = Math.max(0, Date.now() / 1000 - ts);
  if (sec < 5) return "just now";
  if (sec < 90) return `${Math.round(sec)} s ago`;
  if (sec < 5400) return `${Math.round(sec / 60)} min ago`;
  if (sec < 172800) return `${Math.round(sec / 3600)} h ago`;
  return `${Math.round(sec / 86400)} d ago`;
}
export const clock = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour12: false });
export const dateTime = (ts) => new Date(ts * 1000).toLocaleString([], { hour12: false, month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
export const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Count a number up (or down) to a new value; instant under reduced motion. */
export function tick(el, to, format, digits = 0) {
  const from = Number(el.dataset.v);
  el.dataset.v = to === null ? "" : String(to);
  if (to === null || Number.isNaN(from) || reducedMotion() || from === to) { el.textContent = to === null ? "—" : format(to, digits); return; }
  const t0 = performance.now();
  const step = (now) => {
    const k = Math.min(1, (now - t0) / 400);
    el.textContent = format(from + (to - from) * (1 - (1 - k) ** 3), digits);
    if (k < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

// -- clipboard and toast -----------------------------------------------------------------------
export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (_) {
    const area = h("textarea", { style: "position:fixed;opacity:0;top:0;left:0", "aria-hidden": "true" });
    area.value = text;
    document.body.append(area);
    area.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (_e) { /* no clipboard at all */ }
    area.remove();
    return ok;
  }
}
let toastTimer = 0;
export function toast(message, ms = 3200) {
  const el = $("toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, ms);
}
/** A "Copy" button that says it copied. */
export function copyButton(getText, label = "Copy", cls = "btn") {
  const btn = h("button", { class: cls, type: "button" }, icon("copy", 14), h("span", {}, label));
  btn.addEventListener("click", async () => {
    const ok = await copyText(getText());
    toast(ok ? "Copied" : "Could not reach the clipboard: select the text and copy it");
    if (ok) { btn.lastChild.textContent = "Copied"; setTimeout(() => { btn.lastChild.textContent = label; }, 1400); }
  });
  return btn;
}
