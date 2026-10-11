"""The API reference at /docs: one plain HTML page rendered from the OpenAPI spec (no scripts, so the console's CSP holds)."""
from __future__ import annotations

from html import escape as e

STYLE = "/console/docs.css"


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
    if path == "/v1/chat/completions":
        data = '{"model": "%s", "messages": [{"role": "user", "content": "Say hello in five words."}]}' % model
    elif path == "/v1/completions":
        data = '{"model": "%s", "prompt": "Once upon a time", "max_tokens": 32}' % model
    elif path == "/v1/embeddings":
        data = '{"model": "%s", "input": "hello"}' % model
    else:
        data = None
    auth = '' if path == "/health" else ' -H "Authorization: Bearer $MXC_API_KEY"'
    if data is None:
        return f"curl {base}{path}{auth}"
    return f"curl {base}{path}{auth} \\\n  -H 'Content-Type: application/json' \\\n  -d '{data}'"


def render(spec: dict, base: str) -> str:
    models = spec["components"]["schemas"]["ModelName"].get("enum") or ["<model>"]
    model = models[0]
    out = [f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>mxconsole API reference</title><meta name="color-scheme" content="light dark">
<link rel="icon" href="/console/favicon.svg" type="image/svg+xml"><link rel="stylesheet" href="{STYLE}"></head>
<body><header><a class="brand" href="/console">mxconsole</a><span class="mono">API reference</span>
<a class="back" href="/console">Back to the console</a></header><main>
<h1>OpenAI-compatible API</h1>
<p>{e(spec['info']['description'])}</p>
<h2>Base URL and key</h2>
<dl><dt>Base URL</dt><dd class="mono">{e(base)}/v1</dd>
<dt>Auth</dt><dd>Send <code>Authorization: Bearer $MXC_API_KEY</code>. Create a key with <code class="mono">mxc keys new &lt;app&gt;</code>
(or in the console, API keys). A missing or wrong key gets <code>401</code>.</dd>
<dt>Models</dt><dd class="mono">{e(', '.join(models))}</dd>
<dt>Spec</dt><dd><a href="/openapi.json">openapi.json</a></dd></dl>"""]
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            out.append(f'<section><h2><span class="m {e(method)}">{e(method.upper())}</span> <span class="mono">{e(path)}</span></h2>')
            out.append(f"<p>{e(op['summary'])}</p>")
            fields = _fields(spec, op)
            if fields:
                out.append("<table><thead><tr><th>Field</th><th>Type</th><th>Required</th><th></th></tr></thead><tbody>")
                out += [f'<tr><td class="mono">{e(f)}</td><td class="mono">{e(k)}</td><td>{"yes" if req else "no"}</td><td>{e(note)}</td></tr>'
                        for f, k, req, note in fields]
                out.append("</tbody></table>")
            out.append(f'<pre class="mono">{e(_curl(path, method, base, model))}</pre>')
            out.append("<p class=\"codes\">" + " ".join(f'<span class="mono">{e(code)}</span> {e(r["description"])}' for code, r in op["responses"].items()) + "</p>")
            out.append("</section>")
    out.append('<p class="foot">Try a request without a key from the console: add a model, then use its Ready card or <span class="mono">mxc console model &lt;name&gt;</span>.</p></main></body></html>')
    return "\n".join(out)
