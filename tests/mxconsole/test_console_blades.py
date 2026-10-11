"""F-023: the blades shell and My models. Pure helpers run under node; the rest checks what ships."""
import json
import re
import shutil
import subprocess

import pytest
from test_gateway import http, key, store, sup  # noqa: F401  (shared fixtures)

from mxconsole.gateway import CONSOLE_ASSETS, CONSOLE_DIR

STUBS = ["model", "add", "security", "keys", "bench", "logs", "notebook", "gateway", "settings"]
NEW_JS = ["blades.js", "blade-layout.js", "prefs.js", "hardware.js", "topbar.js", "blade-home.js", *[f"blade-{n}.js" for n in STUBS]]
NEW_CSS = ["blades.css", "topbar.css", "blade-home.css", *[f"blade-{n}.css" for n in STUBS]]
needs_node = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")


def node(expr: str, module: str):
    script = f"""
      import * as m from {json.dumps((CONSOLE_DIR / module).as_uri())};
      console.log(JSON.stringify({expr}) ?? "null");
    """
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


# -- what ships ---------------------------------------------------------------------------------
async def test_new_files_are_allowlisted_and_served(http):
    for name in NEW_JS + NEW_CSS:
        assert name in CONSOLE_ASSETS, name
        r = await http.get(f"/console/{name}")
        assert r.status_code == 200, name


async def test_the_page_links_every_stylesheet_and_loads_the_blade_engine(http):
    page = (await http.get("/console")).text
    for name in NEW_CSS:
        assert f'href="/console/{name}"' in page, name
    assert 'src="/console/main.js"' in page and 'id="journey"' in page and 'id="topbar"' in page
    assert 'id="menu"' not in page  # no left menu any more


def test_braces_balance_in_every_stylesheet():
    for path in sorted(CONSOLE_DIR.glob("*.css")):
        css = path.read_text()
        assert css.count("{") == css.count("}"), path.name
        depth = 0
        for line in css.splitlines():
            depth += line.count("{") - line.count("}")
            assert depth >= 0, path.name


def test_themes_are_tokens_light_and_dark_and_dark_follows_the_system():
    css = (CONSOLE_DIR / "blades.css").read_text()
    assert "prefers-color-scheme: dark" in css and ':root:not([data-theme="light"])' in css and ':root[data-theme="dark"]' in css
    for token in ("--chrome", "--card", "--canvas", "--text", "--accent", "--bad", "--hue-models", "--hue-keys", "--hue-bench", "--hue-gateway"):
        assert token in css, token


def test_each_stub_blade_registers_itself_with_its_own_name():
    for n in STUBS:
        text = (CONSOLE_DIR / f"blade-{n}.js").read_text()
        assert f'register("{n}"' in text, n
    main = (CONSOLE_DIR / "main.js").read_text()
    for n in STUBS + ["home"]:
        assert f'./blade-{n}.js' in main, n


def test_the_new_code_writes_no_html_and_no_inline_style():
    banned = re.compile(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|setAttribute\(\s*['\"]style['\"]")
    for name in NEW_JS:
        assert not banned.search((CONSOLE_DIR / name).read_text()), name


# -- the blade layout math ----------------------------------------------------------------------
def plan(specs: str, width: int):
    return node(f"m.planLayout({specs}, {width})", "blade-layout.js")


@needs_node
def test_a_single_blade_takes_the_whole_width():
    assert plan("[{side:300,min:0}]", 1440) == ["full"]


@needs_node
def test_the_newest_blade_is_full_and_the_one_before_it_sits_beside_it_when_there_is_room():
    specs = "[{side:300,min:0},{side:660,narrow:470,min:620}]"
    assert plan(specs, 1440) == ["side", "full"]


@needs_node
def test_older_blades_fold_into_rails_and_only_the_previous_one_stays_open():
    specs = "[{side:300,min:0},{side:660,narrow:470,min:620},{side:600,min:520}]"
    assert plan(specs, 1600) == ["rail", "side", "full"]


@needs_node
def test_a_blade_that_does_not_fit_beside_the_newest_folds_into_a_rail():
    specs = "[{side:300,min:0},{side:660,narrow:470,min:620}]"
    assert plan(specs, 900) == ["rail", "full"]


@needs_node
def test_a_blade_with_a_narrow_form_shrinks_before_it_folds():
    specs = "[{side:660,narrow:470,min:620},{side:600,min:520}]"
    assert plan(specs, 1000) == ["narrow", "full"]
    assert plan(specs, 1300) == ["side", "full"]
    assert plan(specs, 1050 - 400) == ["hidden", "full"]  # phone width
    assert plan(specs, 870) == ["rail", "full"]


@needs_node
def test_a_wide_blade_folds_everything_left_of_it():
    specs = "[{side:300,min:0},{side:660,min:620},{wide:true,min:900}]"
    assert plan(specs, 2400) == ["rail", "rail", "full"]


@needs_node
def test_on_a_phone_only_the_newest_blade_shows():
    specs = "[{side:300,min:0},{side:660,min:620}]"
    assert plan(specs, 800) == ["hidden", "full"]
    assert plan("[{side:300,min:0}]", 390) == ["full"]
    assert node("m.planLayout([], 1200)", "blade-layout.js") == []


@needs_node
def test_closing_back_to_a_blade_keeps_it_and_drops_the_rest():
    assert node("m.keepFirst(['a','b','c'], 2)", "blade-layout.js") == ["a", "b"]
    assert node("m.keepFirst(['a','b','c'], 0)", "blade-layout.js") == ["a"]  # the home blade never closes
    assert node("m.keepFirst(['a'], 5)", "blade-layout.js") == ["a"]


# -- preferences --------------------------------------------------------------------------------
STORE = "{m: {}, getItem(k){return k in this.m ? this.m[k] : null}, setItem(k,v){this.m[k]=String(v)}}"


@needs_node
def test_the_list_or_cards_choice_is_remembered_and_defaults_to_list():
    assert node(f"(() => {{ const s = {STORE}; return [m.readView(s), (m.writeView(s, 'cards'), m.readView(s))]; }})()", "prefs.js") == ["list", "cards"]
    assert node("m.readView({getItem(){return 'weird'}})", "prefs.js") == "list"
    assert node("m.readView({getItem(){throw new Error('blocked')}})", "prefs.js") == "list"
    assert node("m.writeView({setItem(){throw new Error('blocked')}}, 'cards')", "prefs.js") is None  # never throws


@needs_node
def test_the_theme_follows_the_system_until_one_is_chosen():
    assert node("[m.resolveTheme(null, true), m.resolveTheme(null, false), m.resolveTheme('light', true), m.resolveTheme('dark', false)]", "prefs.js") == [
        "dark", "light", "light", "dark"]
    assert node("[m.nextTheme('dark'), m.nextTheme('light')]", "prefs.js") == ["light", "dark"]
    assert node(f"(() => {{ const s = {STORE}; return [m.readTheme(s), (m.writeTheme(s, 'dark'), m.readTheme(s))]; }})()", "prefs.js") == [None, "dark"]
    assert node("m.readTheme({getItem(){return 'contrast'}})", "prefs.js") is None  # the old three-way value is ignored
    assert node("m.readTheme({getItem(){throw new Error('blocked')}})", "prefs.js") is None


# -- the hardware chip --------------------------------------------------------------------------
@needs_node
def test_the_hardware_chip_shows_vram_per_running_model():
    gpus = "[{index:0,name:'NVIDIA GeForce RTX 4090',used_mb:6144,total_mb:24576,util:3}]"
    crew = "[{id:'daemon'},{id:'model:a',gpu_mb:4096},{id:'model:b',gpu_mb:1024},{id:'caddy'}]"
    v = node(f"m.hardwareView({gpus}, {crew})", "hardware.js")
    assert v["present"] and v["name"] == "NVIDIA GeForce RTX 4090" and v["short"] == "RTX 4090"
    assert v["usedGb"] == 6 and v["totalGb"] == 24 and v["freeGb"] == 18
    assert [(s["name"], s["gb"]) for s in v["segments"]] == [("a", 4), ("b", 1), ("other", 1)]
    assert abs(sum(s["pct"] for s in v["segments"]) - 25) < 0.01


@needs_node
def test_without_per_model_numbers_the_bar_is_one_used_segment_and_without_a_gpu_it_says_so():
    one = node("m.hardwareView([{index:0,name:'X',used_mb:2048,total_mb:8192}], null)", "hardware.js")
    assert [(s["name"], s["gb"]) for s in one["segments"]] == [("in use", 2)]
    none = node("m.hardwareView([], [])", "hardware.js")
    assert none["present"] is False and none["label"] == "no GPU"
    assert node("m.hardwareView(undefined, undefined)", "hardware.js")["present"] is False


# -- the My models rows -------------------------------------------------------------------------
ROW = "{name:'qwen',repo_id:'Qwen/Q',architecture:'Qwen2ForCausalLM',state:'ready',port:9001,in_flight:0}"


@needs_node
def test_a_running_model_row_has_live_tokens_and_local_access():
    ctx = "{metrics:{by_model:{qwen:{decode_tps_p50:210.4,ttft_p50_ms:38,requests:12}}}, series:{models:{qwen:[{decode_tps_p50:200},{decode_tps_p50:null},{decode_tps_p50:215.6}]}}, info:{lan_endpoint:null}, system:null, endpoint:'http://127.0.0.1:11435/v1'}"
    v = node(f"m.modelView({ROW}, {ctx})", "blade-home.js")
    assert v["stateKey"] == "running" and v["stateLabel"] == "Running" and v["access"] == "local"
    assert v["tps"] == 216 and v["points"] == [200, 215.6] and v["ttft"] == 38 and v["requests"] == 12
    assert v["arch"] == "Qwen2" and v["crashed"] is False


@needs_node
def test_access_is_secure_when_the_lan_endpoint_is_open_and_idle_and_crashed_are_called_out():
    ctx = "{metrics:null, series:null, info:{lan_endpoint:'https://192.168.1.4:8443/v1'}, system:null, endpoint:'http://127.0.0.1:11435/v1'}"
    idle = node(f"m.modelView({{name:'a',state:'cold',architecture:'LlamaForCausalLM'}}, {ctx})", "blade-home.js")
    assert idle["stateKey"] == "idle" and idle["access"] == "secure" and idle["tps"] is None and idle["endpoint"].startswith("https://")
    crashed = node(f"m.modelView({{name:'a',state:'crashed'}}, {ctx})", "blade-home.js")
    assert crashed["crashed"] is True and crashed["stateLabel"] == "Crashed"
    starting = node(f"m.modelView({{name:'a',state:'starting',starting_seconds:12}}, {ctx})", "blade-home.js")
    assert starting["stateKey"] == "starting" and "12" in starting["note"]


@needs_node
def test_the_list_is_filtered_by_name_or_repo():
    rows = "[{name:'qwen',repo_id:'Qwen/Q'},{name:'gemma',repo_id:'google/gemma-3'}]"
    assert node(f"m.filterModels({rows}, 'GOOG').map(r => r.name)", "blade-home.js") == ["gemma"]
    assert node(f"m.filterModels({rows}, '  ').length", "blade-home.js") == 2
