"""The console's hash routes: the JS parser (run under node) and the CLI's mapping must agree."""
import json
import shutil
import subprocess

import pytest

from mxconsole import report
from mxconsole.gateway import CONSOLE_ASSETS, CONSOLE_DIR

CASES = [  # (page, model, hash the CLI prints)
    ("home", None, ""), ("overview", None, ""), ("models", None, ""), ("metrics", None, ""), ("activity", None, ""),
    ("ports", None, ""), ("keys", None, "keys"), ("network", None, "gateway"), ("gateway", None, "gateway"),
    ("daemon", None, "settings"), ("settings", None, "settings"), ("pull", None, "add"), ("add", None, "add"),
    ("model", "tiny", "model/tiny"), ("security", "tiny", "security/tiny"), ("bench", "tiny", "bench/tiny"),
    ("benchmarks", "tiny", "bench/tiny"), ("logs", "tiny", "logs/tiny"), ("notebook", "tiny", "notebook/tiny"),
    ("benchmarks", None, "bench"), ("logs", None, ""), ("model", None, ""),
    ("model", "my model/x", "model/my%20model%2Fx"), ("bench", "o'b", "bench/o%27b"),
]


@pytest.mark.parametrize("page,model,want", CASES)
def test_cli_maps_a_page_to_a_hash(page, model, want):
    assert report.console_hash(page, model) == want


def test_api_is_the_docs_page_and_every_page_name_is_mapped():
    assert report.console_hash("api") is None and report.console_link("api").endswith("/docs")
    assert all(report.console_hash(p, "m") is not None for p in report.PAGES if p != "api")


needs_node = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")


def node(expr: str):
    script = f"import * as r from {json.dumps((CONSOLE_DIR / 'routes.js').as_uri())}; console.log(JSON.stringify({expr}));"
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def names(plan):
    return [(s["name"], s["opts"].get("id")) for s in plan["steps"]]


@needs_node
def test_the_console_opens_what_the_cli_links_to():
    assert "routes.js" in CONSOLE_ASSETS
    for page, model, want in CASES:
        plan = node(f"r.parseHash({json.dumps('#' + want)})")
        if model:  # the console decodes what the CLI encoded
            kind = want.split("/")[0]
            assert plan["steps"][0] == {"name": "model", "opts": {"id": model}}, want
            assert names(plan)[-1] == (kind, model), want
        elif want in ("keys", "gateway", "settings", "add"):
            assert names(plan) == [(want, None)], want
        elif want == "":
            assert plan["steps"] == [] and not plan.get("needsModel"), page
        elif want == "bench":
            assert plan["needsModel"] == "bench", page


@needs_node
def test_old_page_names_and_edge_cases_still_open_something_sensible():
    assert node('r.parseHash("#network")')["steps"][0]["name"] == "gateway"
    assert node('r.parseHash("#pull")')["steps"][0]["name"] == "add"
    assert node('r.parseHash("#benchmarks")')["needsModel"] == "bench"
    assert node('r.parseHash("#api")')["redirect"] == "/docs"
    assert node('r.parseHash("#nonsense")')["steps"] == [] and node('r.parseHash("")')["steps"] == []
    assert names(node('r.parseHash("#model/org/repo-1")')) == [("model", "org/repo-1")]  # a Hugging Face name keeps its slash
    assert names(node('r.parseHash("#bench/tiny%20x")'))[-1] == ("bench", "tiny x")
    assert node('r.parseHash("#bench/%E0%A4%A")')["steps"][0]["opts"]["id"] == "%E0%A4%A"  # a bad escape never throws


@needs_node
def test_the_hash_follows_the_newest_blade():
    def stack(*blades):
        return json.dumps([{"name": n, "opts": o} for n, o in blades])
    assert node(f'r.hashFor({stack(("home", {}))})') == ""
    assert node(f'r.hashFor({stack(("home", {}), ("keys", {}))})') == "keys"
    assert node(f'r.hashFor({stack(("home", {}), ("model", {"id": "org/m"}), ("bench", {"id": "org/m"}))})') == "bench/org/m"
