"""F-056 (the switcher went in F-061): labels on a model's Overview, and the library's groups column. Run for real under node on the tiny
fake DOM (tests/mxconsole/fake_dom.mjs); every assert is what a person would see or get."""
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
      const lib = await import({json.dumps((CONSOLE_DIR / 'library.js').as_uri())});
      {body}
    """
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


SETUP = """
  core.store.models = [
    { name: "alpha", state: "ready", kind: "chat", labels: ["work"] }, { name: "beta", state: "cold", kind: "coder", labels: [] },
    { name: "gamma", state: "cold", kind: "chat", labels: [] }, { name: "delta", state: "cold", kind: "vision", labels: ["work"] },
  ];
  const posts = [];
  globalThis.fetch = async (url, init = {}) => {
    if (init.method === "POST") posts.push([url, JSON.parse(init.body)]);
    const data = url === "/admin/labels" ? [{ name: "work" }, { name: "later" }] : [];
    return { ok: true, status: 200, headers: { get: () => "application/json" }, json: async () => data, text: async () => "" };
  };
  const toastEl = document.createElement("div"); document.getElementById = () => toastEl;
  const tick = () => new Promise((r) => setTimeout(r, 0));
"""


def test_the_groups_column_has_no_recent_group():
    got = run(SETUP + """
      const l = lib.createLibrary({ onChange() {} });
      l.update();
      console.log(JSON.stringify(l.groups.querySelectorAll(".gi").map((g) => g.getAttribute("data-group"))));
    """)
    assert got[:2] == ["all", "running"] and "recent" not in got and "kind:chat" in got


# -- "+ label" on a model's Overview -----------------------------------------------------------------
def test_plus_label_adds_a_new_label_to_the_model_at_once_and_asks_the_daemon():
    got = run(SETUP + """
      const ed = lib.labelsEditor(() => "beta");
      document.body.append(ed);
      const before = ed.textContent;
      ed.querySelector('[data-act="add-label"]').click();
      await tick();
      const field = ed.querySelector("input");
      field.value = "Night Shift";
      field.key("Enter");
      ed.update();
      const at = ed.querySelectorAll(".tag.lab").map((t) => t.getAttribute("data-lab"));
      await tick(); await tick();
      console.log(JSON.stringify({ before, at, posts, suggestions: 2 }));
    """)
    assert got["before"] == "label" and got["at"] == ["night-shift"]
    assert got["posts"] == [["/admin/labels/night-shift/add", {"models": ["beta"]}]]


def test_the_label_field_stays_open_while_your_existing_labels_load_as_suggestions():
    """Live check caught this: the suggestions arrived, the field was rebuilt, and its blur closed it."""
    got = run(SETUP + """
      const ed = lib.labelsEditor(() => "beta");
      ed.querySelector('[data-act="add-label"]').click();
      const first = ed.querySelector("input");
      await tick(); await tick();
      console.log(JSON.stringify({ same: ed.querySelector("input") === first, suggestions: ed.querySelectorAll("option").map((o) => o.getAttribute("value")) }));
    """)
    assert got == {"same": True, "suggestions": ["work", "later"]}


def test_removing_a_label_takes_it_off_and_a_refusal_puts_it_back_with_the_reason():
    got = run(SETUP + """
      globalThis.fetch = async (url, init = {}) => ({ ok: false, status: 409, headers: { get: () => "application/json" }, json: async () => ({ error: { message: "Label is locked" } }) });
      const ed = lib.labelsEditor(() => "alpha");
      const shown = () => ed.querySelectorAll(".tag.lab").map((t) => t.getAttribute("data-lab"));
      const start = shown();
      const pending = lib.setModelLabel("alpha", "work", false);
      ed.update();
      const during = shown();
      await pending; ed.update();
      console.log(JSON.stringify({ start, during, after: shown(), stored: core.store.models[0].labels, said: toastEl.textContent }));
    """)
    assert got == {"start": ["work"], "during": [], "after": ["work"], "stored": ["work"], "said": "Label is locked"}
