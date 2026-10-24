"""F-053 review fixes: the model page's tabs and the tab content (the picker moved to test_console_switcher.py), run for real under node on a tiny fake DOM
(tests/mxconsole/fake_dom.mjs): keyboard, focus, ARIA and teardown, asserted on what a person using them would get."""
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
      const core = await import({json.dumps((CONSOLE_DIR / 'core.js').as_uri())});
      const mp = await import({json.dumps((CONSOLE_DIR / 'model-page.js').as_uri())});
      {body}
    """
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


# -- tabs: roving tabindex, arrow keys, ARIA ---------------------------------------------------
TABS = """
  const picked = [];
  const t = mp.tabList(mp.TAB_DEFS, { prefix: "m", onPick: (k) => picked.push(k) });
  document.body.append(t.el);
  const state = () => mp.TAB_DEFS.map((d) => { const b = t.buttons.get(d.key); return [b.getAttribute("tabindex"), b.getAttribute("aria-selected")]; });
"""


def test_only_the_selected_tab_is_a_tab_stop_and_it_is_aria_selected():
    got = run(TABS + 't.select("logs"); console.log(JSON.stringify(state()));')
    assert got == [["-1", "false"], ["-1", "false"], ["0", "true"], ["-1", "false"]]


def test_arrow_keys_home_and_end_move_and_activate_the_tab_and_wrap():
    got = run(TABS + """
      t.select("overview");
      const seq = [];
      for (const k of ["ArrowRight", "ArrowRight", "End", "ArrowRight", "ArrowLeft", "Home"]) {
        const sel = mp.TAB_DEFS.map((d) => d.key).find((key) => t.buttons.get(key).getAttribute("tabindex") === "0");
        const e = t.buttons.get(sel).key(k);
        seq.push([k, picked.at(-1), document.activeElement.getAttribute("data-tab"), e.defaultPrevented]);
      }
      console.log(JSON.stringify(seq));
    """)
    assert got == [["ArrowRight", "benchmark", "benchmark", True], ["ArrowRight", "logs", "logs", True], ["End", "notebook", "notebook", True],
                   ["ArrowRight", "overview", "overview", True], ["ArrowLeft", "notebook", "notebook", True], ["Home", "overview", "overview", True]]


def test_other_keys_do_nothing_and_each_tab_points_at_the_one_panel_it_labels():
    got = run(TABS + """
      t.select("overview");
      const e = t.buttons.get("overview").key("a");
      const b = t.buttons.get("logs");
      console.log(JSON.stringify({ picked, prevented: e.defaultPrevented, role: b.getAttribute("role"), controls: b.getAttribute("aria-controls"), id: b.getAttribute("id"),
        list: t.el.getAttribute("role"), keys: ["x", "ArrowDown"].map((k) => mp.tabKeyTarget(k, ["a", "b"], "a")) }));
    """)
    assert got == {"picked": [], "prevented": False, "role": "tab", "controls": "m-panel", "id": "m-tab-logs", "list": "tablist", "keys": [None, None]}


def test_tab_key_target_is_pure():
    keys = ["a", "b", "c"]
    for key, cur, want in [("ArrowRight", "a", "b"), ("ArrowRight", "c", "a"), ("ArrowLeft", "a", "c"), ("Home", "c", "a"), ("End", "a", "c")]:
        assert run(f"console.log(JSON.stringify(mp.tabKeyTarget({key!r}, {keys!r}, {cur!r})));") == want


# -- the tab content: leaving a tab or a model tears the old one down -----------------------------
def test_leaving_a_tab_or_switching_model_closes_the_old_tab_content_exactly_once():
    got = run("""
      const log = [];
      const build = (bladeName, name) => {
        const blade = { alive: true, el: document.createElement("section"), body: document.createElement("div"), onClose: () => log.push(`close ${bladeName}:${name}`) };
        return { blade, def: { mount: () => log.push(`mount ${bladeName}:${name}`) } };
      };
      const host = document.createElement("div");
      const sh = mp.subHost(host, build);
      sh.show("logs", "a"); sh.show("notebook", "a"); sh.show("notebook", "b"); sh.show(null, "b"); sh.close(); sh.close();
      console.log(JSON.stringify({ log, children: host.children.length }));
    """)
    assert got == {"log": ["mount logs:a", "close logs:a", "mount notebook:a", "close notebook:a", "mount notebook:b", "close notebook:b"], "children": 0}


def test_the_closed_tab_content_is_marked_not_alive_so_its_pollers_stop():
    assert run("""
      const blade = { alive: true, el: document.createElement("section"), body: document.createElement("div") };
      const sh = mp.subHost(document.createElement("div"), () => ({ blade, def: {} }));
      sh.show("logs", "a"); sh.close();
      console.log(JSON.stringify(blade.alive));
    """) is False


def test_the_remove_button_says_what_a_second_click_does():
    assert run('console.log(JSON.stringify([mp.removeLabel("tiny", false), mp.removeLabel("tiny", true)]));') == ["Remove", "Click again to remove tiny"]
