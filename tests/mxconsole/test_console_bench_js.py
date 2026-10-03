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


def test_sound_is_only_started_from_a_click():
    src = (CONSOLE_DIR / "page-bench.js").read_text()
    assert src.count("vroom()") == 2  # its definition, and the one call inside race(), reached only from the button
    assert "onRace(true)" in src and "race(false)" in src
    assert "if (sound) vroom()" in src
