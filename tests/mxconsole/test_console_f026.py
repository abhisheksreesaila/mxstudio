"""F-026: the Add a model blade. Pure helpers run under node; the rest checks what ships."""
import json
import re
import shutil

import pytest
from test_console_blades import node

from mxconsole.gateway import CONSOLE_DIR

needs_node = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
JS = "blade-add.js"


def call(fn: str, *args):
    return node(f"m.{fn}({', '.join(json.dumps(a) for a in args)})", JS)


def by_id(rows):
    return {r["id"]: r for r in rows}


# -- fit chips and results -----------------------------------------------------------------------
@needs_node
def test_fit_chips_say_it_in_words():
    assert call("fitChip", "ok") == {"key": "ok", "label": "fits"}
    assert call("fitChip", "tight") == {"key": "tight", "label": "tight fit"}
    assert call("fitChip", "no") == {"key": "no", "label": "too big"}
    assert call("fitChip", "port") == {"key": "port", "label": "needs porting"}
    assert call("fitChip", None) == {"key": "inst", "label": "size unknown"}


ROW = {"id": "qwen2.5-0.5b", "repo_id": "Qwen/Qwen2.5-0.5B-Instruct", "source": "max", "architecture": "Qwen2ForCausalLM",
       "params": 500000000, "size_gb": 1.0, "est_vram_gb": 2.2, "supported": True, "fit": "ok", "suggestions": []}


@needs_node
def test_result_view_names_source_size_and_vram():
    v = call("resultView", ROW)
    assert v["sourceLabel"] == "MAX" and v["sourceKey"] == "max" and v["chip"]["label"] == "fits"
    assert v["id"] == "qwen2.5-0.5b" and v["repo"] == "Qwen/Qwen2.5-0.5B-Instruct"
    assert v["meta"] == "Qwen2 · 500M · 1 GB" and v["vram"] == "~2.2 GB VRAM"
    w = call("resultView", dict(ROW, source="hf", params=None, size_gb=None, est_vram_gb=None, fit=None))
    assert w["sourceLabel"] == "HF" and w["vram"] == "" and w["chip"]["key"] == "inst"


@needs_node
def test_filter_results():
    rows = [{"fit": f} for f in ("ok", "tight", "no", "port", None)]
    assert [r["fit"] for r in call("filterResults", rows, "fits")] == ["ok", "tight"]
    assert [r["fit"] for r in call("filterResults", rows, "port")] == ["port"]
    assert len(call("filterResults", rows, "all")) == 5


# -- the search screen state (no timers: debouncing lives in the blade) --------------------------------
MEM = {"basis": "gpu", "name": "RTX 4090", "free_gb": 20.3, "total_gb": 24}


@needs_node
def test_search_view_states():
    assert call("searchView", {"q": "", "loading": True})["kind"] == "loading"
    assert call("searchView", {"q": "x", "error": "boom"}) == {"kind": "error", "message": "boom", "title": "", "rows": [], "memory": ""}
    v = call("searchView", {"q": "", "resp": {"memory": MEM, "results": [{"id": "a"}]}})
    assert v["kind"] == "list" and v["title"] == "Curated models" and v["memory"] == "RTX 4090 · 20.3 GB free of 24 GB"
    v = call("searchView", {"q": "qwen", "resp": {"memory": MEM, "results": [{"id": "a"}, {"id": "b"}]}})
    assert v["title"] == "2 results"
    v = call("searchView", {"q": "zzz", "resp": {"memory": MEM, "results": []}})
    assert v["kind"] == "empty" and "zzz" in v["message"]
    ram = call("searchView", {"q": "", "resp": {"memory": {"basis": "ram", "name": "system RAM", "free_gb": 9, "total_gb": 16}, "results": []}})
    assert ram["memory"] == "system RAM · 9 GB free of 16 GB"


@needs_node
def test_warn_view_compares_need_with_free_and_offers_picks():
    row = {"id": "Qwen/Qwen3-32B", "repo_id": "Qwen/Qwen3-32B", "est_vram_gb": 78.4, "size_gb": 65.6, "suggestions": [
        {"id": "qwen3-8b", "repo_id": "Qwen/Qwen3-8B", "architecture": "Qwen3ForCausalLM", "params": 8000000000, "size_gb": 16, "est_vram_gb": 20.2}]}
    v = call("warnView", row, {"name": "RTX 4090", "free_gb": 22.5, "total_gb": 24})
    assert v["need"] == 78.4 and v["free"] == 22.5 and v["freePct"] == pytest.approx(28.7, abs=0.1)
    assert v["heading"] == "Qwen3-32B needs ~78.4 GB. You have 22.5 GB free." and v["gpu"] == "RTX 4090"
    assert v["suggestions"] == [{"id": "qwen3-8b", "repo": "Qwen/Qwen3-8B", "label": "fits · ~20.2 GB", "detail": "Qwen3 · 8B · 16 GB"}]


# -- pull progress and the setup stages ---------------------------------------------------------
PULL = {"id": 3, "ref": "qwen2.5-0.5b", "status": "running", "step": "download", "total_bytes": 1_000_000_000, "done_bytes": 450_000_000,
        "percent": 45.0, "speed": 45_000_000, "eta_seconds": 12, "message": "", "hint": None, "model": None,
        "files": [{"name": "a", "size": 1, "done": 1, "state": "done"}, {"name": "b", "size": 2, "done": 1, "state": "now"}],
        "steps": [{"id": "resolve", "state": "done", "detail": "Qwen/Qwen2.5-0.5B-Instruct@abc"}, {"id": "download", "state": "now", "detail": ""}]}


@needs_node
def test_pull_detail_shows_bytes_speed_and_files():
    assert call("pullDetail", PULL) == "450 MB of 1 GB · 45 MB/s · 2 files · 12s left"
    resolving = {"status": "running", "step": "resolve", "files": [], "steps": [{"id": "resolve", "state": "now", "detail": "looking up x on Hugging Face…"}]}
    assert call("pullDetail", resolving) == "looking up x on Hugging Face…"
    failed = {"status": "failed", "message": "Gated repo", "hint": "accept the licence", "steps": [], "files": []}
    assert call("pullDetail", failed) == "Gated repo · accept the licence"


@needs_node
def test_setup_stages_mid_fetch():
    run = {"kind": "pull", "phase": "fetch", "hw": "needs ~2.2 GB, 20 GB free", "fetch": PULL, "ref": "qwen2.5-0.5b",
           "times": {"check": {"start": 90_000, "end": 90_000}, "fetch": {"start": 95_000}}}
    rows = call("setupStages", run, 100_000)
    assert [r["id"] for r in rows] == ["check", "fetch", "compile", "serve", "smoke"]
    s = by_id(rows)
    assert s["check"]["status"] == "done" and s["check"]["detail"] == "needs ~2.2 GB, 20 GB free"
    assert s["fetch"]["status"] == "running" and s["fetch"]["percent"] == 45.0 and s["fetch"]["elapsed"] == 5000
    assert s["fetch"]["detail"].startswith("450 MB of 1 GB")
    assert {s[k]["status"] for k in ("compile", "serve", "smoke")} == {"pending"}
    assert s["fetch"]["twin"] == "mxc pull qwen2.5-0.5b"


@needs_node
def test_setup_stages_later_phases_failure_cancel_and_ready():
    t = {"check": {"start": 0, "end": 0}, "fetch": {"start": 0, "end": 4000}, "compile": {"start": 4000}}
    done = dict(PULL, status="done")
    s = by_id(call("setupStages", {"kind": "pull", "phase": "compile", "fetch": done, "times": t, "name": "q"}, 100_000))
    assert s["fetch"]["status"] == "done" and s["fetch"]["elapsed"] == 4000 and s["fetch"]["percent"] == 100
    assert s["compile"]["status"] == "running" and s["compile"]["elapsed"] == 96_000 and s["compile"]["twin"] == "mxc serve q"
    s = by_id(call("setupStages", {"kind": "pull", "phase": "failed", "failedAt": "compile", "error": "out of memory", "fetch": done, "times": t}, 100_000))
    assert s["compile"]["status"] == "failed" and s["compile"]["detail"] == "out of memory" and s["serve"]["status"] == "pending"
    s = by_id(call("setupStages", {"kind": "pull", "phase": "cancelled", "failedAt": "fetch", "fetch": dict(PULL, status="cancelled"), "times": t}, 100_000))
    assert s["fetch"]["status"] == "cancelled" and s["check"]["status"] == "done"
    s = by_id(call("setupStages", {"kind": "pull", "phase": "ready", "smoke": "Hello there, friend!", "fetch": done, "times": t}, 100_000))
    assert {r["status"] for r in s.values()} == {"done"} and s["smoke"]["detail"] == "“Hello there, friend!”"


PORT = {"id": 1, "ref": "org/Mamba2-1.3b", "name": "mamba2-1.3b", "status": "running", "stage": "build",
        "stages": [{"name": "workspace", "status": "done", "started": 10.0, "ended": 12.5, "detail": "~/.mxc/ports/mamba2", "attempt": 1},
                   {"name": "generate", "status": "done", "started": 12.5, "ended": 80.0, "detail": "", "attempt": 1},
                   {"name": "build", "status": "running", "started": 80.0, "ended": None, "detail": "compiling graph", "attempt": 2},
                   {"name": "parity check", "status": "pending", "started": None, "ended": None, "detail": "", "attempt": 0}],
        "log_tail": ["step one", "error: dtype mismatch"], "model": None}


@needs_node
def test_port_job_maps_to_stage_rows():
    rows = call("portStageRows", PORT, 85_000)
    assert [r["label"] for r in rows] == ["Prepare workspace", "Generate the model", "Build the graph", "Parity check"]
    assert [r["status"] for r in rows] == ["done", "done", "running", "pending"]
    assert rows[0]["elapsed"] == 2500 and rows[2]["elapsed"] == 5000 and rows[3]["elapsed"] is None
    assert rows[2]["detail"] == "compiling graph · attempt 2" and rows[0]["detail"] == "~/.mxc/ports/mamba2"
    s = call("setupStages", {"kind": "port", "phase": "fetch", "port": PORT, "ref": "org/Mamba2-1.3b", "hw": "x", "times": {"check": {"start": 0, "end": 0}}}, 85_000)
    assert [r["id"] for r in s][-3:] == ["compile", "serve", "smoke"] and s[1]["id"].startswith("port:") and s[1]["twin"] == "mxc port org/Mamba2-1.3b --auto"
    assert call("portStageRows", {"stages": [{"name": "mystery_step", "status": "skipped"}]}, 0)[0]["label"] == "Mystery step"
    assert call("portLogLines", {"log_tail": ["a", {"line": "b"}, {"text": "c"}, "d"]}) == ["a", "b", "c", "d"]
    assert call("portLogLines", {}) == []


@needs_node
def test_small_formatters_and_commands():
    assert call("elapsedText", 0) == "0.0s" and call("elapsedText", 3200) == "3.2s" and call("elapsedText", 65000) == "1m 05s"
    assert call("elapsedText", None) == ""
    curl = call("curlFor", "http://127.0.0.1:11435/v1", "qwen2.5-0.5b")
    assert curl.startswith("curl http://127.0.0.1:11435/v1/chat/completions") and "$MXC_API_KEY" in curl and '"model": "qwen2.5-0.5b"' in curl
    assert call("twin", "port", "org/m") == "mxc port org/m --auto"
    assert call("twin", "pull", "qwen2.5-0.5b") == "mxc pull qwen2.5-0.5b"


# -- what ships ------------------------------------------------------------------------------------
def test_the_blade_follows_the_csp_and_the_blade_contract():
    js = (CONSOLE_DIR / JS).read_text()
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js and "outerHTML" not in js
    assert not re.search(r"""\bstyle\s*[:=]\s*["'`]""", js), "inline style strings are blocked by the CSP"
    assert "onClose" in js and 'register("add"' in js
    assert 'push("security", { id' in js and 'push("model", { id' in js
    for route in ("/admin/ports", "/admin/pulls", "/admin/search", "/admin/try/chat"):
        assert route in js, route


def test_the_stylesheet_is_scoped_and_balanced():
    css = (CONSOLE_DIR / "blade-add.css").read_text()
    assert css.count("{") == css.count("}")
    depth = 0
    for line in css.splitlines():
        depth += line.count("{") - line.count("}")
        assert depth >= 0
    for sel in re.findall(r"^\s*([.:][^{}@]*)\{", css, flags=re.M):
        assert all(part.strip().startswith(".b-add") for part in sel.split(",")), sel
