"""The Pull page's pure helpers (rain rate, focus, text) and the palette's pull target, run under node."""
import json
import shutil
import subprocess

import pytest

from mxconsole.gateway import CONSOLE_DIR, CONSOLE_ASSETS

pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")


def node(expr: str, module: str = "page-pull.js"):
    script = f"""
      import * as m from {json.dumps((CONSOLE_DIR / module).as_uri())};
      console.log(JSON.stringify({expr}));
    """
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_the_page_is_served_and_wired_in():
    assert "page-pull.js" in CONSOLE_ASSETS
    assert "pull: pullPage" in (CONSOLE_DIR / "main.js").read_text()


def test_shard_rate_follows_the_download_speed():
    assert node("m.rain(0, true)")["count"] == 0
    slow, mid, fast = (node(f"m.rain({bps}, true)") for bps in (500_000, 10_000_000, 120_000_000))
    assert 0 < slow["count"] < mid["count"] < fast["count"] == 14
    assert slow["fall"] > mid["fall"] > fast["fall"]  # faster download, faster rain


def test_the_rain_stops_when_the_pull_is_not_running():
    assert node("m.rain(50_000_000, false)")["count"] == 0
    assert node("[m.stage(null), m.stage({status:'running'}), m.stage({status:'done'}), m.stage({status:'failed'}), m.stage({status:'cancelled'}), m.stage({status:'unsupported'})]") == [
        "idle", "running", "done", "failed", "cancelled", "failed"]


def test_intensity_is_zero_without_speed_and_never_above_one():
    assert node("[m.intensity(0), m.intensity(-5), m.intensity(1e12) <= 1, m.intensity(1e6) > 0]") == [0, 0, True, True]


def test_focus_prefers_the_pick_then_a_running_pull_then_the_newest():
    pulls = "[{id:3,status:'done'},{id:2,status:'running'},{id:1,status:'failed'}]"
    assert node(f"m.pickFocus({pulls}, 1).id") == 1
    assert node(f"m.pickFocus({pulls}, null).id") == 2
    assert node("m.pickFocus([{id:3,status:'done'},{id:1,status:'failed'}], 99).id") == 3
    assert node("m.pickFocus([], null)") is None


def test_text_helpers():
    assert node("[m.bytes(512), m.bytes(2048), m.bytes(5*1024*1024), m.bytes(3*1024**3)]") == ["512 B", "2 KB", "5.0 MB", "3.00 GB"]
    assert node("[m.speedText(0), m.speedText(2*1024*1024)]") == ["—", "2.0 MB/s"]
    assert node("[m.etaText(null), m.etaText(42), m.etaText(125)]") == ["—", "42 s", "2 min 5 s"]
    assert node("m.headline({status:'unsupported', steps:[]})") == "MAX can't run this yet"
    assert node("m.headline({status:'running', steps:[{state:'now', label:'Download'}]})") == "Download…"


def test_the_palette_pull_row_opens_the_page_with_the_ref_filled_in():
    for query, target in [("pull qwen2.5-3b", "pull/qwen2.5-3b"), ("pull Qwen/Qwen2.5-3B-Instruct", "pull/Qwen%2FQwen2.5-3B-Instruct"),
                          ("pull", "pull"), ("", "pull"), ("pull a model please", "pull"), ("download smollm2-1.7b", "pull/smollm2-1.7b")]:
        assert node(f"m.pullTarget({json.dumps(query)})", "palette.js") == target, query


def test_a_typed_ref_gets_its_own_palette_row():
    rows = node('m.pullRowFor("pull acme/weird")', "palette.js")
    assert [(r["label"], r["cli"]) for r in rows] == [("Pull acme/weird", "mxc pull acme/weird")]
    assert node('m.pullRowFor("pull")', "palette.js") == [] and node('m.pullRowFor("serve acme/x")', "palette.js") == []
    assert node('m.pullRowFor("pull a model please")', "palette.js") == []
