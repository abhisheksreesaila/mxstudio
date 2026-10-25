"""F-062: My models as a list or cards. The numbers each column shows (pure, from what the daemon already measures),
the List/Cards toggle, a card, and the tick bar's Remove, run under node on the tiny fake DOM."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from mxconsole.gateway import CONSOLE_DIR

pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
FAKE = Path(__file__).with_name("fake_dom.mjs").as_uri()


def run(body: str):
    script = f"""
      import {json.dumps(FAKE)};
      const store = new Map();
      globalThis.localStorage = {{ getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, String(v)) }};
      window.localStorage = globalThis.localStorage;
      globalThis.fetch = async (path, init) => ({{ ok: true, status: 200, headers: {{ get: () => "application/json" }}, json: async () => [] }});
      const home = await import({json.dumps((CONSOLE_DIR / 'blade-home.js').as_uri())});
      const lib = await import({json.dumps((CONSOLE_DIR / 'library.js').as_uri())});
      const core = await import({json.dumps((CONSOLE_DIR / 'core.js').as_uri())});
      const prefs = await import({json.dumps((CONSOLE_DIR / 'prefs.js').as_uri())});
      const settle = () => new Promise((r) => setTimeout(r, 0));
      {body}
    """
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


CTX = """{metrics:{by_model:{qwen:{decode_tps_p50:210.4,ttft_p50_ms:38.2,requests:1284}, idle:{requests:3}}}, series:null,
  info:{lan_endpoint:'https://192.168.1.4:8443/v1'}, system:[{id:'model:qwen',vram_gb:3.04}], endpoint:'http://127.0.0.1:11435/v1'}"""


def cells(row: str):
    return run(f"console.log(JSON.stringify(home.cellsFor(home.modelView({row}, {CTX}))));")


def test_a_running_model_shows_every_number_the_daemon_measures():
    got = cells("{name:'qwen',repo_id:'Qwen/Q',state:'ready',size_bytes:1288490189,kind:'chat',labels:[]}")
    assert got == {"size": "1.3 GB", "memory": "3 GB", "tps": "210", "ttft": "38 ms", "requests": "1,284", "access": "Secure · LAN"}


def test_a_model_with_no_measurements_shows_dashes_not_zeros():
    got = cells("{name:'new',repo_id:'o/new',state:'cold',kind:'chat',labels:[]}")
    assert got == {"size": "–", "memory": "–", "tps": "–", "ttft": "–", "requests": "–", "access": "Secure · LAN"}


def test_an_idle_model_still_shows_requests_it_served_in_the_window_and_small_sizes_are_in_mb():
    got = cells("{name:'idle',state:'cold',size_bytes:360000000,kind:'chat',labels:[]}")
    assert got["requests"] == "3" and got["size"] == "360 MB"


def test_access_says_this_machine_when_the_lan_is_not_open():
    got = run("""
      const v = home.modelView({name:'a',state:'ready'}, {metrics:null, series:null, info:{lan_endpoint:null}, system:null, endpoint:'x'});
      console.log(JSON.stringify(home.cellsFor(v).access));""")
    assert got == "This machine"


def test_the_toggle_marks_the_current_view_calls_back_and_remembers_it():
    got = run("""
      const picked = [];
      const t = home.viewToggle("list", (v) => picked.push(v));
      const b = (v) => t.querySelector(`[data-view="${v}"]`);
      const state = () => ["list", "cards"].map((v) => b(v).getAttribute("aria-pressed"));
      const first = state();
      b("cards").click();
      console.log(JSON.stringify({ first, after: state(), picked, remembered: prefs.readView(prefs.safeStorage()) }));""")
    assert got == {"first": ["true", "false"], "after": ["false", "true"], "picked": ["cards"], "remembered": "cards"}


def test_a_card_has_the_name_state_speed_memory_requests_kind_and_labels_and_opens_the_model():
    got = run(f"""
      const opened = [];
      const v = home.modelView({{name:'qwen',repo_id:'Qwen/Q',state:'ready',kind:'coder',labels:['work']}}, {CTX});
      const card = home.cardEl(v, {{ ticked: new Set(["qwen"]), tick: () => {{}}, draggable: () => {{}} }}, (n) => opened.push(n));
      card.click();
      console.log(JSON.stringify({{ text: card.textContent, selected: card.classList.contains("sel"), opened }}));""")
    assert got == {"text": "qwenRunningtok/s210Memory3 GBRequests1,284Coder#work", "selected": True, "opened": ["qwen"]}


def test_ticking_a_card_selects_it_without_opening_the_model():
    got = run(f"""
      const opened = [], ticks = [];
      const v = home.modelView({{name:'qwen',state:'cold'}}, {CTX});
      const card = home.cardEl(v, {{ ticked: new Set(), tick: (n) => ticks.push(n), draggable: () => {{}} }}, (n) => opened.push(n));
      card.querySelector('[data-act="tick"]').click();
      console.log(JSON.stringify({{ opened, ticks }}));""")
    assert got == {"opened": [], "ticks": ["qwen"]}


def test_the_tick_bar_offers_remove_with_the_count_and_hands_over_the_ticked_names():
    got = run("""
      core.store.models = [{name:"a",state:"cold",kind:"chat",labels:[]},{name:"b",state:"cold",kind:"chat",labels:[]},{name:"c",state:"cold",kind:"chat",labels:[]}];
      const asked = [];
      const l = lib.createLibrary({ onChange: () => {}, onRemove: (names) => asked.push(names) });
      l.tick("a"); l.tick("c");
      const rm = l.bar.querySelector('[data-act="remove-ticked"]');
      rm.click();
      console.log(JSON.stringify({ label: rm.textContent, asked }));""")
    assert got == {"label": "Remove 2", "asked": [["a", "c"]]}


def test_a_poll_does_not_rebuild_the_tick_bar_so_focus_on_remove_survives_it():
    got = run("""
      core.store.models = [{name:"a",state:"cold",kind:"chat",labels:[]},{name:"b",state:"cold",kind:"chat",labels:[]}];
      const l = lib.createLibrary({ onChange: () => {}, onRemove: () => {} });
      l.tick("a");
      const before = l.bar.querySelector('[data-act="remove-ticked"]');
      before.focus();
      l.update();  // what every poll does
      const same = l.bar.querySelector('[data-act="remove-ticked"]') === before;
      l.tick("b");
      console.log(JSON.stringify({ same, relabelled: l.bar.querySelector('[data-act="remove-ticked"]').textContent }));""")
    assert got == {"same": True, "relabelled": "Remove 2"}


def test_without_a_remove_handler_the_bar_has_no_remove():
    got = run("""
      core.store.models = [{name:"a",state:"cold",kind:"chat",labels:[]}];
      const l = lib.createLibrary({ onChange: () => {} });
      l.tick("a");
      console.log(JSON.stringify(l.bar.querySelector('[data-act="remove-ticked"]')));""")
    assert got is None
