"""The ⌘K palette's fuzzy filter is plain JS: run it under node (skipped where node isn't installed)."""
import json
import shutil
import subprocess

import pytest

from hotfire.gateway import CONSOLE_DIR

pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")

ROWS = [
    {"group": "Suggested", "label": "Serve a model…", "cli": "hotfire serve <model>"},
    {"group": "Suggested", "label": "Create an API key…", "cli": "hotfire keys new <app>"},
    {"group": "qwen2.5-0.5b", "label": "Keep qwen2.5-0.5b loaded", "cli": "hotfire set qwen2.5-0.5b --pin"},
    {"group": "qwen2.5-0.5b", "label": "Stop qwen2.5-0.5b", "cli": "hotfire stop qwen2.5-0.5b"},
    {"group": "Go to", "label": "Metrics", "cli": "hotfire console metrics"},
]


def run(query: str) -> list[str]:
    script = f"""
      import {{ filterActions }} from {json.dumps((CONSOLE_DIR / "palette.js").as_uri())};
      console.log(JSON.stringify(filterActions({json.dumps(ROWS)}, {json.dumps(query)}).map((r) => r.label)));
    """
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_empty_query_keeps_every_row_in_order():
    assert run("") == [r["label"] for r in ROWS]


def test_substring_beats_scattered_letters():
    assert run("serve")[0] == "Serve a model…"
    assert run("stop")[0] == "Stop qwen2.5-0.5b"


def test_it_matches_the_cli_twin_and_skips_letters():
    assert run("keys new") == ["Create an API key…"]
    assert "Keep qwen2.5-0.5b loaded" in run("kp qwn")


def test_no_match_gives_nothing():
    assert run("zzzzqq") == []
