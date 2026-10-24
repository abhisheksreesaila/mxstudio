"""F-053 review fixes: the model page's tabs, the picker and the tab content, run for real under node on a tiny fake DOM
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
      const pk = await import({json.dumps((CONSOLE_DIR / 'picker.js').as_uri())});
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


# -- the picker ---------------------------------------------------------------------------------
PICKER = """
  core.store.models = [{ name: "tiny", state: "ready" }, { name: "other", state: "cold" }];
  const anchor = document.createElement("button"); document.body.append(anchor);
  const app = document.createElement("div"); app.classList.add("app"); document.body.append(app);
  document.querySelector = (s) => (s === ".app" ? app : null);
  let route = { page: "model", model: "tiny", tab: "logs" };
  const routeListeners = new Set(), went = [];
  const env = { currentRoute: () => route, go: (r) => went.push(r), onRoute: (fn) => { routeListeners.add(fn); fn(route); return () => routeListeners.delete(fn); } };
  const panel = () => app.querySelector(".pmenu");
  pk.openPicker(anchor, env);
"""


def test_the_picker_is_a_modal_list_that_marks_the_current_model():
    got = run(PICKER + """
      console.log(JSON.stringify({ modal: panel().getAttribute("aria-modal"), role: panel().getAttribute("role"),
        rows: panel().querySelectorAll(".pr").map((r) => [r.getAttribute("data-pick"), r.getAttribute("aria-selected")]), focused: document.activeElement.getAttribute("data-pick") }));
    """)
    assert got == {"modal": "true", "role": "dialog", "rows": [["tiny", "true"], ["other", "false"]], "focused": "tiny"}


def test_picking_a_model_keeps_the_tab_and_returns_focus_to_the_crumb():
    got = run(PICKER + """
      panel().querySelector('[data-pick="other"]').click();
      console.log(JSON.stringify({ went, focus: document.activeElement === anchor }));
    """)
    assert got == {"went": [{"page": "model", "model": "other", "tab": "logs"}], "focus": True}


def test_tab_stays_inside_the_picker_and_escape_closes_it():
    got = run(PICKER + """
      const seen = [];
      for (let i = 0; i < 3; i++) { const e = document.key("Tab"); seen.push([document.activeElement.getAttribute("data-pick"), e.defaultPrevented]); }
      const e2 = document.key("Escape");
      console.log(JSON.stringify({ seen, escaped: e2.defaultPrevented, focus: document.activeElement === anchor }));
    """)
    assert got["seen"] == [["other", True], ["tiny", True], ["other", True]] and got["escaped"] is True and got["focus"] is True


def test_a_route_change_closes_the_picker_and_it_leaves_no_listeners_behind():
    got = run(PICKER + """
      const listening = routeListeners.size;
      route = { page: "model", model: "tiny", tab: "benchmark" };
      routeListeners.forEach((fn) => fn(route));
      const after = { listeners: routeListeners.size, focus: document.activeElement === anchor };
      const e = document.key("Escape");
      console.log(JSON.stringify({ listening, after, escapeIgnored: e.defaultPrevented }));
    """)
    assert got == {"listening": 1, "after": {"listeners": 0, "focus": True}, "escapeIgnored": False}


def test_the_picker_is_a_sheet_when_the_page_is_760px_or_narrower_the_same_cut_as_the_page_css():
    assert run("console.log(JSON.stringify([pk.usesSheet(390), pk.usesSheet(760), pk.usesSheet(761), pk.usesSheet(1068)]));") == [True, True, False, False]
