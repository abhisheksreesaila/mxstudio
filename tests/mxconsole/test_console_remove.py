"""F-062: Remove (one model, or the ticked ones) behind an accessible confirm. Run under node on the tiny fake DOM:
what a person gets from the keyboard and screen reader, and what the daemon is asked."""
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
      const toastEl = document.createElement("div");
      document.getElementById = (id) => (id === "toast" ? toastEl : null);
      const calls = [];
      globalThis.fetch = async (path, init) => {{
        calls.push([init.method, path]);
        const bad = path.endsWith("/broken");
        return {{ ok: !bad, status: bad ? 500 : 200, headers: {{ get: () => "application/json" }},
                 json: async () => (bad ? {{ error: {{ message: "disk is read-only" }} }} : []) }};
      }};
      const rm = await import({json.dumps((CONSOLE_DIR / 'remove.js').as_uri())});
      const settle = () => new Promise((r) => setTimeout(r, 0));
      {body}
    """
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_the_confirm_says_the_model_stops_leaves_my_models_and_the_weights_stay():
    one, many = run('console.log(JSON.stringify([rm.removeCopy(["tiny"]), rm.removeCopy(["a", "b", "c"])]));')
    assert one == {"title": "Remove tiny?", "confirm": "Remove",
                   "body": "It stops, leaves My models and its compiled graphs are deleted. The downloaded weights stay, so adding it back is quick. Same as mxc rm."}
    assert many == {"title": "Remove 3 models?", "confirm": "Remove 3",
                    "body": "They stop, leave My models and their compiled graphs are deleted. The downloaded weights stay, so adding them back is quick. Same as mxc rm."}


OPEN = """
  const opener = document.createElement("button"); document.body.append(opener); opener.focus();
  let answer = "pending";
  rm.confirmDialog(rm.removeCopy(["tiny"])).then((v) => { answer = v; });
  const dlg = () => document.body.querySelector('[role="dialog"]');
  const btn = (act) => document.body.querySelector(`[data-act="${act}"]`);
  const byId = (id) => document.body.querySelector(`[id="${id}"]`);
"""


def test_the_dialog_is_labelled_takes_focus_in_and_cancel_is_the_safe_default():
    got = run(OPEN + """
      console.log(JSON.stringify({ modal: dlg().getAttribute("aria-modal"), title: byId(dlg().getAttribute("aria-labelledby")).textContent,
        described: byId(dlg().getAttribute("aria-describedby")).textContent.startsWith("It stops"), focus: document.activeElement.getAttribute("data-act") }));
    """)
    assert got == {"modal": "true", "title": "Remove tiny?", "described": True, "focus": "cancel"}


def test_escape_cancels_and_puts_focus_back_where_it_was():
    got = run(OPEN + """
      document.activeElement.key("Escape"); await settle();
      console.log(JSON.stringify({ answer, open: !!dlg(), back: document.activeElement === opener }));
    """)
    assert got == {"answer": False, "open": False, "back": True}


def test_cancel_and_the_backdrop_say_no_and_confirm_says_yes():
    assert run(OPEN + 'btn("cancel").click(); await settle(); console.log(JSON.stringify([answer, !!dlg()]));') == [False, False]
    assert run(OPEN + 'document.body.querySelector(".confirm").click(); await settle(); console.log(JSON.stringify(answer));') is False
    assert run(OPEN + 'btn("ok").click(); await settle(); console.log(JSON.stringify([answer, !!dlg(), document.activeElement === opener]));') == [True, False, True]


def test_a_click_inside_the_box_does_not_dismiss_it():
    assert run(OPEN + 'dlg().click(); await settle(); console.log(JSON.stringify([answer, !!dlg()]));') == ["pending", True]


def test_tab_stays_inside_the_dialog():
    got = run(OPEN + """
      const at = [];
      btn("ok").focus(); const fwd = btn("ok").key("Tab"); at.push([fwd.defaultPrevented, document.activeElement.getAttribute("data-act")]);
      const back = btn("cancel").key("Tab", { shiftKey: true }); at.push([back.defaultPrevented, document.activeElement.getAttribute("data-act")]);
      btn("cancel").focus(); const mid = btn("cancel").key("Tab"); at.push([mid.defaultPrevented]);  // Cancel to Remove is the browser's own move
      console.log(JSON.stringify(at));
    """)
    assert got == [[True, "cancel"], [True, "ok"], [False]]


def test_removing_calls_the_remove_route_per_model_and_reports_what_it_did():
    got = run("""
      const p = rm.removeModels(["org/m", "c"]);
      await settle();
      document.body.querySelector('[data-act="ok"]').click();
      const done = await p;
      console.log(JSON.stringify({ done, calls: calls.filter((c) => c[0] === "DELETE"), toast: toastEl.textContent }));
    """)
    assert got == {"done": ["org/m", "c"], "calls": [["DELETE", "/admin/models/org%2Fm"], ["DELETE", "/admin/models/c"]], "toast": "Removed 2 models · $ mxc rm org/m c"}


def test_a_refused_removal_is_reported_and_the_others_still_go():
    got = run("""
      const p = rm.removeModels(["broken", "fine"]);
      await settle();
      document.body.querySelector('[data-act="ok"]').click();
      const done = await p;
      console.log(JSON.stringify({ done, toast: toastEl.textContent }));
    """)
    assert got == {"done": ["fine"], "toast": "Could not remove broken: disk is read-only"}


PAGE = """
  const mp = await import(%s);
  location.hash = "#/models/tiny/overview";
  const p = mp.removeThenHome("tiny");
  await settle();
  const press = (act) => document.body.querySelector(`[data-act="${act}"]`).click();
"""


def test_removing_from_a_model_page_lands_on_my_models_after_confirming():
    got = run(PAGE % json.dumps((CONSOLE_DIR / "model-page.js").as_uri()) + """
      const before = location.hash;
      press("ok");
      console.log(JSON.stringify({ before, went: await p, after: location.hash, calls: calls.filter((c) => c[0] === "DELETE") }));
    """)
    assert got == {"before": "#/models/tiny/overview", "went": True, "after": "#/", "calls": [["DELETE", "/admin/models/tiny"]]}


def test_cancelling_a_model_pages_remove_keeps_you_on_the_model_and_removes_nothing():
    got = run(PAGE % json.dumps((CONSOLE_DIR / "model-page.js").as_uri()) + """
      press("cancel");
      console.log(JSON.stringify({ went: await p, after: location.hash, calls: calls.filter((c) => c[0] === "DELETE") }));
    """)
    assert got == {"went": False, "after": "#/models/tiny/overview", "calls": []}


def test_saying_no_removes_nothing():
    got = run("""
      const p = rm.removeModels(["a"]);
      await settle();
      document.body.querySelector('[data-act="cancel"]').click();
      console.log(JSON.stringify({ done: await p, calls: calls.length }));
    """)
    assert got == {"done": [], "calls": 0}
