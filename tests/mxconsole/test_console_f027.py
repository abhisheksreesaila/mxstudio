"""F-027: the Benchmark and Logs blades. Pure helpers run under node; the rest checks what ships."""
import json
import re
import shutil
import subprocess

import pytest

from mxconsole.gateway import CONSOLE_DIR

needs_node = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
BANNED = re.compile(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|setAttribute\(\s*['\"]style['\"]")


def node(expr: str, module: str):
    script = f"""
      import * as m from {json.dumps((CONSOLE_DIR / module).as_uri())};
      console.log(JSON.stringify(await ({expr})) ?? "null");
    """
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


R = [{"model": "a", "status": "done", "decode_tps": 300, "ttft_p50_ms": 40},
     {"model": "b", "status": "done", "decode_tps": 150, "ttft_p50_ms": 20},
     {"model": "c", "status": "failed", "decode_tps": None, "ttft_p50_ms": None, "error": "oom"},
     {"model": "d", "status": "done", "decode_tps": 75, "ttft_p50_ms": 80}]


@needs_node
def test_higher_is_better_bars_sort_fastest_first_and_scale_to_the_top():
    rows = node(f"m.barRows({json.dumps(R)}, 'decode_tps', false, 'b')", "blade-bench.js")
    assert [(r["model"], r["fraction"], r["best"], r["me"]) for r in rows] == [
        ("a", 1, True, False), ("b", 0.5, False, True), ("d", 0.25, False, False)]  # a failure has no bar


@needs_node
def test_lower_is_better_bars_sort_smallest_first_and_keep_length_proportional_to_value():
    rows = node(f"m.barRows({json.dumps(R)}, 'ttft_p50_ms', true, 'a')", "blade-bench.js")
    assert [(r["model"], r["fraction"], r["best"]) for r in rows] == [("b", 0.25, True), ("a", 0.5, False), ("d", 1, False)]
    assert node("m.barRows([], 'decode_tps', false, 'x')", "blade-bench.js") == []


@needs_node
def test_workload_line_states_the_real_numbers_and_flags_estimates():
    run = {"prompt_tokens": 512, "max_tokens": 128, "runs": 5, "started_at": 0,
           "results": [{"status": "done", "completion_tokens": 120, "estimated": False}]}
    line = node(f"m.workloadLine({json.dumps(run)})", "blade-bench.js")
    assert "512 prompt tokens" in line and "120 tokens out" in line and "batch 1" in line and "5 runs" in line
    run["results"][0]["estimated"] = True
    assert "estimated" in node(f"m.workloadLine({json.dumps(run)})", "blade-bench.js")
    run["results"] = []
    assert "up to 128 tokens out" in node(f"m.workloadLine({json.dumps(run)})", "blade-bench.js")


@needs_node
def test_progress_percent_per_model_state():
    got = node("['queued','loading','warming','running','done','failed'].map((s) => m.progressPct({state: s, done: 2}, 4))", "blade-bench.js")
    assert got[0] == 0 and 0 < got[1] < got[2] < got[3] < 100 and got[4] == 100 and got[5] == 100


@needs_node
def test_log_lines_are_classified_by_severity():
    cases = ["ERROR: boom", "Traceback (most recent call last):", "CUDA error: out of memory", "worker exited with code 137",
             "WARNING: slow", "warn: x", "INFO: Application startup complete.", "POST /v1/chat 200"]
    got = node(f"{json.dumps(cases)}.map(m.classifyLine)", "blade-logs.js")
    assert got == ["err", "err", "err", "err", "warn", "warn", "", ""]
    assert node("m.classifyLine('terrorism is not a problem')", "blade-logs.js") == ""  # whole words only


@needs_node
def test_filtering_is_case_insensitive_and_reports_totals():
    text = "one\nTwo ERROR\nthree\n\nfour error again\n"
    got = node(f"m.visibleLines({json.dumps(text)}, 'error', false)", "blade-logs.js")
    assert got == {"lines": ["Two ERROR", "four error again"], "total": 4}
    assert node(f"m.visibleLines({json.dumps(text)}, '', false)", "blade-logs.js")["lines"] == ["one", "Two ERROR", "three", "four error again"]
    # a tail cut mid-line drops its partial first line
    assert node("m.visibleLines('rtial\\nfull\\n', '', true)", "blade-logs.js")["lines"] == ["full"]
    many = "\n".join(f"l{i}" for i in range(5000))
    assert len(node(f"m.visibleLines({json.dumps(many)}, '', false)", "blade-logs.js")["lines"]) == 2000


def test_blades_follow_the_contract_and_the_csp():
    for n in ("bench", "logs"):
        js = (CONSOLE_DIR / f"blade-{n}.js").read_text()
        assert f'register("{n}"' in js and "onClose" in js and not BANNED.search(js), n
        css = (CONSOLE_DIR / f"blade-{n}.css").read_text()
        assert css.count("{") == css.count("}") and ".b-" in css, n
    logs = (CONSOLE_DIR / "blade-logs.js").read_text()
    assert "poller.stop()" in logs and "/log" in logs and "X-Log-Truncated" in logs
    assert "tools: [followBtn, copy]" in logs and "toolsEl" not in logs
    bench = (CONSOLE_DIR / "blade-bench.js").read_text()
    assert "poller.stop()" in bench and "tools: runBtn" in bench and "toolsEl" not in bench
    assert "/admin/bench" in bench and "higher is better" in bench and "lower is better" in bench


RUN1 = {"id": 1, "started_at": 100, "status": "done", "results": [
    {"model": "a", "status": "done", "decode_tps": 300, "ttft_p50_ms": 40},
    {"model": "b", "status": "done", "decode_tps": 80, "ttft_p50_ms": 50},
    {"model": "c", "status": "failed", "decode_tps": None}]}
RUN2 = {"id": 2, "started_at": 200, "status": "done", "results": [{"model": "b", "status": "done", "decode_tps": 90, "ttft_p50_ms": 45}]}


@needs_node
def test_a_single_model_rerun_keeps_the_others_from_the_older_run():
    got = node(f"m.mergeLatest([{json.dumps(RUN2)}, {json.dumps(RUN1)}], null)", "blade-bench.js")
    by = {r["model"]: r for r in got}
    assert set(by) == {"a", "b"}  # c failed everywhere: no bar
    assert by["b"]["decode_tps"] == 90 and by["b"]["older"] is False  # this model: from the new run
    assert by["a"]["decode_tps"] == 300 and by["a"]["older"] is True and by["a"]["at"] == 100
    # input order does not matter, and `known` drops models that were removed
    assert {r["model"] for r in node(f"m.mergeLatest([{json.dumps(RUN1)}, {json.dumps(RUN2)}], ['b'])", "blade-bench.js")} == {"b"}
    rows = node(f"m.barRows(m.mergeLatest([{json.dumps(RUN2)}, {json.dumps(RUN1)}]), 'decode_tps', false, 'b')", "blade-bench.js")
    assert [(r["model"], r["older"], r["me"]) for r in rows] == [("a", True, False), ("b", False, True)]


@needs_node
@pytest.mark.parametrize("module", ["blade-bench.js", "blade-logs.js"])
def test_closing_stops_the_poll_no_further_calls(module):
    expr = """(async () => {
      const calls = [], timers = [];
      const p = m.createPoller(async () => { calls.push(1); }, () => 10, { set: (f) => timers.push(f) && timers.length, clear: () => {} });
      const tick = () => new Promise((r) => setTimeout(r, 0));
      p.start(); await tick(); const first = calls.length;
      timers.shift()(); await tick(); const second = calls.length;   // a scheduled tick runs while open
      const pending = timers.shift(); p.stop();                      // close with one tick still scheduled
      if (pending) pending(); await tick(); timers.forEach((f) => f()); await tick();
      return [first, second, calls.length]; })()"""
    assert node(expr, module) == [1, 2, 2]


@needs_node
def test_a_tick_in_flight_when_closed_schedules_nothing():
    expr = """(async () => { const timers = []; let n = 0, release;
      const p = m.createPoller(() => new Promise((r) => { n++; release = r; }), () => 1, { set: (f) => timers.push(f), clear: () => {} });
      p.start(); p.stop(); release(); await new Promise((r) => setTimeout(r, 0));
      return [n, timers.length]; })()"""
    assert node(expr, "blade-logs.js") == [1, 0]


from test_console import ADMIN  # noqa: E402
from test_gateway import http, store, sup  # noqa: E402,F401  (shared fixtures)


async def test_log_route_says_whether_the_tail_was_cut(http):
    from mxconsole.store import mxc_home
    logs = mxc_home() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "tiny.log").write_text("line\n" * 2000)
    assert (await http.get("/admin/models/tiny/log", headers=ADMIN)).headers["x-log-truncated"] == "1"
    (logs / "tiny.log").write_text("short\n")
    assert (await http.get("/admin/models/tiny/log", headers=ADMIN)).headers["x-log-truncated"] == "0"
    assert (await http.get("/admin/models/other/log", headers=ADMIN)).headers["x-log-truncated"] == "0"  # no file yet


def test_every_bar_row_has_the_same_fixed_value_column_so_equal_values_draw_equal_bars():
    css = (CONSOLE_DIR / "blade-bench.css").read_text()
    rules = [line for line in css.splitlines() if "grid-template-columns" in line and ".bn-row" in line]
    assert rules and all("auto" not in r and "var(--bn-val)" in r for r in rules)
    assert re.search(r"--bn-val:\s*\d+px", css)
    js = (CONSOLE_DIR / "blade-bench.js").read_text()
    assert "(store.models || [])" not in js and 'r.status === "done" || r.status === "failed"' in js  # cold open, running runs
