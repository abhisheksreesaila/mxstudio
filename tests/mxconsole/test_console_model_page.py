"""F-053 (ADR 0005): one model page with tabs, a verbs-only command bar, flat buttons and a 2x2 "Right now" grid.
Pure decisions run under node; the page's structure is read from the source."""
import json
import re
import shutil
import subprocess

import pytest

from mxconsole.gateway import CONSOLE_ASSETS, CONSOLE_DIR

needs_node = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")


def node(module: str, expr: str):
    script = f"import * as m from {json.dumps((CONSOLE_DIR / module).as_uri())}; console.log(JSON.stringify({expr}) ?? 'null');"
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def text(name: str) -> str:
    return (CONSOLE_DIR / name).read_text()


ROUTE = {"page": "model", "model": "org/tiny", "tab": "logs"}


# -- routes: tabs -------------------------------------------------------------------
@needs_node
def test_switching_tab_keeps_the_model():
    assert node("routes.js", f"m.tabRoute({json.dumps(ROUTE)}, 'benchmark')") == {"page": "model", "model": "org/tiny", "tab": "benchmark"}


@needs_node
def test_the_tab_is_not_a_crumb_and_the_model_crumb_is_plain_text():
    for tab in ("overview", "benchmark", "logs", "notebook"):
        crumbs = node("routes.js", f"m.crumbsFor({{page:'model',model:'tiny',tab:'{tab}'}})")
        assert [(c["label"], bool(c.get("to"))) for c in crumbs] == [("My models", True), ("tiny", False)], tab


@needs_node
def test_going_between_model_routes_updates_the_page_in_place_everything_else_rebuilds():
    def reuse(a, b):
        return node("routes.js", f"m.updatesInPlace({json.dumps(a)}, {json.dumps(b)})")
    tiny = {"page": "model", "model": "tiny", "tab": "overview"}
    assert reuse(tiny, {**tiny, "tab": "logs"}) is True and reuse(tiny, {**tiny, "model": "other"}) is True
    assert reuse(tiny, {"page": "keys"}) is False and reuse({"page": "keys"}, tiny) is False and reuse(None, tiny) is False


# -- the command bar: the model's verbs only -------------------------------------------------------
@needs_node
@pytest.mark.parametrize("state,want", [("ready", ["stop", "restart", "copy", "rm"]), ("cold", ["start", "copy", "rm"]),
                                         ("crashed", ["restart", "copy", "rm"]), ("starting", ["stop", "copy", "rm"])])
def test_the_command_bar_holds_only_the_models_verbs(state, want):
    cmds = node("model-page.js", f"m.commandsFor('{state}')")
    assert [c["key"] for c in cmds] == want
    assert [c["key"] for c in cmds if c.get("primary")] == [want[0]]  # one filled verb
    assert cmds[-1]["danger"] is True and cmds[-1]["iconOnly"] is True and cmds[-1]["label"] == "Remove"


@needs_node
def test_the_tabs_are_overview_benchmark_logs_notebook_and_security_is_not_one():
    assert node("model-page.js", "m.TAB_DEFS.map((t) => [t.key, t.label])") == [["overview", "Overview"], ["benchmark", "Benchmark"], ["logs", "Logs"], ["notebook", "Notebook"]]
    assert node("routes.js", "m.TABS") == ["overview", "benchmark", "logs", "notebook"]
    js = text("model-page.js")
    assert 'href: "#/security"' in js and "Reachable" in js  # Overview links to the one Security place


# -- "Right now": a regular 2x2 --------------------------------------------------------------------
@needs_node
def test_right_now_is_requests_speed_latency_memory_in_that_order():
    ov = {"tokens": {"tps": 120, "ttft": 38, "requests": 14, "points": []}, "state": "running", "fit": {"thisGb": 2}}
    tiles = node("model-page.js", f"m.metricsView({json.dumps(ov)})")
    assert [(t["label"], t["value"], t["unit"]) for t in tiles] == [("Requests", 14, ""), ("Speed", 120, "tok/s"), ("Latency p50", 38, "ms"), ("Memory", 2, "GB")]
    stopped = {"tokens": {"tps": None, "ttft": None, "requests": 0, "points": []}, "state": "idle", "fit": {"thisGb": 0}}
    assert [t["value"] for t in node("model-page.js", f"m.metricsView({json.dumps(stopped)})")] == [0, None, None, None]


def test_the_metrics_grid_is_two_columns_one_surface_and_no_elevation():
    css = text("model-page.css")
    assert "grid-template-columns: 1fr 1fr" in re.search(r"\.m4 \{([^}]*)\}", css).group(1)
    tile = re.search(r"\.m4 > div \{([^}]*)\}", css).group(1)
    assert "shadow" not in tile and "transform" not in tile
    assert not re.search(r"\.m4[^{]*:nth-child", css)  # no tile is treated differently from the others


# -- flat buttons, the pill, the phone --------------------------------------------------------------
def test_every_button_on_the_model_page_is_flat_and_the_tab_pill_slides():
    css = text("model-page.css")
    assert "box-shadow: none" in re.search(r"\.fb \{([^}]*)\}", css).group(1)
    assert not re.search(r"\.fb[^{]*:hover[^{]*\{[^}]*(shadow|translateY)", css)
    assert re.search(r"\.tabs \.pill \{[^}]*transition: left \.32s", css)
    js = text("model-page.js")
    assert "btn fill" not in js and "btn tonal" not in js and "btn danger" not in js  # the page's own buttons are .fb


def test_the_phone_rules_stack_tabs_over_verbs_and_keep_touch_targets_44px():
    css = text("model-page.css")
    phone = css[css.index("@container (max-width: 760px)"):]
    assert re.search(r"\.mhead \{[^}]*flex-direction: column", phone)
    assert re.search(r"\.sec \{[^}]*grid-template-columns: 1fr;", phone)
    for target in (r"\.tabs button \{[^}]*height: 44px", r"\.cmdbar \.fb \{[^}]*height: 44px", r"\.cmdbar \.fb\.ic \{[^}]*width: 44px", r"\.cmdbar \.fb\.cp \{[^}]*width: 44px"):
        assert re.search(target, phone), target  # touch targets are 44 px on a phone; desktop .fb stays 36
    assert "height: 36px" in re.search(r"\.fb \{([^}]*)\}", css).group(1)
    assert css.count("{") == css.count("}")


def test_numbers_morph_in_220ms():
    assert "MORPH_MS = 220" in text("model-page.js")  # the one timing the design names; the behaviour needs a real browser (checked live)


def test_the_new_files_are_served_the_blade_menu_is_gone_and_the_page_has_no_back_button():
    assert {"model-page.js", "model-page.css"} <= CONSOLE_ASSETS and "blade-model.js" not in CONSOLE_ASSETS
    assert "rmenu" not in text("model-page.js")
    pages = text("pages.js")
    assert 'blade.header.prepend(backButton({ page: "home" }))' in pages and 'tab !== "overview"' not in pages  # only Add keeps Back
