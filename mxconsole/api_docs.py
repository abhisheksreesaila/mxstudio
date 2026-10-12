"""The API reference at /docs: one plain HTML page rendered from the OpenAPI spec (no inline scripts; it loads theme.js from the daemon, so the console's CSP holds)."""
from __future__ import annotations

import json
import shlex
from html import escape as e

STYLE = "/console/docs.css"


def md(text: str) -> str:
    """Escape, then turn `code` spans into <code>: the spec's descriptions use them."""
    parts = e(text).split("`")
    return "".join(f"<code>{p}</code>" if i % 2 else p for i, p in enumerate(parts))


def _schema(spec: dict, name: str) -> dict:
    return spec["components"]["schemas"][name]


def _fields(spec: dict, op: dict) -> list[tuple[str, str, bool, str]]:
    body = op.get("requestBody")
    if not body:
        return []
    ref = body["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[-1]
    schema = _schema(spec, ref)
    rows = []
    for field, prop in schema.get("properties", {}).items():
        if "$ref" in prop:
            prop = _schema(spec, prop["$ref"].rsplit("/", 1)[-1])
        kind = prop.get("type", "string")
        kind = " | ".join(kind) if isinstance(kind, list) else kind
        note = prop.get("description", "") or (f"default {str(prop['default']).lower()}" if "default" in prop else "")
        rows.append((field, kind, field in schema.get("required", []), note))
    return rows


def _curl(path: str, method: str, base: str, model: str) -> str:
    bodies = {"/v1/chat/completions": {"model": model, "messages": [{"role": "user", "content": "Say hello in five words."}]},
              "/v1/completions": {"model": model, "prompt": "Once upon a time", "max_tokens": 32},
              "/v1/embeddings": {"model": model, "input": "hello"}}
    auth = "" if path == "/health" else ' -H "Authorization: Bearer $MXC_API_KEY"'
    if path not in bodies:
        return f"curl {base}{path}{auth}"
    # the model name is user data: JSON-encode it, then shell-quote the whole body
    return f"curl {base}{path}{auth} \\\n  -H 'Content-Type: application/json' \\\n  -d {shlex.quote(json.dumps(bodies[path]))}"


def render(spec: dict, base: str) -> str:
    models = spec["components"]["schemas"]["ModelName"].get("enum") or ["<model>"]
    model = models[0]
    out = [f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>mxconsole API reference</title><meta name="color-scheme" content="light dark">
<link rel="icon" href="/console/favicon.svg" type="image/svg+xml"><link rel="stylesheet" href="/console/blades.css">
<link rel="stylesheet" href="{STYLE}"><script src="/console/theme.js"></script></head>
<body class="docs"><header class="top"><a class="brand" href="/console">mxconsole</a><span class="mono">API reference</span>
<a class="back" href="/console">Back to the console</a></header><main>
<h1>OpenAI-compatible API</h1>
<p>{md(spec['info']['description'])}</p>
<h2>Base URL and key</h2>
<dl><dt>Base URL</dt><dd class="mono">{e(base)}/v1</dd>
<dt>Auth</dt><dd>Send <code>Authorization: Bearer $MXC_API_KEY</code>. Create a key with <code class="mono">mxc keys new &lt;app&gt;</code>
(or in the console, API keys). A missing or wrong key gets <code>401</code>.</dd>
<dt>Models</dt><dd class="mono">{e(', '.join(models))}</dd>
<dt>Spec</dt><dd><a href="/openapi.json">openapi.json</a></dd></dl>"""]
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            out.append(f'<section><h2><span class="m {e(method)}">{e(method.upper())}</span> <span class="mono">{e(path)}</span></h2>')
            out.append(f"<p>{md(op['summary'])}</p>")
            fields = _fields(spec, op)
            if fields:
                out.append("<table><thead><tr><th>Field</th><th>Type</th><th>Required</th><th></th></tr></thead><tbody>")
                out += [f'<tr><td class="mono">{e(f)}</td><td class="mono">{e(k)}</td><td>{"yes" if req else "no"}</td><td>{md(note)}</td></tr>'
                        for f, k, req, note in fields]
                out.append("</tbody></table>")
            out.append(f'<pre class="mono">{e(_curl(path, method, base, model))}</pre>')
            out.append("<p class=\"codes\">" + " ".join(f'<span class="mono">{e(code)}</span> {md(r["description"])}' for code, r in op["responses"].items()) + "</p>")
            out.append("</section>")
    out.append('<p class="foot">Try a request without a key from the console: add a model, then use its Ready card or <span class="mono">mxc console model &lt;name&gt;</span>.</p></main></body></html>')
    return "\n".join(out)
