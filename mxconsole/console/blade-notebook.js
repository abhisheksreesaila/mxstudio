// mxconsole, the Notebook blade (wide): the generated notebook for a model, read-only. Chapters on the left,
// the markdown and code cells with their saved outputs in the middle, the architecture on the right.
// Data: GET /admin/learn/{name} and POST /admin/learn/{name}/open. The saved view runs nothing; "Run in Jupyter"
// shows Jupyter's single-notebook view in an iframe (Jupyter listens on 127.0.0.1 only and lets the console frame it).
//
// Safety: the notebook text comes from a model card on the internet. It is never parsed as HTML: markdown goes
// through the tiny parser below into a node-description tree (pure, tested under node), and the tree is built
// with createElement/createTextNode. `<script>` is just text; links are http/https only; images are never loaded
// (the console's CSP is default-src 'self', which also blocks data: images, so an image output points to Jupyter).
import { h, icon, api, store, onData, fmt, copyText, toast, reducedMotion } from "./core.js";
import { register, setHead } from "./blades.js";

/** Only Jupyter's single-notebook view, on this machine, goes in the iframe. On a console opened at localhost it
 * is framed at localhost too: 127.0.0.1 would be another site, the browser would hold back Jupyter's cookies, and
 * the kernel would never connect. */
export function embedUrl(url, pageHost) {
  const u = localUrl(url);
  if (!u || !new URL(u).pathname.startsWith("/doc/tree/")) return null;
  if (pageHost !== "localhost") return u;
  const same = new URL(u);
  same.hostname = "localhost";
  return same.href;
}

// -- markdown, as data -------------------------------------------------------------------------------
/** Only absolute http(s) URLs may become links. Anything else (javascript:, data:, relative) is not a link. */
export function safeUrl(url) {
  try {
    const u = new URL(String(url));
    return u.protocol === "http:" || u.protocol === "https:" ? u.href : null;
  } catch (_) { return null; }
}

/** Jupyter is started by the daemon on this machine: only an http://127.0.0.1 link is ever opened. */
export function localUrl(url) {
  const u = safeUrl(url);
  return u && new URL(u).protocol === "http:" && new URL(u).hostname === "127.0.0.1" ? u : null;
}

const T = (text) => ({ text });
// Every quantifier is bounded, so each start position costs at most ~500 steps: linear overall, whatever the input.
const INLINE = /`([^`\n]{1,500})`|\*\*([^*\n]{1,500})\*\*|(?<![\w])__([^_\n]{1,500})__(?![\w])|\*([^\s*][^*\n]{0,499})\*|(?<![\w])_([^\s_][^_\n]{0,499})_(?![\w])|\[([^\]\n]{1,500})\]\(([^)\s]{1,2000})\)/g;
const MAX_MD = 200000; // a notebook cell is never this big; the rest is dropped rather than parsed

/** Inline markdown to nodes: **bold**, *italic*, `code`, [text](http-url). Everything else stays text. */
export function parseInline(src) {
  const text = String(src ?? "").slice(0, MAX_MD), out = [];
  let last = 0;
  const push = (node) => {
    const prev = out[out.length - 1];
    if (node.text !== undefined && prev && prev.text !== undefined) prev.text += node.text;
    else out.push(node);
  };
  for (const m of text.matchAll(INLINE)) {
    if (m.index > last) push(T(text.slice(last, m.index)));
    last = m.index + m[0].length;
    if (m[1] !== undefined) push({ tag: "code", children: [T(m[1])] });
    else if (m[2] !== undefined || m[3] !== undefined) push({ tag: "strong", children: parseInline(m[2] ?? m[3]) });
    else if (m[4] !== undefined || m[5] !== undefined) push({ tag: "em", children: parseInline(m[4] ?? m[5]) });
    else {
      const href = safeUrl(m[7]);
      if (href) push({ tag: "a", attrs: { href, target: "_blank", rel: "noopener noreferrer" }, children: parseInline(m[6]) });
      else push(T(m[0])); // javascript:, data:, relative: shown as the text it was, not a link
    }
  }
  if (last < text.length) push(T(text.slice(last)));
  return out;
}

const FENCE = /^\s*(```+|~~~+)\s*([\w+.-]*)\s*$/;
const HEADING = /^(#{1,6})\s+(.*)$/;
const headText = (t) => t.replace(/\s+$/, "").replace(/\s+#+$/, "");
const ITEM = /^\s*([-*+]|\d+[.)])\s+(.*)$/;
const blank = (l) => /^\s*$/.test(l);

/** Block markdown to a node-description tree: headings, paragraphs, lists, fenced code. Anything else is text.
 * A node is {tag, attrs?, children?} or {text}. */
export function parseMarkdown(src) {
  const lines = String(src ?? "").slice(0, MAX_MD).replace(/\r\n?/g, "\n").split("\n");
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (blank(line)) { i++; continue; }
    const fence = FENCE.exec(line);
    if (fence) {
      const body = [];
      i++;
      while (i < lines.length && !(lines[i].trim().startsWith(fence[1]) && lines[i].trim().replace(/[`~]/g, "") === "")) body.push(lines[i++]);
      i++; // the closing fence; an unclosed one runs to the end
      out.push({ tag: "pre", attrs: fence[2] ? { "data-lang": fence[2] } : {}, children: [{ tag: "code", children: [T(body.join("\n"))] }] });
      continue;
    }
    const head = HEADING.exec(line);
    if (head) { out.push({ tag: `h${head[1].length}`, children: parseInline(headText(head[2])) }); i++; continue; }
    const item = ITEM.exec(line);
    if (item) {
      const ordered = /\d/.test(item[1][0]), items = [];
      while (i < lines.length) {
        const m = ITEM.exec(lines[i]);
        if (m && /\d/.test(m[1][0]) === ordered) { items.push([m[2]]); i++; }
        else if (!blank(lines[i]) && /^\s+\S/.test(lines[i]) && items.length) { items[items.length - 1].push(lines[i].trim()); i++; }
        else break;
      }
      out.push({ tag: ordered ? "ol" : "ul", children: items.map((parts) => ({ tag: "li", children: parseInline(parts.join(" ")) })) });
      continue;
    }
    const para = [];
    while (i < lines.length && !blank(lines[i]) && !FENCE.test(lines[i]) && !HEADING.test(lines[i]) && !(para.length && ITEM.test(lines[i]))) para.push(lines[i++].trim());
    out.push({ tag: "p", children: parseInline(para.join(" ")) });
  }
  return out;
}

/** The plain text of a node tree (a heading's title in the chapter list). */
export function plainText(nodes) {
  return (nodes || []).map((n) => (n.text !== undefined ? n.text : plainText(n.children))).join("");
}

/** Build real DOM from a node-description tree. Text always goes in as a text node. */
export function buildNodes(nodes) {
  return (nodes || []).map((n) => (n.text !== undefined ? document.createTextNode(n.text) : h(n.tag, n.attrs || {}, ...buildNodes(n.children))));
}

// -- the notebook, as data ---------------------------------------------------------------------------
/** Chapters from the markdown headings (levels 1 and 2; any level when the notebook has none of those).
 * Returns [{title, level, cell}] where `cell` is the index of the cell holding the heading. */
export function chapters(cells) {
  const all = [];
  (cells || []).forEach((c, cell) => {
    if (c.type !== "markdown") return;
    for (const b of parseMarkdown(c.source)) {
      const m = /^h([1-6])$/.exec(b.tag);
      if (m) all.push({ title: plainText(b.children).trim(), level: Number(m[1]), cell });
    }
  });
  const top = all.filter((c) => c.level <= 2);
  return (top.length ? top : all).filter((c) => c.title);
}

const joinText = (v) => (Array.isArray(v) ? v.join("") : String(v ?? ""));
const stripAnsi = (s) => s.replace(/\x1b\[[0-9;?]*[ -/]*[@-~]/g, "");
const IMAGE = ["image/png", "image/jpeg", "image/gif", "image/svg+xml"];

/** Saved nbformat outputs to what the blade shows: {kind: text|error|image|other, text?, mime?, stream?}. */
export function outputsView(outputs) {
  const out = [];
  for (const o of outputs || []) {
    if (o.output_type === "stream") out.push({ kind: "text", stream: o.name || "stdout", text: joinText(o.text) });
    else if (o.output_type === "error") out.push({ kind: "error", text: stripAnsi((o.traceback || []).map(joinText).join("\n")) || `${o.ename || "Error"}: ${o.evalue || ""}` });
    else if (o.output_type === "execute_result" || o.output_type === "display_data") {
      const data = o.data || {}, img = IMAGE.find((m) => data[m]);
      if (img) out.push({ kind: "image", mime: img }); // not drawn: the CSP blocks data: images
      else if (data["text/plain"] !== undefined) out.push({ kind: "text", stream: "result", text: joinText(data["text/plain"]) });
      else { const mime = Object.keys(data)[0]; if (mime) out.push({ kind: "other", mime }); }
    }
  }
  return out;
}

/** The [n] beside a code cell: the saved execution count, or null (shown as "[ ]") when it was never run. */
export function execCount(cell) {
  const result = (cell.outputs || []).find((o) => o.output_type === "execute_result" && Number.isInteger(o.execution_count));
  if (result) return result.execution_count;
  return Number.isInteger(cell.execution_count) ? cell.execution_count : null;
}

/** The kernel chip: the notebook talks to the model's endpoint, so it needs the model running. */
export function kernelChip(model, endpoint) {
  const on = !!model && model.state === "ready";
  return { on, text: on ? `model running · ${String(endpoint || "").replace(/^https?:\/\//, "")}` : "start it to run cells" };
}

/** What Run in Jupyter says when it fails, and the command that fixes it. */
export function jupyterProblem(err, path) {
  if (err.status === 501) return { text: err.message, fix: "pixi add jupyterlab" };
  return { text: err.message, fix: `jupyter lab ${path || "<notebook>.ipynb"}` };
}

const MAX_OUTPUT = 20000;
const compact = (n) => (n >= 1e9 ? `${fmt(n / 1e9, 2)} B` : n >= 1e6 ? `${fmt(n / 1e6, 1)} M` : fmt(n));
const showValue = (v) => (typeof v === "object" && v !== null ? JSON.stringify(v) : String(v));

// -- DOM ---------------------------------------------------------------------------------------------
function outputEl(o) {
  if (o.kind === "image") return h("div", { class: "nb-out note" }, icon("external-link", 13), `image output (${o.mime}): run it in Jupyter to see it`);
  if (o.kind === "other") return h("div", { class: "nb-out note" }, icon("external-link", 13), `${o.mime} output: run it in Jupyter to see it`);
  const cut = o.text.length > MAX_OUTPUT;
  return h("pre", { class: `nb-out ${o.kind === "error" ? "err" : o.stream === "stderr" ? "stderr" : ""}` }, cut ? `${o.text.slice(0, MAX_OUTPUT)}\n… (cut: run it in Jupyter for the rest)` : o.text);
}

function cellEl(cell) {
  if (cell.type !== "code") return h("section", { class: "card nb-cell" }, h("div", { class: "nb-md" }, ...buildNodes(parseMarkdown(cell.source))));
  const n = execCount(cell), outs = outputsView(cell.outputs);
  return h("section", { class: "card nb-cell" },
    h("div", { class: "nb-in" },
      h("div", { class: "nb-n mono", title: n === null ? "not run" : `run ${n}` }, n === null ? "[ ]" : `[${n}]`),
      h("pre", { class: "nb-code mono" }, h("code", {}, cell.source))),
    outs.length ? h("div", { class: "nb-outs" }, outs.map(outputEl)) : null);
}

function archPanel(nb) {
  const a = nb.architecture;
  const kv = [];
  if (a) {
    const p = a.parameters;
    if (p) kv.push(["Layers", fmt(p.layers)], ["Parameters", `${compact(p.total)} (from config)`], ["Embeddings", compact(p.embeddings)], ["Per layer", `attn ${compact(p.attention_per_layer)} · mlp ${compact(p.mlp_per_layer)}`]);
    if (p && p.experts > 1) kv.push(["Experts", fmt(p.experts)]);
    if (a.safetensors_total) kv.push(["Weights", `${compact(a.safetensors_total)} (safetensors)`]);
  }
  return h("aside", { class: "nb-side", "aria-label": "Architecture" },
    h("section", { class: "card" }, h("div", { class: "card-h" }, h("h3", {}, "Architecture")),
      h("div", { class: "card-b" },
        a ? h("div", { class: "nb-arch mono" }, a.architecture || "unknown architecture") : h("p", { class: "nb-none" }, "This notebook has no architecture summary."),
        kv.length ? h("dl", { class: "ox-dl nb-kv" }, kv.map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])) : null,
        a && a.rows.length ? h("table", { class: "nb-rows" }, h("tbody", {}, a.rows.map((r) =>
          h("tr", { title: r.meaning || null }, h("th", { class: "mono" }, r.field), h("td", { class: "mono" }, showValue(r.value)))))) : null),
      h("div", { class: "src note" }, "read from the model's config")),
    h("section", { class: "card" }, h("div", { class: "card-h" }, h("h3", {}, "Kernel")),
      h("div", { class: "card-b" }, h("p", { class: "nb-none" }, nb.kernelspec && (nb.kernelspec.display_name || nb.kernelspec.name) ? `${nb.kernelspec.display_name || nb.kernelspec.name} · saved outputs only, nothing runs here` : "saved outputs only, nothing runs here")),
      h("div", { class: "src note" }, "Run in Jupyter above to execute cells")));
}

register("notebook", (blade) => {
  const id = String(blade.opts.id || "");
  const enc = encodeURIComponent(id);
  let nb = null;

  const chipEl = h("span", { class: "badge kchip" }, h("i"), h("span", { class: "kt" }, "start it to run cells"));
  const status = h("div", { class: "nb-status", role: "status", hidden: true });
  const modeBtn = h("button", { class: "btn tonal sm", type: "button", disabled: true });
  const tabLink = h("a", { class: "btn text icon sm", target: "_blank", rel: "noopener noreferrer", "aria-label": "Open Jupyter in a new tab", title: "Open in a new tab", hidden: true }, icon("external-link", 14));
  const shell = h("div", { class: "nb" });
  const saved = h("div", { class: "nb-saved" });
  const frameWrap = h("div", { class: "nb-frame", hidden: true });
  let frame = null; // kept while the blade is open, so switching views doesn't restart the kernel

  function setMode(jupyter) {
    saved.hidden = jupyter;
    frameWrap.hidden = !jupyter;
    modeBtn.replaceChildren(...(jupyter ? [icon("book-open", 13), "Saved view"] : [icon("play", 13), "Run in Jupyter"]));
  }
  setMode(false);

  function paintChip() {
    const v = kernelChip((store.models || []).find((m) => m.name === id), store.info && store.info.endpoint);
    chipEl.classList.toggle("ok", v.on);
    chipEl.lastChild.textContent = v.text;
  }
  function say(...content) { status.replaceChildren(...content); status.hidden = !content.length; }

  async function runInJupyter() {
    if (frame) { setMode(saved.hidden === false); return; }
    modeBtn.disabled = true;
    status.classList.remove("bad");
    say(icon("loader-circle", 13), "Starting Jupyter… the first start takes a few seconds.");
    try {
      const r = await api("POST", `/admin/learn/${enc}/open`);
      if (!blade.alive) return;
      const url = embedUrl(r.embed_url, location.hostname), full = localUrl(r.url);
      if (!url || !full) throw new Error("Jupyter returned a link that is not on this machine (http://127.0.0.1), so it was not opened");
      frame = h("iframe", { title: `Jupyter: ${id}`, allow: "clipboard-read; clipboard-write" });
      frame.src = url;
      frameWrap.replaceChildren(frame);
      tabLink.href = full;
      tabLink.hidden = false;
      say();
      setMode(true);
    } catch (e) {
      if (!blade.alive) return;
      const p = jupyterProblem(e, nb && nb.path);
      status.classList.add("bad");
      say(icon("circle-alert", 14), h("span", {}, p.text, " Fix: "), h("code", { class: "mono" }, p.fix));
    }
    modeBtn.disabled = false;
  }
  modeBtn.addEventListener("click", runInJupyter);

  function notice(title, text, retry) {
    shell.replaceChildren(h("div", { class: "nb-notice" }, h("section", { class: "card" },
      h("div", { class: "empty" }, h("div", { class: "ring" }, icon(retry ? "triangle-alert" : "book-open", 20)), h("b", {}, title), text,
        retry ? h("div", {}, h("button", { class: "btn fill sm", type: "button", onClick: retry }, icon("rotate-cw", 13), "Try again")) : h("div", { class: "prog indet nb-wait" }, h("i"))),
      h("div", { class: "src" }, `mxc learn ${id}`))));
  }

  function draw() {
    const cells = nb.cells, chs = chapters(cells);
    const cellEls = cells.map(cellEl);
    const toc = h("nav", { class: "nb-toc", "aria-label": "Chapters" }, h("div", { class: "nb-lab" }, "Chapters"),
      chs.length ? chs.map((c, i) => h("button", { type: "button", class: `lv${c.level}`, onClick: (e) => {
        toc.querySelectorAll("button").forEach((b) => b.removeAttribute("aria-current"));
        e.currentTarget.setAttribute("aria-current", "true");
        cellEls[c.cell].scrollIntoView({ block: "start", behavior: reducedMotion() ? "auto" : "smooth" });
      } }, h("span", { class: "num mono" }, String(i + 1)), h("span", {}, c.title))) : h("p", { class: "nb-none" }, "No headings in this notebook."));
    const side = archPanel(nb);
    const twin = h("section", { class: "card" }, h("div", { class: "card-h" }, h("h3", {}, "File")),
      h("div", { class: "card-b" }, h("div", { class: "nb-path mono" }, nb.path)),
      h("div", { class: "src" }, `mxc learn ${id}`,
        h("button", { class: "cp", type: "button", "aria-label": "Copy command", onClick: async () => toast((await copyText(`mxc learn ${id}`)) ? "Copied" : "Could not reach the clipboard") }, "copy")));
    side.append(twin);
    saved.replaceChildren(h("div", { class: "nb-grid" }, toc, h("div", { class: "nb-main" }, h("div", { class: "nb-inner" }, cellEls)), side));
    shell.replaceChildren(status, saved, frameWrap);
    modeBtn.disabled = false;
  }

  async function load() {
    notice("Building the notebook", `Reading the model's config and card from Hugging Face the first time, then it is saved for next time.`);
    try {
      nb = await api("GET", `/admin/learn/${enc}`);
    } catch (e) {
      if (!blade.alive) return;
      notice("Could not build the notebook", e.message, load);
      return;
    }
    if (!blade.alive) return;
    setHead(blade, { sub: `${id} · ${nb.path}` });
    draw();
  }

  return {
    title: "Notebook", crumb: "Notebook", sub: id,
    icon: "book-open", hue: "var(--hue-notebook)", wide: true, min: 900,
    tools: [chipEl, modeBtn, tabLink],
    mount(body) {
      blade.el.classList.add("oq-flat");
      body.replaceChildren(shell);
      paintChip();
      blade.onClose = onData(paintChip);
      load();
    },
  };
});
