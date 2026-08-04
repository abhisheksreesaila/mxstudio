/* mxstudio frontend: tab routing + polling + streaming chat. */

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

const TAB_TITLES = {
  models: "Models",
  chat: "Chat",
  notebooks: "Notebooks",
  bench: "Benchmark",
  learn: "Learn",
};

let activeTab = "models";
let models = [];
let serverStatus = { state: "stopped" };
let chatMessages = [];
let chatBusy = false;
let lastActivityIds = new Set();
let lastBenchState = "idle";
let lastNbState = "idle";
let lastServeState = "stopped";

/* ---------- helpers ---------- */

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail;
      if (detail && typeof detail === "object") {
        detail = detail.message || JSON.stringify(detail);
      }
      detail = detail || res.statusText;
    } catch {}
    throw new Error(detail);
  }
  return res.json();
}

function fmtGB(bytes) {
  return bytes == null ? "?" : (bytes / 1e9).toFixed(2) + " GB";
}

function esc(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

function toast(title, body = "", kind = "warn") {
  const host = $("#toasts");
  if (!host) return;
  const el = document.createElement("div");
  el.className = "toast " + kind;
  el.innerHTML = `<div class="toast-title">${esc(title)}</div>` +
    (body ? `<div class="toast-body">${esc(body)}</div>` : "");
  host.appendChild(el);
  setTimeout(() => el.remove(), 5000);
}

function renderActivity(jobs) {
  const bar = $("#activity-bar");
  const list = $("#activity-list");
  if (!bar || !list) return;
  if (!jobs.length) {
    bar.hidden = true;
    list.innerHTML = "";
    return;
  }
  bar.hidden = false;
  list.innerHTML = jobs
    .map(
      (j) =>
        `<span class="activity-chip"><strong>${esc(j.label)}</strong>` +
        `<span class="detail">${esc(j.detail || "")}</span></span>`
    )
    .join("");
}

/* ---------- tabs ---------- */

$$(".nav-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    activeTab = btn.dataset.tab;
    $$(".nav-btn").forEach((b) => b.classList.toggle("active", b === btn));
    $$(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + activeTab));
    $("#tab-title").textContent = TAB_TITLES[activeTab];
    if (activeTab === "bench") loadHardware();
    if (activeTab === "notebooks") loadNotebooks();
  });
});

(function setupSidebarToggle() {
  const shell = document.querySelector(".shell");
  const btn = $("#sidebar-toggle");
  if (!shell || !btn) return;
  const KEY = "mxstudio.sidebarCollapsed";
  const apply = (collapsed) => {
    shell.classList.toggle("sidebar-collapsed", collapsed);
    btn.textContent = collapsed ? "»" : "«";
    btn.title = collapsed ? "Expand sidebar" : "Minimize sidebar";
    localStorage.setItem(KEY, collapsed ? "1" : "0");
  };
  apply(localStorage.getItem(KEY) === "1");
  btn.addEventListener("click", () => {
    apply(!shell.classList.contains("sidebar-collapsed"));
  });
})();

/* ---------- server status pill ---------- */

function renderServerStatus() {
  const s = serverStatus;
  const pill = $("#server-pill");
  pill.className = "pill " + s.state;
  const label = s.model_id ? `${s.model_id.split("/")[1] || s.model_id}: ${s.state}` : "server: stopped";
  pill.textContent = label + (s.state === "starting" ? ` (${s.uptime_s}s)` : "");
  const mini = $("#mini-status");
  mini.innerHTML = `<span class="dot ${s.state}"></span><span class="mini-text">${esc(label)}</span>`;
}

/* ---------- models tab ---------- */

const openDetails = new Set();
const loadedCards = new Set();
let lastHfReport = null;

async function loadEnvPill() {
  const el = $("#env-pill");
  if (!el) return;
  try {
    const env = await api("/api/env");
    const parts = [
      env.hf_token_set ? "HF_TOKEN ok" : "HF_TOKEN missing (ok for public models)",
      `cache ${env.hf_hub_cache}`,
      env.disk_free_gb != null ? `${env.disk_free_gb} GB free` : null,
      env.max_cli ? "max cli ok" : "max cli not on PATH",
    ].filter(Boolean);
    el.textContent = parts.join(" · ");
    el.className = "env-pill " + (env.hf_token_set ? "ok" : "warn");
  } catch (err) {
    el.textContent = "env check failed: " + err.message;
    el.className = "env-pill warn";
  }
}

function renderHfReport(report) {
  const box = $("#hf-report");
  const dlBtn = $("#hf-download");
  lastHfReport = report;
  if (!report) {
    box.hidden = true;
    dlBtn.disabled = true;
    return;
  }
  if (!report.ok) {
    box.hidden = false;
    box.innerHTML = `<div style="color:var(--err)">${esc(report.error || "check failed")}</div>
      <p class="tab-hint">${esc(report.hint || "")}</p>`;
    dlBtn.disabled = true;
    return;
  }
  const compat = report.modular_compatible
    ? '<span class="badge installed">modular ok</span>'
    : '<span class="badge exp">compat unknown</span>';
  const warns = (report.warnings || [])
    .map((w) => `<li>${esc(w)}</li>`)
    .join("");
  box.hidden = false;
  box.innerHTML = `
    <div class="row"><span class="k">Model</span><span>${esc(report.model_id)}</span> ${compat}</div>
    <div class="row"><span class="k">Architecture</span><span>${esc(report.architecture || "?")}</span></div>
    <div class="row"><span class="k">Size</span><span>~${report.approx_gb ?? "?"} GB · safetensors ${report.has_safetensors ? "yes" : "no"}</span></div>
    <div class="row"><span class="k">Gated</span><span>${report.gated ? "yes" : "no"} · token ${report.env?.hf_token_set ? "set" : "missing"}</span></div>
    <div class="row"><span class="k">Docs</span>
      <a href="${report.handbook}" target="_blank" rel="noopener">Handbook</a> ·
      <a href="${report.models_docs}" target="_blank" rel="noopener">MAX models</a>
    </div>
    ${warns ? `<ul class="warn-list">${warns}</ul>` : ""}
    <p class="tab-hint" style="margin:8px 0 0">Ready to download: <strong>${report.can_download ? "yes" : "no"}</strong>${
      report.modular_compatible ? "" : " — Download will ask to force if architecture is unknown."
    }</p>`;
  dlBtn.disabled = !(report.can_download || !report.gated);
}

$("#hf-check")?.addEventListener("click", async () => {
  const id = ($("#hf-id").value || "").trim();
  if (!id) {
    toast("Hugging Face id required", "Use org/name format", "warn");
    return;
  }
  try {
    $("#hf-check").disabled = true;
    const report = await api("/api/models/check", {
      method: "POST",
      body: JSON.stringify({ model_id: id }),
    });
    renderHfReport(report);
    if (report.ok) toast("Check complete", report.model_id, report.modular_compatible ? "ok" : "warn");
  } catch (err) {
    toast("Check failed", err.message, "err");
  } finally {
    $("#hf-check").disabled = false;
  }
});

$("#hf-download")?.addEventListener("click", async () => {
  const id = ($("#hf-id").value || "").trim();
  if (!id) return;
  try {
    $("#hf-download").disabled = true;
    let force = false;
    if (lastHfReport && !lastHfReport.modular_compatible) {
      force = confirm(
        `${id} may not be Modular-compatible.\nDownload anyway?`
      );
      if (!force) {
        $("#hf-download").disabled = false;
        return;
      }
    }
    await api("/api/models/download-hf", {
      method: "POST",
      body: JSON.stringify({ model_id: id, force }),
    });
    toast("Download started", id, "warn");
    pollModels();
    pollActivity();
  } catch (err) {
    // FastAPI 409 detail may be an object
    const msg = typeof err.message === "string" ? err.message : JSON.stringify(err.message);
    toast("Download blocked", msg, "err");
  } finally {
    $("#hf-download").disabled = false;
  }
});

function renderModels() {
  const list = $("#model-list");
  list.innerHTML = "";
  for (const m of models) {
    const card = document.createElement("div");
    card.className = "model-card";
    card.dataset.id = m.id;

    const badges = [];
    if (m.installed) badges.push('<span class="badge installed">installed</span>');
    if (m.experimental) badges.push('<span class="badge exp">experimental</span>');
    if (m.server_state) badges.push(`<span class="badge serving">${m.server_state}</span>`);

    const dl = m.download;
    const downloading = dl && dl.state === "downloading";

    let progressHtml = "";
    if (downloading) {
      const pct = dl.pct != null ? dl.pct : 0;
      progressHtml = `
        <div class="progress"><div style="width:${pct}%"></div></div>
        <div class="progress-label">${dl.pct != null ? dl.pct + "%" : ""} ${fmtGB(dl.done_bytes)} / ${fmtGB(dl.total_bytes)}</div>`;
    } else if (dl && dl.state === "error") {
      progressHtml = `<div class="progress-label" style="color:var(--err)">download failed: ${esc(dl.error || "")}</div>`;
    }

    const canServe = m.installed && m.server_state !== "running" && m.server_state !== "starting";
    const isServing = m.server_state === "running" || m.server_state === "starting";

    card.innerHTML = `
      <div class="model-head">
        <span class="model-name">${esc(m.name)}</span>
        <span class="model-id">${esc(m.id)}</span>
        ${badges.join(" ")}
      </div>
      <div class="model-meta">${esc(m.params)} · ~${m.approx_gb} GB · ${m.context.toLocaleString()} ctx · ${esc(m.license)}</div>
      <div class="model-desc">${esc(m.description)}</div>
      ${progressHtml}
      <div class="model-actions">
        ${!m.installed && !downloading ? `<button class="btn primary" data-act="download">Download</button>` : ""}
        ${downloading ? `<button class="btn" disabled>Downloading…</button>` : ""}
        ${canServe ? `<button class="btn primary" data-act="serve">Serve</button>` : ""}
        ${isServing ? `<button class="btn danger" data-act="stop">Stop</button>` : ""}
        ${m.installed && !isServing && !downloading ? `<button class="btn danger" data-act="uninstall">Uninstall</button>` : ""}
        <button class="btn" data-act="details">${openDetails.has(m.id) ? "Hide details" : "Details"}</button>
        <button class="btn" data-act="notebook">Notebook</button>
      </div>
      <div class="model-detail ${openDetails.has(m.id) ? "open" : ""}" id="detail-${cssId(m.id)}">
        <h4>What's the innovation?</h4>
        <p>${esc(m.innovation)}</p>
        <div class="links-row">
          ${Object.entries(m.links).map(([k, v]) => `<a href="${v}" target="_blank" rel="noopener">${esc(k)} ↗</a>`).join("")}
        </div>
        <h4>Model card</h4>
        <div class="card-md" id="card-${cssId(m.id)}">loading…</div>
      </div>`;

    card.addEventListener("click", (ev) => {
      const act = ev.target.dataset && ev.target.dataset.act;
      if (act) handleModelAction(act, m, ev.target);
    });
    list.appendChild(card);

    if (openDetails.has(m.id) && !loadedCards.has(m.id)) loadCard(m.id);
  }
}

function cssId(id) {
  return id.replace(/[^a-zA-Z0-9]/g, "_");
}

async function handleModelAction(act, m, btn) {
  try {
    if (act === "download") {
      btn.disabled = true;
      await api(`/api/models/${m.id}/download`, { method: "POST" });
      toast("Download started", `${m.name} is downloading in the background`, "warn");
      pollModels();
      pollActivity();
    } else if (act === "serve") {
      btn.disabled = true;
      await api("/api/server/start", { method: "POST", body: JSON.stringify({ model_id: m.id }) });
      toast("Starting model", `${m.name} — compile can take a few minutes; UI stays usable`, "warn");
      pollModels();
      pollActivity();
    } else if (act === "stop") {
      btn.disabled = true;
      await api("/api/server/stop", { method: "POST" });
      toast("Server stopped", m.name, "ok");
      pollModels();
      pollActivity();
    } else if (act === "uninstall") {
      const ok = confirm(
        `Uninstall ${m.name}?\n\nThis deletes the local Hugging Face cache for ${m.id} and frees disk space.`
      );
      if (!ok) return;
      btn.disabled = true;
      const res = await api(`/api/models/${m.id}`, { method: "DELETE" });
      const gb = res.freed_bytes != null ? (res.freed_bytes / 1e9).toFixed(2) : "?";
      toast("Uninstalled", `${m.name} · freed ~${gb} GB`, "ok");
      pollModels();
    } else if (act === "details") {
      if (openDetails.has(m.id)) openDetails.delete(m.id);
      else {
        openDetails.add(m.id);
        if (!loadedCards.has(m.id)) loadCard(m.id);
      }
      renderModels();
    } else if (act === "notebook") {
      btn.disabled = true;
      const res = await api(`/api/models/${m.id}/notebook`, { method: "POST" });
      btn.disabled = false;
      btn.textContent = "Notebook ✓";
      setTimeout(() => (btn.textContent = "Notebook"), 2500);
      console.log("notebook:", res.path);
    }
  } catch (err) {
    alert(err.message);
    pollModels();
  }
}

async function loadCard(modelId) {
  loadedCards.add(modelId);
  try {
    const res = await api(`/api/models/${modelId}/card`);
    const el = $(`#card-${cssId(modelId)}`);
    if (!el) return;
    const md = res.markdown || "(model card not available)";
    el.innerHTML = window.marked ? marked.parse(md) : `<pre>${esc(md)}</pre>`;
  } catch {
    const el = $(`#card-${cssId(modelId)}`);
    if (el) el.textContent = "(model card not available)";
  }
}

/* ---------- polling ---------- */

async function pollModels() {
  try {
    const [ms, ss] = await Promise.all([api("/api/models"), api("/api/server")]);
    models = ms;
    serverStatus = ss;
    renderServerStatus();
    if (activeTab === "models") renderModels();
  } catch {
    /* backend briefly unavailable; retry on next tick */
  }
}

async function pollActivity() {
  try {
    const act = await api("/api/activity");
    const jobs = act.jobs || [];
    renderActivity(jobs);

    // Toasts when background jobs start / finish
    const ids = new Set(jobs.map((j) => j.id));
    for (const j of jobs) {
      if (!lastActivityIds.has(j.id)) {
        toast("Background job started", `${j.label} — ${j.detail || "running"}`, "warn");
      }
    }
    lastActivityIds = ids;

    const serve = act.server || {};
    if (lastServeState === "starting" && serve.state === "running") {
      toast("Model ready", `${serve.model_id} is serving`, "ok");
    } else if (lastServeState === "starting" && serve.state === "crashed") {
      toast("Serve failed", "Check server logs in the Models tab", "err");
    }
    lastServeState = serve.state || "stopped";
    serverStatus = serve;
    renderServerStatus();

    const bench = act.bench || {};
    if (bench.state === "running" || lastBenchState === "running") {
      if (activeTab === "bench") renderBenchResult(bench);
    }
    if (lastBenchState === "running" && bench.state === "done") {
      toast(
        "Benchmark finished",
        `${bench.summary?.mean_tok_per_s ?? "?"} tok/s · TTFT ${bench.summary?.mean_ttft_s ?? "?"}s`,
        "ok"
      );
      const btn = $("#bench-btn");
      if (btn) btn.disabled = false;
      if (activeTab === "bench") {
        renderBenchResult(bench);
        renderBenchHistory();
        loadHardware();
      }
    } else if (lastBenchState === "running" && bench.state === "error") {
      toast("Benchmark failed", bench.error || "unknown error", "err");
      const btn = $("#bench-btn");
      if (btn) btn.disabled = false;
      if (activeTab === "bench") renderBenchResult(bench);
    }
    lastBenchState = bench.state || "idle";

    const nb = act.notebook || {};
    if (nb.state === "running" && openNotebook && nb.notebook === openNotebook) {
      applyNotebookJobProgress(nb);
    }
    if (lastNbState === "running" && nb.state === "done") {
      toast("Notebook finished", nb.notebook || "run complete", "ok");
      if (openNotebook && nb.notebook === openNotebook) {
        applyNotebookJobProgress(nb);
        finishNotebookJob(nb, true);
      }
      nbBusy = false;
      setKernelStatus("Python 3 | idle", false);
      $("#nb-run-all") && ($("#nb-run-all").disabled = false);
      $("#nb-run") && ($("#nb-run").disabled = false);
    } else if (lastNbState === "running" && nb.state === "error") {
      toast("Notebook failed", nb.error || "error", "err");
      nbBusy = false;
      setKernelStatus("Python 3 | error", false);
      $("#nb-run-all") && ($("#nb-run-all").disabled = false);
      $("#nb-run") && ($("#nb-run").disabled = false);
    }
    lastNbState = nb.state || "idle";
  } catch {
    /* ignore transient */
  }
}

setInterval(pollModels, 2500);
setInterval(pollActivity, 1500);
pollModels();
pollActivity();
loadEnvPill();

/* ---------- chat tab ---------- */

$("#temperature").addEventListener("input", (e) => ($("#temp-out").textContent = e.target.value));
$("#top_p").addEventListener("input", (e) => ($("#topp-out").textContent = e.target.value));

$("#chat-clear").addEventListener("click", () => {
  chatMessages = [];
  renderChat();
});

function renderChat() {
  const box = $("#chat-messages");
  if (chatMessages.length === 0) {
    box.innerHTML = '<div class="chat-empty">Serve a model from the Models tab, then say hello.</div>';
    return;
  }
  box.innerHTML = chatMessages
    .map((m) => `<div class="msg ${m.role}">${esc(m.content)}</div>`)
    .join("");
  box.scrollTop = box.scrollHeight;
}

function apiMessages() {
  // Only roles the OpenAI/MAX API accepts — never send UI-only "error" rows.
  return chatMessages
    .filter((m) => m.role === "user" || m.role === "assistant" || m.role === "system")
    .filter((m) => m.content); // drop the empty assistant placeholder
}

$("#chat-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  if (chatBusy) return;
  if (serverStatus.state !== "running") {
    chatMessages.push({
      role: "error",
      content: `Wait until the server pill shows "running" (currently: ${serverStatus.state}).`,
    });
    renderChat();
    return;
  }
  const input = $("#chat-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";

  // Drop previous UI error banners so they don't clutter the thread.
  chatMessages = chatMessages.filter((m) => m.role !== "error");
  chatMessages.push({ role: "user", content: text });
  chatMessages.push({ role: "assistant", content: "" });
  renderChat();
  chatBusy = true;

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        messages: apiMessages(),
        temperature: parseFloat($("#temperature").value),
        top_p: parseFloat($("#top_p").value),
        max_tokens: parseInt($("#max_tokens").value, 10),
      }),
    });
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch {}
      throw new Error(detail);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split("\n\n");
      buffer = events.pop();
      for (const evt of events) {
        if (!evt.startsWith("data: ")) continue;
        const data = JSON.parse(evt.slice(6));
        if (data.delta) {
          chatMessages[chatMessages.length - 1].content += data.delta;
          renderChat();
        }
        if (data.error) throw new Error(data.error);
      }
    }
  } catch (err) {
    chatMessages.pop(); // remove empty/partial assistant turn
    chatMessages.push({ role: "error", content: err.message });
    renderChat();
  } finally {
    chatBusy = false;
  }
});

/* ---------- notebooks tab — Jupyter-style in-app editor ---------- */

let openNotebook = null;
let nbBusy = false;
let nbCells = []; // {index, cell_type, source, exec?}
let nbSelected = 0;
let nbMode = "command"; // command | edit
let nbExecCounter = 0;

function setKernelStatus(text, busy) {
  const el = $("#nb-kernel-status");
  if (!el) return;
  el.textContent = text;
  el.classList.toggle("busy", !!busy);
}

async function loadNotebooks() {
  const select = $("#nb-model");
  if (models.length) {
    select.innerHTML = models
      .map((m) => `<option value="${m.id}">${esc(m.name)}</option>`)
      .join("");
  }
  try {
    const nbs = await api("/api/notebooks");
    const list = $("#nb-list");
    if (!nbs.length) {
      list.innerHTML = '<p class="tab-hint">No notebooks yet — generate one above.</p>';
      return;
    }
    list.innerHTML = nbs
      .map(
        (nb) => `
      <div class="nb-item">
        <div>
          <div>${esc(nb.name)}</div>
          <div class="path">${esc(nb.path)}</div>
        </div>
        <button class="btn primary" data-open="${esc(nb.name)}">Open</button>
      </div>`
      )
      .join("");
    list.querySelectorAll("[data-open]").forEach((btn) => {
      btn.addEventListener("click", () => openNotebookViewer(btn.dataset.open));
    });
  } catch {}
}

$("#nb-btn").addEventListener("click", async () => {
  const id = $("#nb-model").value;
  if (!id) return;
  try {
    const res = await api(`/api/models/${id}/notebook`, { method: "POST" });
    await loadNotebooks();
    await openNotebookViewer(res.name);
  } catch (err) {
    alert(err.message);
  }
});

$("#nb-back").addEventListener("click", () => {
  $("#nb-viewer").hidden = true;
  $("#nb-browser").hidden = false;
  openNotebook = null;
});

$("#nb-run").addEventListener("click", () => runSelectedCell());
$("#nb-run-all").addEventListener("click", () => runAllCells());
$("#nb-save").addEventListener("click", () => saveNotebook());
$("#nb-add-code").addEventListener("click", () => insertCell("code"));
$("#nb-add-md").addEventListener("click", () => insertCell("markdown"));
$("#nb-reset").addEventListener("click", async () => {
  if (!openNotebook) return;
  try {
    await api(`/api/notebooks/${openNotebook}/reset`, { method: "POST" });
    nbExecCounter = 0;
    nbCells.forEach((c) => { c.exec = null; c.output = null; });
    renderJpNotebook();
    setKernelStatus("Python 3 | restarted", false);
  } catch (err) {
    alert(err.message);
  }
});

function syncSourcesFromDom() {
  $$(".jp-cell").forEach((el) => {
    const i = parseInt(el.dataset.index, 10);
    const cell = nbCells[i];
    if (!cell) return;
    if (cell.cell_type === "code") {
      const ta = el.querySelector(".jp-code");
      if (ta) cell.source = ta.value;
    } else {
      const ta = el.querySelector(".jp-md-edit");
      if (ta) cell.source = ta.value;
    }
  });
}

function collectCells() {
  syncSourcesFromDom();
  return nbCells.map((c, i) => ({
    index: i,
    cell_type: c.cell_type,
    source: c.source,
  }));
}

async function openNotebookViewer(name) {
  try {
    openNotebook = name;
    const nb = await api(`/api/notebooks/${name}`);
    $("#nb-title").textContent = name;
    $("#nb-browser").hidden = true;
    $("#nb-viewer").hidden = false;
    nbCells = nb.cells.map((c) => ({
      ...c,
      exec: null,
      output: null,
    }));
    nbSelected = 0;
    nbMode = "command";
    nbExecCounter = 0;
    renderJpNotebook();
    setKernelStatus("Python 3 | idle", false);
    $("#nb-cells").focus();
  } catch (err) {
    alert(err.message);
  }
}

function renderJpNotebook() {
  const box = $("#nb-cells");
  const prevScroll = box.scrollTop;
  box.innerHTML = "";
  nbCells.forEach((cell, i) => {
    const block = document.createElement("div");
    block.className = "jp-block";

    const wrap = document.createElement("div");
    wrap.className =
      "jp-cell " +
      (cell.cell_type === "code" ? "code" : "md") +
      (i === nbSelected ? " selected" : "") +
      (i === nbSelected && nbMode === "edit" ? " edit" : "");
    wrap.dataset.index = i;

    if (cell.cell_type === "code") {
      const prompt = cell.exec != null ? `In [${cell.exec}]:` : "In [ ]:";
      wrap.innerHTML = `
        <div class="jp-prompt in">${prompt}</div>
        <div class="jp-input-area">
          <textarea class="jp-code" spellcheck="false"></textarea>
        </div>`;
      wrap.querySelector(".jp-code").value = cell.source;
    } else {
      const rendered = window.marked
        ? marked.parse(cell.source || "")
        : `<pre>${esc(cell.source)}</pre>`;
      wrap.innerHTML = `
        <div class="jp-prompt"></div>
        <div class="jp-input-area">
          <div class="jp-md-render">${rendered}</div>
          <textarea class="jp-md-edit" spellcheck="false"></textarea>
        </div>`;
      wrap.querySelector(".jp-md-edit").value = cell.source;
    }

    wrap.addEventListener("click", (ev) => {
      if (ev.target.closest("textarea")) return;
      selectCell(i, "command");
    });
    wrap.addEventListener("dblclick", () => selectCell(i, "edit"));
    block.appendChild(wrap);

    if (cell.cell_type === "code" && cell.output) {
      const outPrompt = cell.exec != null ? `Out[${cell.exec}]:` : "";
      const outRow = document.createElement("div");
      outRow.className =
        "jp-output visible" +
        (cell.output.running ? " running" : cell.output.ok === false ? " err" : "");
      outRow.innerHTML = `
        <div class="jp-prompt out">${outPrompt}</div>
        <div class="jp-output-body"></div>`;
      outRow.querySelector(".jp-output-body").textContent = cell.output.text || "";
      block.appendChild(outRow);
    }

    box.appendChild(block);

    const ta = wrap.querySelector("textarea");
    if (ta) {
      autoGrow(ta);
      ta.addEventListener("input", () => {
        cell.source = ta.value;
        autoGrow(ta);
      });
      ta.addEventListener("focus", () => {
        nbSelected = i;
        nbMode = "edit";
        $$(".jp-cell").forEach((el) => {
          const idx = parseInt(el.dataset.index, 10);
          el.classList.toggle("selected", idx === i);
          el.classList.toggle("edit", idx === i);
        });
      });
      ta.addEventListener("keydown", (ev) => {
        if (ev.key === "Escape") {
          ev.preventDefault();
          ta.blur();
          selectCell(i, "command");
          $("#nb-cells").focus();
        }
        if (ev.key === "Enter" && ev.shiftKey) {
          ev.preventDefault();
          nbSelected = i;
          runSelectedCell(true);
        }
        if (ev.key === "Tab") {
          ev.preventDefault();
          const start = ta.selectionStart;
          ta.value =
            ta.value.slice(0, start) + "    " + ta.value.slice(ta.selectionEnd);
          ta.selectionStart = ta.selectionEnd = start + 4;
          cell.source = ta.value;
        }
      });
    }

    const mdRender = wrap.querySelector(".jp-md-render");
    if (mdRender) {
      mdRender.addEventListener("dblclick", (ev) => {
        ev.stopPropagation();
        selectCell(i, "edit");
        wrap.querySelector(".jp-md-edit")?.focus();
      });
    }
  });
  box.scrollTop = prevScroll;
}

function autoGrow(ta) {
  ta.style.height = "auto";
  ta.style.height = Math.max(48, ta.scrollHeight) + "px";
}

function selectCell(i, mode) {
  // leave edit on previous markdown: re-render
  if (nbMode === "edit" && nbCells[nbSelected]?.cell_type === "markdown") {
    syncSourcesFromDom();
  }
  nbSelected = Math.max(0, Math.min(i, nbCells.length - 1));
  nbMode = mode;
  renderJpNotebook();
  if (mode === "edit") {
    const cellEl = $(`.jp-cell[data-index="${nbSelected}"]`);
    const ta = cellEl?.querySelector("textarea");
    if (ta) {
      ta.focus();
      autoGrow(ta);
    }
  } else {
    $("#nb-cells").focus();
  }
}

function insertCell(type) {
  syncSourcesFromDom();
  const idx = nbSelected + 1;
  nbCells.splice(idx, 0, {
    index: idx,
    cell_type: type,
    source: type === "code" ? "" : "### New cell\n",
    exec: null,
    output: null,
  });
  nbCells.forEach((c, i) => (c.index = i));
  selectCell(idx, "edit");
}

async function saveNotebook() {
  if (!openNotebook) return;
  try {
    await api(`/api/notebooks/${openNotebook}`, {
      method: "PUT",
      body: JSON.stringify({ cells: collectCells() }),
    });
    setKernelStatus("Python 3 | saved", false);
  } catch (err) {
    alert(err.message);
  }
}

function showCellOutput(index, result) {
  const cell = nbCells[index];
  if (!cell || cell.cell_type !== "code") return;
  if (result.skipped) return;
  if (result.ok && cell.exec == null) {
    nbExecCounter += 1;
    cell.exec = nbExecCounter;
  }
  const text = [result.stdout, result.stderr].filter(Boolean).join("") ||
    (result.ok ? "" : "(failed)");
  cell.output = { ok: result.ok, text, running: false };
}

let nbAdvanceAfter = false;

function applyNotebookJobProgress(job) {
  const results = job.results || [];
  const seen = new Set(results.map((r) => r.index));
  for (const r of results) showCellOutput(r.index, r);
  const cur = job.current_cell;
  if (cur != null && nbCells[cur] && nbCells[cur].cell_type === "code" && !seen.has(cur)) {
    nbCells[cur].output = { ok: true, text: "Running in background…", running: true };
  }
  renderJpNotebook();
  setKernelStatus(
    job.state === "running" ? "Python 3 | busy (background)" : "Python 3 | idle",
    job.state === "running"
  );
}

function finishNotebookJob(job, advance) {
  const results = job.results || [];
  nbCells.forEach((c) => {
    if (c.cell_type === "code" && c.output?.running) c.output = null;
  });
  for (const r of results) showCellOutput(r.index, r);
  renderJpNotebook();
  if (advance && nbAdvanceAfter && results.length) {
    const last = results[results.length - 1].index;
    selectCell(Math.min(last + 1, nbCells.length - 1), "command");
  }
  nbAdvanceAfter = false;
}

async function runSelectedCell(advance = true) {
  if (!openNotebook || nbBusy) return;
  syncSourcesFromDom();
  const cell = nbCells[nbSelected];
  if (!cell) return;

  if (cell.cell_type === "markdown") {
    selectCell(nbSelected, "command");
    if (advance) {
      selectCell(Math.min(nbSelected + 1, nbCells.length - 1), "command");
    }
    return;
  }

  if (serverStatus.state !== "running") {
    toast("Model not ready", 'Serve a model first (pill should say "running")', "err");
    return;
  }

  nbBusy = true;
  nbAdvanceAfter = advance;
  setKernelStatus("Python 3 | busy (background)", true);
  cell.output = { ok: true, text: "Running in background…", running: true };
  renderJpNotebook();
  $("#nb-run-all").disabled = true;
  $("#nb-run").disabled = true;

  try {
    await api(`/api/notebooks/${openNotebook}/run`, {
      method: "POST",
      body: JSON.stringify({
        cell_index: nbSelected,
        code: cell.source,
      }),
    });
    toast("Cell running", `Cell ${nbSelected} — UI stays usable`, "warn");
    pollActivity();
  } catch (err) {
    cell.output = { ok: false, text: err.message, running: false };
    renderJpNotebook();
    nbBusy = false;
    setKernelStatus("Python 3 | idle", false);
    $("#nb-run-all").disabled = false;
    $("#nb-run").disabled = false;
    toast("Could not start cell", err.message, "err");
  }
}

async function runAllCells() {
  if (!openNotebook || nbBusy) return;
  if (serverStatus.state !== "running") {
    toast("Model not ready", 'Serve a model first (pill should say "running")', "err");
    return;
  }
  nbBusy = true;
  nbAdvanceAfter = false;
  setKernelStatus("Python 3 | busy (background)", true);
  nbExecCounter = 0;
  nbCells.forEach((c) => {
    if (c.cell_type === "code") {
      c.exec = null;
      c.output = { ok: true, text: "queued (background)…", running: true };
    }
  });
  renderJpNotebook();
  $("#nb-run-all").disabled = true;
  $("#nb-run").disabled = true;

  try {
    await api(`/api/notebooks/${openNotebook}/run`, {
      method: "POST",
      body: JSON.stringify({
        cell_index: null,
        cells: collectCells(),
      }),
    });
    toast("Notebook running", "All cells — switch tabs freely; watch the activity bar", "warn");
    pollActivity();
  } catch (err) {
    nbBusy = false;
    setKernelStatus("Python 3 | idle", false);
    $("#nb-run-all").disabled = false;
    $("#nb-run").disabled = false;
    toast("Could not start notebook", err.message, "err");
  }
}

// Notebook keyboard shortcuts (command mode)
$("#nb-cells").addEventListener("keydown", (ev) => {
  if (!openNotebook || nbMode === "edit") return;
  if (ev.target.tagName === "TEXTAREA") return;
  const key = ev.key;
  if (key === "Enter" && !ev.shiftKey) {
    ev.preventDefault();
    selectCell(nbSelected, "edit");
  } else if (key === "Enter" && ev.shiftKey) {
    ev.preventDefault();
    runSelectedCell(true);
  } else if (key === "ArrowDown" || key === "j") {
    ev.preventDefault();
    selectCell(Math.min(nbSelected + 1, nbCells.length - 1), "command");
  } else if (key === "ArrowUp" || key === "k") {
    ev.preventDefault();
    selectCell(Math.max(nbSelected - 1, 0), "command");
  } else if (key === "a") {
    ev.preventDefault();
    syncSourcesFromDom();
    nbCells.splice(nbSelected, 0, {
      index: nbSelected,
      cell_type: "code",
      source: "",
      exec: null,
      output: null,
    });
    nbCells.forEach((c, i) => (c.index = i));
    selectCell(nbSelected, "edit");
  } else if (key === "b") {
    ev.preventDefault();
    insertCell("code");
  } else if (key === "dd" || (key === "d" && ev.repeat)) {
    // single d handled below with simple delete on Delete/Backspace with confirm skip — use Delete key
  } else if (key === "Delete" || key === "Backspace") {
    if (nbCells.length <= 1) return;
    ev.preventDefault();
    syncSourcesFromDom();
    nbCells.splice(nbSelected, 1);
    nbCells.forEach((c, i) => (c.index = i));
    selectCell(Math.min(nbSelected, nbCells.length - 1), "command");
  } else if (key === "m") {
    ev.preventDefault();
    nbCells[nbSelected].cell_type = "markdown";
    renderJpNotebook();
  } else if (key === "y") {
    ev.preventDefault();
    nbCells[nbSelected].cell_type = "code";
    renderJpNotebook();
  }
});

/* ---------- benchmark tab ---------- */

async function loadHardware() {
  try {
    const hw = await api("/api/hardware");
    const cards = [];
    for (const gpu of hw.gpus) {
      cards.push(`
        <div class="hw-card">
          <div class="k">GPU</div>
          <div class="v">${esc(gpu.name)}</div>
          <div class="sub">${(gpu.vram_used_mb / 1024).toFixed(1)} / ${(gpu.vram_total_mb / 1024).toFixed(0)} GB VRAM · ${gpu.util_pct}% util · ${gpu.temp_c}°C</div>
        </div>`);
    }
    cards.push(`
      <div class="hw-card">
        <div class="k">CPU</div>
        <div class="v">${esc(hw.cpu.model || "unknown")}</div>
        <div class="sub">${hw.cpu.cores} threads</div>
      </div>
      <div class="hw-card">
        <div class="k">RAM</div>
        <div class="v">${hw.memory.total_gb} GB</div>
        <div class="sub">${hw.memory.available_gb} GB available</div>
      </div>
      <div class="hw-card">
        <div class="k">Disk (home)</div>
        <div class="v">${hw.disk.free_gb} GB free</div>
        <div class="sub">of ${hw.disk.total_gb} GB · ${hw.wsl ? "WSL2" : esc(hw.os)}</div>
      </div>`);
    $("#hw-cards").innerHTML = cards.join("");
    renderBenchHistory();
  } catch {}
}

function renderBenchResult(job) {
  const el = $("#bench-result");
  if (job.state === "idle") { el.innerHTML = ""; return; }
  if (job.state === "error") {
    el.innerHTML = `<p style="color:var(--err)">Benchmark failed: ${esc(job.error || "")}</p>`;
    return;
  }
  const rows = (job.runs || [])
    .map(
      (r, i) => `<tr><td>${i + 1}</td><td>${r.ttft_s}s</td><td>${r.tokens}</td><td>${r.tok_per_s}</td></tr>`
    )
    .join("");
  let summary = "";
  if (job.state === "running") {
    summary = `<p class="bench-summary">Running Modular <code>max benchmark</code>… backend ${esc(job.backend || "max_benchmark")} · prompts ${job.num_prompts ?? "?"}</p>`;
  } else if (job.summary) {
    const backend = job.summary.backend || job.backend || "";
    summary = `<p class="bench-summary">Mean: <strong>${job.summary.mean_tok_per_s ?? "?"} tok/s</strong> · TTFT <strong>${job.summary.mean_ttft_s ?? "?"}s</strong> on ${esc(job.model_id)} <span class="badge">${esc(backend)}</span></p>`;
  }
  el.innerHTML = `
    ${summary}
    <table class="bench-table">
      <tr><th>Run</th><th>TTFT</th><th>Tokens</th><th>tok/s</th></tr>
      ${rows}
    </table>`;
}

async function renderBenchHistory() {
  try {
    const history = await api("/api/bench/history");
    $("#bench-history").innerHTML = history.length
      ? `<table class="bench-table">
          <tr><th>When</th><th>Model</th><th>tok/s</th><th>TTFT</th><th>GPU</th></tr>
          ${history
            .slice()
            .reverse()
            .map(
              (h) => `<tr><td>${esc(h.ts)}</td><td>${esc(h.model_id)}</td><td>${h.summary.mean_tok_per_s}</td><td>${h.summary.mean_ttft_s}s</td><td>${esc(h.gpu)}</td></tr>`
            )
            .join("")}
        </table>`
      : '<p class="tab-hint">No benchmarks yet.</p>';
  } catch {}
}

$("#bench-btn").addEventListener("click", async () => {
  const btn = $("#bench-btn");
  try {
    btn.disabled = true;
    const num_prompts = parseInt($("#bench-prompts").value, 10);
    const max_concurrency = parseInt($("#bench-conc").value, 10);
    const max_tokens = parseInt($("#bench-tokens").value, 10);
    await api("/api/bench", {
      method: "POST",
      body: JSON.stringify({
        runs: 3,
        num_prompts,
        max_concurrency,
        max_tokens,
        use_max_benchmark: true,
      }),
    });
    toast(
      "max benchmark started",
      "Modular CLI running in the background — you can leave this tab",
      "warn"
    );
    renderBenchResult({
      state: "running",
      runs: [],
      runs_total: 3,
      num_prompts,
      backend: "max_benchmark",
      model_id: serverStatus.model_id,
    });
    pollActivity();
  } catch (err) {
    btn.disabled = false;
    toast("Benchmark could not start", err.message, "err");
  }
});
