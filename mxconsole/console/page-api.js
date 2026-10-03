// API reference: base URLs, auth, endpoints, request fields, response examples, a code panel with real model
// names, and "Try it", which sends one chat request through an admin route so no app key reaches the browser.
import { h, icon, api, store, fmt, copyButton, ADMIN } from "./core.js";

const LANGS = ["Python", "JavaScript", "curl"];
const KW = { Python: ["import", "from", "for", "in", "print", "True", "False", "None"], JavaScript: ["import", "from", "const", "await", "for", "of", "new"], curl: ["curl"], json: [] };
const TOKEN = /("(?:[^"\\]|\\.)*"\s*:)|("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')|(#.*$|\/\/.*$)|(\b\d+(?:\.\d+)?\b)|([A-Za-z_][\w.]*)(?=\()|([A-Za-z_]\w*)|(\s+|.)/g;
const STATUS_TONE = { 200: "var(--ok)", 401: "var(--bad)", 404: "var(--warn)", 503: "var(--bad)" };

/** A small highlighter: strings, numbers, keywords, comments, JSON keys, calls. Returns one element per line. */
export function highlight(code, lang) {
  return code.split("\n").map((line) => {
    const spans = [];
    TOKEN.lastIndex = 0;
    let m;
    while ((m = TOKEN.exec(line))) {
      const [text, key, str, com, num, fn, word] = m;
      const cls = key ? "tk-key" : str ? "tk-str" : com ? "tk-com" : num ? "tk-num" : fn ? "tk-fn" : word && (KW[lang] || []).includes(word) ? "tk-kw" : "tk-txt";
      spans.push(h("span", { class: cls }, text));
    }
    return h("div", { class: "l" }, spans.length ? spans : " ");
  });
}

const ENDPOINTS = [
  { group: "Models", method: "GET", path: "/v1/models", desc: "Lists every registered model with its live state (ready, starting, cold) in an `mst` field. A cold model costs nothing until its first request.", fields: [], statuses: [200, 401] },
  { group: "Inference", method: "POST", path: "/v1/chat/completions", desc: "Chat with a model. A cold model loads on this request (the response waits for it); set stream to true for server-sent events.",
    fields: [["model", "string", true, "A registered MX Console name, for example qwen2.5-0.5b."], ["messages", "array", true, "Objects with role (system, user, assistant) and content."],
      ["stream", "boolean", false, "Send tokens as server-sent events. Default false."], ["max_tokens", "integer", false, "Upper bound on generated tokens."], ["temperature", "number", false, "0 to 2. Default 1."]], statuses: [200, 401, 404, 503] },
  { group: "Inference", method: "POST", path: "/v1/completions", desc: "Plain text completion, for base models.",
    fields: [["model", "string", true, "A registered MX Console name."], ["prompt", "string", true, "The text to continue."], ["stream", "boolean", false, "Server-sent events when true."], ["max_tokens", "integer", false, "Upper bound on generated tokens."]], statuses: [200, 401, 404, 503] },
  { group: "Inference", method: "POST", path: "/v1/embeddings", desc: "Vectors from an embedding model such as all-minilm-l6.",
    fields: [["model", "string", true, "An embedding model."], ["input", "string | array", true, "Text or a list of texts."]], statuses: [200, 401, 404, 503] },
  { group: "Service", method: "GET", path: "/health", desc: "Liveness of the daemon. No key needed.", fields: [], statuses: [200] },
];

const isEmbed = (n) => /embed|minilm|bge|e5-/i.test(n);

function responses(ep, model) {
  const err = (message, type, code) => `{\n  "error": {\n    "message": "${message}",\n    "type": "${type}",\n    "code": "${code}"\n  }\n}`;
  const ok = {
    "/v1/models": `{\n  "object": "list",\n  "data": [{\n    "id": "${model}",\n    "object": "model",\n    "owned_by": "mst",\n    "mst": {"state": "cold", "pinned": false, "keep_alive": 300}\n  }]\n}`,
    "/v1/chat/completions": `{\n  "id": "chatcmpl-7f3a",\n  "model": "${model}",\n  "choices": [{\n    "index": 0,\n    "message": {"role": "assistant", "content": "Hello there!"},\n    "finish_reason": "stop"\n  }],\n  "usage": {"prompt_tokens": 9, "completion_tokens": 3}\n}`,
    "/v1/completions": `{\n  "model": "${model}",\n  "choices": [{"index": 0, "text": " the quick brown fox", "finish_reason": "length"}],\n  "usage": {"prompt_tokens": 4, "completion_tokens": 16}\n}`,
    "/v1/embeddings": `{\n  "model": "${model}",\n  "data": [{"index": 0, "embedding": [0.0123, -0.0456, 0.0789]}],\n  "usage": {"prompt_tokens": 5}\n}`,
    "/health": `{"status": "ok", "service": "mst"}`,
  };
  return {
    200: ok[ep.path],
    401: err("Incorrect API key provided. Create one with `mxc keys new <app>`.", "authentication_error", "invalid_api_key"),
    404: err("The model `nope` is not registered. Run `mxc pull nope`.", "invalid_request_error", "model_not_found"),
    503: err(`Model \`${model}\` failed to start: …log tail…`, "server_error", "model_unavailable"),
  };
}

function examples(ep, model, base, origin) {
  const key = 'os.environ["MXC_API_KEY"]';
  if (ep.path === "/health") {
    return { Python: `import httpx\n\nprint(httpx.get("${origin}/health").json())`, JavaScript: `const res = await fetch("${origin}/health");\nconsole.log(await res.json());`, curl: `curl ${origin}/health` };
  }
  if (ep.path === "/v1/models") {
    return {
      Python: `import os\nfrom openai import OpenAI\n\nclient = OpenAI(base_url="${base}", api_key=${key})\nfor m in client.models.list():\n    print(m.id)`,
      JavaScript: `import OpenAI from "openai";\n\nconst client = new OpenAI({ baseURL: "${base}", apiKey: process.env.MXC_API_KEY });\nfor await (const m of client.models.list()) console.log(m.id);`,
      curl: `curl ${origin}/v1/models \\\n  -H "Authorization: Bearer $MXC_API_KEY"`,
    };
  }
  if (ep.path === "/v1/embeddings") {
    return {
      Python: `import os\nfrom openai import OpenAI\n\nclient = OpenAI(base_url="${base}", api_key=${key})\nres = client.embeddings.create(model="${model}", input="Hello")\nprint(len(res.data[0].embedding))`,
      JavaScript: `import OpenAI from "openai";\n\nconst client = new OpenAI({ baseURL: "${base}", apiKey: process.env.MXC_API_KEY });\nconst res = await client.embeddings.create({ model: "${model}", input: "Hello" });\nconsole.log(res.data[0].embedding.length);`,
      curl: `curl ${origin}${ep.path} \\\n  -H "Authorization: Bearer $MXC_API_KEY" \\\n  -H "Content-Type: application/json" \\\n  -d '{"model": "${model}", "input": "Hello"}'`,
    };
  }
  if (ep.path === "/v1/completions") {
    return {
      Python: `import os\nfrom openai import OpenAI\n\nclient = OpenAI(base_url="${base}", api_key=${key})\nres = client.completions.create(model="${model}", prompt="Once upon a time", max_tokens=16)\nprint(res.choices[0].text)`,
      JavaScript: `import OpenAI from "openai";\n\nconst client = new OpenAI({ baseURL: "${base}", apiKey: process.env.MXC_API_KEY });\nconst res = await client.completions.create({ model: "${model}", prompt: "Once upon a time", max_tokens: 16 });\nconsole.log(res.choices[0].text);`,
      curl: `curl ${origin}${ep.path} \\\n  -H "Authorization: Bearer $MXC_API_KEY" \\\n  -H "Content-Type: application/json" \\\n  -d '{"model": "${model}", "prompt": "Once upon a time", "max_tokens": 16}'`,
    };
  }
  return {
    Python: `import os\nfrom openai import OpenAI\n\nclient = OpenAI(\n    base_url="${base}",\n    api_key=${key},\n)\nstream = client.chat.completions.create(\n    model="${model}",\n    messages=[{"role": "user", "content": "Hello"}],\n    stream=True,\n)\nfor chunk in stream:  # tokens as they arrive\n    print(chunk.choices[0].delta.content or "", end="")`,
    JavaScript: `import OpenAI from "openai";\n\nconst client = new OpenAI({\n  baseURL: "${base}",\n  apiKey: process.env.MXC_API_KEY,\n});\nconst stream = await client.chat.completions.create({\n  model: "${model}",\n  messages: [{ role: "user", content: "Hello" }],\n  stream: true,\n});\nfor await (const chunk of stream) // tokens as they arrive\n  process.stdout.write(chunk.choices[0]?.delta?.content ?? "");`,
    curl: `curl ${origin}${ep.path} \\\n  -H "Authorization: Bearer $MXC_API_KEY" \\\n  -H "Content-Type: application/json" \\\n  -d '{\n    "model": "${model}",\n    "messages": [{"role": "user", "content": "Hello"}]\n  }'`,
  };
}

export function apiPage(ctx) {
  ctx.actions.append(h("a", { class: "btn", href: "/openapi.json", target: "_blank", rel: "noopener" }, icon("download", 15), "OpenAPI JSON"),
    h("a", { class: "btn primary", href: "#keys" }, icon("key-round", 15), "Create key"));
  const st = { ep: 1, lang: "Python", status: 200 };
  const host = h("div", {});
  ctx.body.replaceChildren(host);
  const tryState = { text: "Say hello in five words.", max: "128", stream: true, model: null };
  let tryPanel = null;

  const names = () => (store.models || []).map((m) => m.name);
  const chatModel = () => names().find((n) => !isEmbed(n)) || names()[0] || "<model>";
  const modelFor = (ep) => (ep.path === "/v1/embeddings" ? names().find(isEmbed) || names()[0] || "<model>" : chatModel());

  function urlRow(url, tag, tagCls) {
    return h("div", { class: "url-row" }, h("span", { class: "u mono" }, url), h("span", { class: `pill ${tagCls || ""}` }, tag), copyButton(() => url, "Copy"));
  }

  function draw() {
    const info = store.info || {};
    const base = info.endpoint || `${location.origin}/v1`, origin = base.replace(/\/v1$/, "");
    const ep = ENDPOINTS[st.ep], model = modelFor(ep);
    const live = (store.models || []).length;
    const top = h("section", { class: "panel api-top" },
      h("div", {},
        h("div", { class: "pills" }, h("span", { class: "pill ok" }, icon("shield-check", 12, "currentColor", 2), "OpenAI-compatible"), h("span", { class: "pill" }, "v1"),
          h("span", { class: "pill info" }, `${live} model${live === 1 ? "" : "s"} registered`)),
        h("p", { class: "lead" }, "Point any OpenAI SDK at the base URL and send an app key. Models load on their first request and unload when idle; the model field is the MX Console name you see on the Models page."),
        h("div", { class: "k" }, "Base URL"),
        urlRow(base, "this machine"),
        info.lan_endpoint ? urlRow(info.lan_endpoint, "LAN · HTTPS", "info")
          : h("div", { class: "url-row" }, h("span", { class: "u mono muted" }, "LAN: not exposed"), h("a", { class: "btn", href: "#network" }, "Expose")),
      ),
      h("div", { class: "side" },
        h("div", { style: "font-size:14px;font-weight:600;margin-bottom:10px" }, "Authentication"),
        h("div", { class: "authline mono" }, "Authorization: Bearer ", h("b", {}, "$MXC_API_KEY")),
        h("div", { class: "k", style: "line-height:1.5;margin-bottom:12px" }, "One key per app, shown once, revocable. Create one on the API keys page or with ", h("span", { class: "mono" }, "mxc keys new my-app"), "."),
        h("div", { style: "font-size:14px;font-weight:600;margin-bottom:8px" }, "Quickstart"),
        h("div", { style: "display:flex;gap:8px;flex-wrap:wrap" }, LANGS.map((l) => h("button", { class: "btn", type: "button", "aria-pressed": l === st.lang ? "true" : "false",
          style: l === st.lang ? { borderColor: "#3e63dd", color: "#3e63dd" } : {}, onClick: () => { st.lang = l; draw(); } }, l)))));

    const groups = [...new Set(ENDPOINTS.map((e) => e.group))];
    const list = h("section", { class: "panel ep-list", "aria-label": "Endpoints" }, groups.map((g) => [h("div", { class: "ep-group" }, g),
      ENDPOINTS.map((e, i) => (e.group === g ? h("button", { class: "ep-item", type: "button", "aria-current": i === st.ep ? "true" : "false", onClick: () => { st.ep = i; if (!ep.statuses.includes(st.status)) st.status = 200; draw(); } },
        h("span", { class: `method ${e.method}` }, e.method), h("span", { class: "mono", style: "font-size:12.5px" }, e.path.replace("/v1", ""))) : null))]));

    const resp = responses(ep, model);
    if (!ep.statuses.includes(st.status)) st.status = 200;
    const detail = h("section", { class: "panel ep-detail", "aria-label": "Endpoint" },
      h("div", { class: "head" }, h("span", { class: `method big ${ep.method}` }, ep.method), h("h2", { class: "mono" }, ep.path)),
      h("p", { class: "desc" }, ep.desc),
      h("h3", {}, "Request body"),
      h("div", { class: "fields" }, ep.fields.length ? ep.fields.map(([n, t, req, note]) => h("div", { class: "field-row" },
        h("div", { class: "fh" }, h("span", { class: "fn mono" }, n), h("span", { class: "ft mono" }, t), req ? h("span", { class: "pill bad" }, "required") : null), h("div", { class: "fd" }, note)))
        : [h("div", { class: "field-row" }, h("div", { class: "fd" }, "This endpoint takes no body."))]),
      h("h3", {}, "Responses"),
      h("div", { class: "status-tabs" }, ep.statuses.map((c) => h("button", { type: "button", "aria-pressed": c === st.status ? "true" : "false", style: { "--c": STATUS_TONE[c] }, onClick: () => { st.status = c; draw(); } }, String(c)))),
      h("div", { class: "codeblock mono" }, highlight(resp[st.status], "json")));

    const code = examples(ep, model, base, origin)[st.lang];
    const panel = h("section", { class: "codepanel", "aria-label": "Example" },
      h("div", { class: "code-tabs" }, LANGS.map((l) => h("button", { class: "t", type: "button", "aria-pressed": l === st.lang ? "true" : "false", onClick: () => { st.lang = l; draw(); } }, l)),
        copyButton(() => code, "Copy")),
      h("div", { class: "body mono" }, highlight(code, st.lang)));

    if (!tryPanel) tryPanel = buildTry(); // kept across redraws, so a language switch doesn't wipe the last answer
    host.replaceChildren(top, h("div", { class: "api-grid" }, list, detail, h("div", { class: "right" }, panel, tryPanel)));
  }

  function buildTry() {
    const opts = names();
    if (!tryState.model || !opts.includes(tryState.model)) tryState.model = chatModel();
    const select = h("select", { class: "select", "aria-label": "Model", onChange: (e) => { tryState.model = e.target.value; } }, (opts.length ? opts : ["<no models>"]).map((n) => h("option", { value: n, selected: n === tryState.model }, n)));
    const max = h("input", { class: "input", value: tryState.max, inputmode: "numeric", "aria-label": "Max tokens", onInput: (e) => { tryState.max = e.target.value; } });
    const text = h("textarea", { class: "textarea", "aria-label": "Message", onInput: (e) => { tryState.text = e.target.value; } });
    text.value = tryState.text;
    const sw = h("button", { class: "switch", type: "button", role: "switch", "aria-checked": tryState.stream ? "true" : "false", "aria-label": "Stream" });
    sw.addEventListener("click", () => { tryState.stream = !tryState.stream; sw.setAttribute("aria-checked", tryState.stream ? "true" : "false"); });
    const pills = h("div", { class: "pills" });
    const out = h("div", { class: "out", role: "status" }, h("span", { class: "muted" }, "The answer appears here."));
    const send = h("button", { class: "btn primary", type: "button" }, icon("send", 15, "currentColor"), "Send");
    send.addEventListener("click", async () => {
      send.disabled = true; pills.replaceChildren(); out.className = "out";
      out.replaceChildren(h("span", { class: "muted" }, "Waiting for the model… the first request after a pause loads it, which takes a while."));
      const t0 = performance.now();
      let first = null, tokens = 0, answer = "";
      const node = document.createTextNode(""), caret = h("span", { class: "caret" });
      try {
        const res = await fetch("/admin/try/chat", { method: "POST", headers: { ...ADMIN, "Content-Type": "application/json" },
          body: JSON.stringify({ model: select.value, messages: [{ role: "user", content: text.value }], max_tokens: Number(max.value) || 128, stream: tryState.stream }) });
        if (!res.ok) {
          let msg = `HTTP ${res.status}`;
          try { msg = (await res.json()).error.message; } catch (_) { /* keep the status */ }
          out.className = "out err"; out.textContent = msg;
          pills.replaceChildren(h("span", { class: "pill bad" }, `${res.status} ${res.status === 404 ? "Not found" : "Error"}`));
          return;
        }
        if (tryState.stream) {
          out.replaceChildren(node, caret);
          const reader = res.body.getReader(), dec = new TextDecoder();
          let buf = "";
          for (;;) {
            const { done, value } = await reader.read();
            if (done) break;
            buf += dec.decode(value, { stream: true });
            const events = buf.split("\n\n"); buf = events.pop();
            for (const ev of events) for (const line of ev.split("\n")) {
              if (!line.startsWith("data:") || line.includes("[DONE]")) continue;
              let piece = "";
              try { const d = JSON.parse(line.slice(5)); const c = (d.choices || [])[0] || {}; piece = (c.delta && (c.delta.content || c.delta.reasoning_content)) || c.text || ""; } catch (_) { continue; }
              if (!piece) continue;
              if (first === null) first = performance.now();
              tokens += 1; answer += piece; node.data = answer;
            }
          }
        } else {
          const body = await res.json();
          answer = ((body.choices || [])[0] || {}).message ? body.choices[0].message.content : "";
          tokens = (body.usage && body.usage.completion_tokens) || 0;
          out.textContent = answer;
        }
        caret.remove();
        const total = performance.now() - t0;
        const decodeS = first !== null && tokens > 1 ? (performance.now() - first) / 1000 : 0;
        pills.replaceChildren(h("span", { class: "pill ok" }, "200 OK"),
          ...(first !== null ? [h("span", { class: "pill info" }, `TTFT ${fmt(first - t0)} ms`)] : []),
          h("span", { class: "pill info" }, `${tokens} token${tokens === 1 ? "" : "s"}${decodeS ? ` · ${fmt((tokens - 1) / decodeS)} tok/s` : ""}`),
          h("span", { class: "pill" }, `${fmt(total)} ms`));
      } catch (e) {
        out.className = "out err"; out.textContent = `Could not reach the daemon: ${e.message}`;
      } finally { send.disabled = false; }
    });
    return h("section", { class: "panel try", "aria-label": "Try it" },
      h("div", { class: "th" }, h("span", { class: "chip30", style: { "--hue": "#3e63dd", display: "inline-flex", width: "30px", height: "30px", borderRadius: "8px", alignItems: "center", justifyContent: "center", background: "color-mix(in srgb, #3e63dd 12%, transparent)", color: "#3e63dd" } }, icon("send", 15, "currentColor", 1.8)),
        h("h3", {}, "Try it"), h("span", { class: "hint" }, "uses the console session, not an app key")),
      h("div", { class: "two" }, h("label", { class: "field" }, "Model", select), h("label", { class: "field" }, "Max tokens", max)),
      h("label", { class: "field" }, "Message", text),
      h("div", { class: "ctl" }, sw, "Stream", send), pills, out);
  }

  let last = "";
  draw();
  return {
    update() { // redraw only when the registered models or endpoints change, so typing in Try it is never disturbed
      const sig = JSON.stringify([names(), store.info && store.info.endpoint, store.info && store.info.lan_endpoint]);
      if (sig !== last) {
        last = sig;
        if (!tryPanel || !tryPanel.contains(document.activeElement)) { tryPanel = null; draw(); }
      }
    },
  };
}
