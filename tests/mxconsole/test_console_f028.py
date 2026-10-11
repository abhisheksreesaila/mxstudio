"""F-028: the Notebook, Gateway and Settings blades. Pure helpers run under node; the rest checks what ships."""
import json
import re
import shutil
import subprocess

import pytest

from mxconsole.gateway import CONSOLE_DIR

needs_node = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
FILES = ["blade-notebook", "blade-gateway", "blade-settings"]


def node(module: str, expr: str):
    script = f"""
      import * as m from {json.dumps((CONSOLE_DIR / (module + '.js')).as_uri())};
      console.log(JSON.stringify({expr}) ?? "null");
    """
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def md(src):
    return node("blade-notebook", f"m.parseMarkdown({json.dumps(src)})")


def tags(nodes):
    return [n.get("tag", "#text") for n in nodes]


def walk(nodes):
    for n in nodes:
        yield n
        yield from walk(n.get("children", []))


# -- markdown renderer ---------------------------------------------------------------------------------
@needs_node
def test_markdown_blocks_headings_paragraphs_lists_and_fences():
    tree = md("# Title\n\nSome **bold**, *italic* and `code`.\nSecond line.\n\n- one\n- two\n  more\n\n1. a\n2. b\n\n```python\nx = 1\n\ny = 2\n```\n")
    assert tags(tree) == ["h1", "p", "ul", "ol", "pre"]
    para = tree[1]["children"]
    assert [n.get("tag", "#text") for n in para] == ["#text", "strong", "#text", "em", "#text", "code", "#text"]
    assert para[1]["children"] == [{"text": "bold"}] and para[5]["children"] == [{"text": "code"}]
    assert para[6]["text"] == ".\nSecond line.".replace("\n", " ")  # soft line breaks join with a space
    assert [li["children"] for li in tree[2]["children"]] == [[{"text": "one"}], [{"text": "two more"}]]
    pre = tree[4]
    assert pre["attrs"] == {"data-lang": "python"} and pre["children"][0]["children"] == [{"text": "x = 1\n\ny = 2"}]


@needs_node
def test_markdown_never_makes_html_script_is_text():
    tree = md("<script>alert(1)</script>\n\nhello <img src=x onerror=alert(1)> **b**")
    assert set(tags(tree)) == {"p"}
    flat = [n for n in walk(tree)]
    assert not any(n.get("tag") in {"script", "img"} for n in flat)
    assert tree[0]["children"] == [{"text": "<script>alert(1)</script>"}]
    # inside a code fence it is text too
    fenced = md("```\n<script>alert(1)</script>\n```")
    assert fenced[0]["children"][0]["children"] == [{"text": "<script>alert(1)</script>"}]


@needs_node
def test_only_http_and_https_links_become_links():
    ok = md("[docs](https://example.com/a?b=1) and [plain](http://example.com)")[0]["children"]
    links = [n for n in ok if n.get("tag") == "a"]
    assert [a["attrs"]["href"] for a in links] == ["https://example.com/a?b=1", "http://example.com/"]
    assert all(a["attrs"]["target"] == "_blank" and a["attrs"]["rel"] == "noopener noreferrer" for a in links)
    for bad in ["javascript:alert(1)", "JaVaScript:alert(1)", "data:text/html,<b>", "vbscript:x", "//evil.example/x", "/relative", "file:///etc/passwd"]:
        kids = md(f"[click]({bad})")[0]["children"]
        assert not any(n.get("tag") == "a" for n in kids), bad
        assert kids == [{"text": f"[click]({bad})"}], bad


@needs_node
def test_snake_case_and_odd_input_stay_text():
    assert md("use some_var_name and a * b * c here")[0]["children"] == [{"text": "use some_var_name and a * b * c here"}]
    assert md("") == [] and md("\n\n  \n") == []
    unclosed = md("```\nnever closed")
    assert unclosed[0]["tag"] == "pre" and unclosed[0]["children"][0]["children"] == [{"text": "never closed"}]


# -- chapters, outputs, counters ---------------------------------------------------------------------------
@needs_node
def test_chapters_come_from_markdown_headings_only():
    cells = [
        {"type": "markdown", "source": "# Qwen2.5 **tiny**\n\nintro", "outputs": []},
        {"type": "code", "source": "# not a heading\nx = 1", "outputs": []},
        {"type": "markdown", "source": "```\n## inside a fence\n```\n\n## Where the parameters live\n\n### detail", "outputs": []},
        {"type": "markdown", "source": "## Call it", "outputs": []},
    ]
    got = node("blade-notebook", f"m.chapters({json.dumps(cells)})")
    assert got == [{"title": "Qwen2.5 tiny", "level": 1, "cell": 0}, {"title": "Where the parameters live", "level": 2, "cell": 2},
                   {"title": "Call it", "level": 2, "cell": 3}]
    deep = [{"type": "markdown", "source": "### only deep\n#### deeper", "outputs": []}]
    assert [c["title"] for c in node("blade-notebook", f"m.chapters({json.dumps(deep)})")] == ["only deep", "deeper"]
    assert node("blade-notebook", "m.chapters([])") == []


@needs_node
def test_output_selection_text_stream_error_and_images():
    outs = [
        {"output_type": "stream", "name": "stdout", "text": ["hello\n", "world\n"]},
        {"output_type": "stream", "name": "stderr", "text": "warn\n"},
        {"output_type": "execute_result", "execution_count": 3, "data": {"text/plain": "42", "text/html": "<b>42</b>"}, "metadata": {}},
        {"output_type": "display_data", "data": {"image/png": "iVBORw0KGgo=", "text/plain": "<Figure>"}, "metadata": {}},
        {"output_type": "display_data", "data": {"text/html": "<table></table>"}, "metadata": {}},
        {"output_type": "error", "ename": "ValueError", "evalue": "bad", "traceback": ["\u001b[0;31mValueError\u001b[0m: bad"]},
    ]
    got = node("blade-notebook", f"m.outputsView({json.dumps(outs)})")
    assert got == [
        {"kind": "text", "stream": "stdout", "text": "hello\nworld\n"},
        {"kind": "text", "stream": "stderr", "text": "warn\n"},
        {"kind": "text", "stream": "result", "text": "42"},
        {"kind": "image", "mime": "image/png"},  # never carried to the page: the CSP blocks data: images
        {"kind": "other", "mime": "text/html"},
        {"kind": "error", "text": "ValueError: bad"},
    ]
    assert "iVBOR" not in json.dumps(got)
    assert node("blade-notebook", "m.outputsView(undefined)") == []


@needs_node
def test_execution_count_and_kernel_chip():
    ran = {"type": "code", "source": "1+1", "outputs": [{"output_type": "execute_result", "execution_count": 4, "data": {"text/plain": "2"}}]}
    assert node("blade-notebook", f"m.execCount({json.dumps(ran)})") == 4
    assert node("blade-notebook", 'm.execCount({type: "code", source: "x", outputs: []})') is None
    assert node("blade-notebook", 'm.execCount({type: "code", source: "x", outputs: [], execution_count: 2})') == 2
    on = node("blade-notebook", 'm.kernelChip({state: "ready"}, "https://box.lan:8443/v1")')
    assert on == {"on": True, "text": "model running · box.lan:8443/v1"}
    for model in ("null", '{state: "cold"}', '{state: "crashed"}'):
        assert node("blade-notebook", f'm.kernelChip({model}, "http://x/v1")') == {"on": False, "text": "start it to run cells"}


@needs_node
def test_open_in_jupyter_problems_name_the_fix():
    missing = node("blade-notebook", 'm.jupyterProblem({status: 501, message: "jupyter was not found"}, "/h/a.ipynb")')
    assert missing == {"text": "jupyter was not found", "fix": "pixi add jupyterlab"}
    failed = node("blade-notebook", 'm.jupyterProblem({status: 502, message: "did not start"}, "/h/a.ipynb")')
    assert failed["fix"] == "jupyter lab /h/a.ipynb"


# -- gateway and settings helpers ----------------------------------------------------------------------------
@needs_node
def test_gateway_routes_status_and_log_tail():
    net = {"ports": {"daemon": 11435, "litellm": 4000, "caddy": 8443}, "expose": {"caddy": True, "litellm": False}, "lan_endpoint": None,
           "models": [{"name": "tiny", "state": "ready", "port": 8001, "recent": 3}, {"name": "big", "state": "cold", "port": None, "recent": 0}]}
    routes = node("blade-gateway", f"m.routesView({json.dumps(net)})")
    assert routes["daemon"] == "http://127.0.0.1:11435/v1"
    assert routes["rows"][0] == {"name": "tiny", "upstream": "openai/tiny", "state": "ready", "port": 8001, "recent": 3}
    assert routes["rows"][1]["port"] is None
    assert node("blade-gateway", f"m.statusView({json.dumps(net)})") == {"litellm": False, "caddy": True, "lan": None, "port": 4000}
    assert node("blade-gateway", "m.routesView(null)") == {"daemon": None, "rows": []}
    assert node("blade-gateway", 'm.tailLines("a\\nb\\nc\\n", 2)') == ["b", "c"]
    assert node("blade-gateway", 'm.tailLines("", 5)') == [] and node("blade-gateway", "m.tailLines(undefined)") == []
    gw = {"tls": {"on": True, "endpoint": "https://box:8443/v1"}, "keys": {"active": 2, "revoked": 1}, "unauthorized_today": 1,
          "unknown_routes_today": 0, "admin_refused_today": 5}
    lines = dict(node("blade-gateway", f"m.safeguardLines({json.dumps(gw)})"))
    assert lines["One key per app"] == "2 active · 1 revoked · 1 request refused today (401)"
    assert lines["Admin stays home"] == "5 refusals today"


@needs_node
def test_settings_idle_rows_daemon_rows_and_theme():
    models = [{"name": "a", "pinned": False, "keep_alive": 300}, {"name": "b", "pinned": True, "keep_alive": 300}, {"name": "c", "pinned": False, "keep_alive": 5400}]
    assert [r["text"] for r in node("blade-settings", f"m.idleRows({json.dumps(models)})")] == ["after 5m idle", "pinned: never unloads", "after 90m idle"]
    info = {"host": "box", "endpoint": "http://127.0.0.1:11435/v1", "lan_endpoint": None, "max_version": "26.5", "uptime_seconds": 3700, "active_keys": 2}
    rows = dict(node("blade-settings", f"m.daemonRows({json.dumps(info)}, '/h/.mxc', 60)"))
    assert rows["Uptime"] == "1h 2m" and rows["MXC_HOME"] == "/h/.mxc" and rows["LAN endpoint"].startswith("off") and rows["MAX version"] == "26.5"
    assert node("blade-settings", "m.daemonRows(null)") == []
    assert node("blade-settings", "[m.effectiveTheme(null, true), m.effectiveTheme(null, false), m.effectiveTheme('light', true)]") == ["dark", "light", "light"]
    assert node("blade-settings", "m.DEFAULT_KEEP_ALIVE") == 300


# -- what ships -------------------------------------------------------------------------------------------
def test_braces_balance_in_the_three_stylesheets():
    for name in FILES:
        css = re.sub(r"/\*.*?\*/", "", (CONSOLE_DIR / f"{name}.css").read_text(), flags=re.S)
        assert css.count("{") == css.count("}"), name
        assert css.strip(), name


def test_blades_follow_the_contract_and_the_csp():
    for name in FILES:
        src = (CONSOLE_DIR / f"{name}.js").read_text()
        assert f'register("{name.removeprefix("blade-")}"' in src
        for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
            assert banned not in src, (name, banned)
        assert "style=" not in src and "setAttribute(\"style\"" not in src
        assert "placeholder(" not in src  # the F-023 stub is gone
        assert "blade.onClose" in src  # everything started in mount is torn down
    notebook = (CONSOLE_DIR / "blade-notebook.js").read_text()
    assert "wide: true" in notebook and "data:image" not in notebook and 'src:' not in notebook  # images are never loaded


@needs_node
def test_a_link_label_can_hold_code_and_the_header_tools_are_one_node():
    kids = md("[`org/tiny`](https://huggingface.co/org/tiny)")[0]["children"]
    assert kids[0]["tag"] == "a" and kids[0]["children"] == [{"tag": "code", "children": [{"text": "org/tiny"}]}]
    for name in ("blade-notebook", "blade-gateway"):
        assert re.search(r"tools: (\[chipEl, jupBtn\]|restartBtn),", (CONSOLE_DIR / f"{name}.js").read_text()), name


@needs_node
def test_parsers_are_linear_on_hostile_input():
    import time
    for line in ["[" * 40000, "*" * 40000, "`" * 40000, "_a" * 20000, "# " + "#" * 40000, "[a](" + "b" * 40000, "**" + "a" * 40000, "# " + " " * 40000 + "x"]:
        t = time.time()
        md(line)
        assert time.time() - t < 1.0, line[:10]  # includes node start-up; was seconds when quadratic
    script = f"""
      import * as m from {json.dumps((CONSOLE_DIR / 'blade-notebook.js').as_uri())};
      const t = performance.now(); m.parseMarkdown("[".repeat(40000)); m.parseMarkdown("# " + "#".repeat(40000));
      console.log(performance.now() - t);
    """
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert float(out.stdout) < 200, out.stdout


@needs_node
def test_jupyter_opens_only_on_this_machine_and_the_chip_uses_the_local_endpoint():
    assert node("blade-notebook", 'm.localUrl("http://127.0.0.1:8899/lab/tree/a.ipynb?token=t")') == "http://127.0.0.1:8899/lab/tree/a.ipynb?token=t"
    for bad in ["https://127.0.0.1:1/", "http://evil.example/", "http://localhost:1/", "http://127.0.0.1.evil.example/", "javascript:alert(1)"]:
        assert node("blade-notebook", f"m.localUrl({json.dumps(bad)})") is None, bad
    assert "store.info.lan_endpoint" not in (CONSOLE_DIR / "blade-notebook.js").read_text()
