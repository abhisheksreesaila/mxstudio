"""The drag race's placement maths is plain JS: run it under node (skipped where node isn't installed)."""
import json
import shutil
import subprocess

import pytest

from mxconsole.gateway import CONSOLE_ASSETS, CONSOLE_DIR

pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")


def js(expr: str):
    script = f"""
      import * as b from {json.dumps((CONSOLE_DIR / "page-bench.js").as_uri())};
      console.log(JSON.stringify({expr}));
    """
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_cars_are_placed_by_decode_speed_relative_to_the_fastest():
    results = [{"model": "a", "status": "done", "decode_tps": 300}, {"model": "b", "status": "done", "decode_tps": 150},
               {"model": "c", "status": "failed", "decode_tps": None}, {"model": "d", "status": "done", "decode_tps": 75}]
    got = js(f"b.placements({json.dumps(results)})")
    assert [(p["model"], p["fraction"], p["place"]) for p in got] == [("a", 1, 1), ("b", 0.5, 2), ("d", 0.25, 3)]  # no car for a failure
    assert js("b.placements([])") == []


def test_ordinals_gauge_scale_and_needle():
    assert js("[1, 2, 3, 4, 11, 12, 13, 21, 22, 103].map(b.ordinal)") == ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "103rd"]
    assert js("[b.niceMax(312), b.niceMax(40), b.niceMax(99), b.niceMax(12000)]") == [400, 50, 150, 13000]
    assert js("[b.needleAngle(0, 400), b.needleAngle(200, 400), b.needleAngle(400, 400), b.needleAngle(900, 400)]") == [-120, 0, 120, 120]


def test_the_page_script_is_served_and_the_palette_offers_the_run():
    assert "page-bench.js" in CONSOLE_ASSETS
    palette = (CONSOLE_DIR / "palette.js").read_text()
    assert '"Run benchmark"' in palette and "mxc bench --all" in palette
    assert "benchPage" in (CONSOLE_DIR / "main.js").read_text()


def run_node(body: str):
    script = f"""
      let made = 0;
      globalThis.window = {{ AudioContext: class {{ constructor() {{ made++; }} get currentTime() {{ return 0; }}
        createOscillator() {{ return {{ frequency: {{ setValueAtTime() {{}}, exponentialRampToValueAtTime() {{}}, value: 0 }}, connect() {{}}, start() {{}}, stop() {{}} }}; }}
        createBiquadFilter() {{ return {{ frequency: {{}}, Q: {{}}, connect() {{}} }}; }}
        createGain() {{ return {{ gain: {{ setValueAtTime() {{}}, exponentialRampToValueAtTime() {{}} }}, connect() {{}} }}; }}
        get destination() {{ return {{}}; }} close() {{}} }} }};
      const b = await import({json.dumps((CONSOLE_DIR / "page-bench.js").as_uri())});
      {body}
      console.log(JSON.stringify(made));
    """
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_sound_only_makes_an_audio_context_for_the_click():
    assert run_node("") == 0  # loading the page script builds no AudioContext
    assert run_node("b.playIfClicked(false); b.playIfClicked(undefined); b.playIfClicked(null);") == 0  # the silent first view
    assert run_node("b.playIfClicked(true);") == 1  # the click on "Race again"
    src = (CONSOLE_DIR / "page-bench.js").read_text()
    assert "onRace(true)" in src and "race(false)" in src  # only the button passes true; the first view passes false


def test_failed_models_and_estimates_are_labelled():
    run = {"results": [{"status": "done"}, {"status": "failed"}, {"status": "failed"}], "status": "done"}
    src = (CONSOLE_DIR / "page-bench.js").read_text()
    assert "failed` : \"done\"" in src or "· ${failures(run)} failed" in src
    assert "r.completion_estimated" in src and "r.prompt_estimated" in src and "no prefix cache" in src


def test_internal_apps_get_a_friendly_label_on_activity_and_overview():
    core = (CONSOLE_DIR / "core.js").read_text()
    assert 'name === "_bench"' in core and "benchmark" in core and 'name === "console"' in core
    for page in ("page-activity.js", "page-overview.js"):
        assert "appLabel(r.app)" in (CONSOLE_DIR / page).read_text(), page


def test_the_stylesheet_braces_balance_so_no_rule_is_swallowed_by_an_open_media_query():
    css = (CONSOLE_DIR / "console.css").read_text()
    assert css.count("{") == css.count("}")
    depth = 0
    for line in css.splitlines():
        if line.startswith("/* ---- benchmarks"):
            assert depth == 0, "the benchmarks block starts inside an unclosed rule, so it never applies on desktop"
        depth += line.count("{") - line.count("}")
