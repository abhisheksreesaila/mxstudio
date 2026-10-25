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


def route(hash_: str):
    return node(f"r.parseRoute({json.dumps(hash_)})")


@needs_node
def test_the_console_opens_what_the_cli_links_to():
    """F-052: every hash the CLI prints still lands on the page it means (old shapes map to the new routes)."""
    assert "routes.js" in CONSOLE_ASSETS
    tab = {"model": "overview", "bench": "benchmark", "logs": "logs", "notebook": "notebook"}
    for page, model, want in CASES:
        got = route("#" + want)
        if model and want.split("/")[0] in tab:
            assert got == {"page": "model", "model": model, "tab": tab[want.split("/")[0]]}, want
        elif model:  # security is one place for every model
            assert got == {"page": "security"}, want
        elif want in ("keys", "gateway", "settings", "add"):
            assert got == {"page": want}, want
        elif want == "":
            assert got == {"page": "home"}, page
        elif want == "bench":
            assert got == {"page": "model", "model": None, "tab": "benchmark", "needsModel": True}, page


@needs_node
def test_the_new_routes():
    assert route("#/") == {"page": "home"} and route("") == {"page": "home"} and route("#") == {"page": "home"}
    for place in ("keys", "gateway", "security", "settings", "add"):
        assert route(f"#/{place}") == {"page": place}
    assert route("#/models/tiny/logs") == {"page": "model", "model": "tiny", "tab": "logs"}
    assert route("#/models/tiny") == {"page": "model", "model": "tiny", "tab": "overview"}
    assert route("#/models/org/repo-1/benchmark") == {"page": "model", "model": "org/repo-1", "tab": "benchmark"}  # a Hugging Face name keeps its slash
    assert route("#/models/my%20model/notebook")["model"] == "my model"
    assert route("#/models/%E0%A4%A/logs")["model"] == "%E0%A4%A"  # a bad escape never throws


@needs_node
def test_unknown_routes_go_home_and_the_api_page_redirects():
    for bad in ("#/nonsense", "#/keys/extra", "#/models", "#/models/", "#/models//logs", "#nonsense"):
        assert route(bad) == {"page": "home"}, bad
    for inherited in ("#/__proto__/x", "#/constructor/x", "#/toString", "#hasOwnProperty/x", "#/__proto__"):
        assert route(inherited) == {"page": "home"}, inherited  # Object's own names are not places
    assert route("#/models/x/__proto__") == {"page": "model", "model": "x/__proto__", "tab": "overview"}  # a name, not a lookup
    assert route("#api") == {"redirect": "/docs"}
    assert route("#network") == {"page": "gateway"} and route("#pull") == {"page": "add"} and route("#daemon") == {"page": "settings"}


@needs_node
def test_the_hash_a_route_has_is_canonical_and_parses_back():
    cases = [({"page": "home"}, "#/"), ({"page": "keys"}, "#/keys"), ({"page": "add"}, "#/add"),
             ({"page": "model", "model": "org/m", "tab": "logs"}, "#/models/org/m/logs"),
             ({"page": "model", "model": "my model", "tab": "overview"}, "#/models/my%20model/overview")]
    for r, want in cases:
        assert node(f"r.hashFor({json.dumps(r)})") == want
        assert route(want) == r


@needs_node
def test_the_top_bar_only_says_where_you_are():
    def crumbs(r):
        return [(c["label"], bool(c.get("to"))) for c in node(f"r.crumbsFor({json.dumps(r)})")]
    assert crumbs({"page": "home"}) == [("My models", False)]
    assert crumbs({"page": "keys"}) == [("API keys", False)]
    assert crumbs({"page": "add"}) == [("My models", True), ("Add a model", False)]
    assert crumbs({"page": "model", "model": "tiny", "tab": "overview"}) == [("My models", True), ("tiny", False)]
    assert crumbs({"page": "model", "model": "tiny", "tab": "logs"}) == [("My models", True), ("tiny", False)]  # F-053: the tab is not a crumb


@needs_node
def test_back_uses_history_only_after_a_step_inside_the_console():
    assert node("r.backUsesHistory({mxc: 1})") is True
    for state in ("null", "undefined", "{mxc: 0}", "{}", "{mxc: '1'}", "{other: 1}"):
        assert node(f"r.backUsesHistory({state})") is False, state
