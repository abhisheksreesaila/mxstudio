"""F-055: the library's search, groups and labelling rules (pure, run under node) and what ships."""
import json
import re
import shutil
import subprocess

import pytest
from test_gateway import http, key, store, sup  # noqa: F401  (shared fixtures)

from mxconsole.gateway import CONSOLE_ASSETS, CONSOLE_DIR

needs_node = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")

M = json.dumps([
    {"name": "qwen2.5-0.5b", "state": "ready", "kind": "chat", "labels": ["work", "favorites"]},
    {"name": "qwen2.5-coder-1.5b", "state": "cold", "kind": "coder", "labels": ["work"]},
    {"name": "llama-3.2-1b", "state": "crashed", "kind": "chat", "labels": []},
    {"name": "bge-small", "state": "cold", "kind": "embeddings", "labels": ["experiments"]},
    {"name": "smollm3-3b", "state": "ready", "kind": "chat", "labels": ["experiments"]},
])


def run_node(body: str):
    script = f"""
      import * as m from {json.dumps((CONSOLE_DIR / 'library-logic.js').as_uri())};
      const M = {M};
      {body}
    """
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def node(expr: str):
    return run_node(f"console.log(JSON.stringify({expr}) ?? 'null');")


def names(group="all", q=""):
    return node(f"m.visibleModels(M, {{group: {json.dumps(group)}, q: {json.dumps(q)}}}).map(x => x.name)")


@needs_node
def test_running_models_sort_first_then_crashed_then_by_name():
    assert names() == ["qwen2.5-0.5b", "smollm3-3b", "llama-3.2-1b", "bge-small", "qwen2.5-coder-1.5b"]


@needs_node
def test_groups_filter_by_show_kind_and_label():
    assert names("running") == ["qwen2.5-0.5b", "smollm3-3b"]
    assert names("kind:coder") == ["qwen2.5-coder-1.5b"]
    assert names("label:work") == ["qwen2.5-0.5b", "qwen2.5-coder-1.5b"]
    assert names("label:chat") == []  # a label called "chat" is not the kind chat


@needs_node
def test_search_matches_name_kind_and_hash_label_and_every_word_must_match():
    assert names(q="coder") == ["qwen2.5-coder-1.5b"]
    assert names(q="embed") == ["bge-small"]  # kind, by prefix
    assert names(q="#exp") == ["smollm3-3b", "bge-small"]  # label, by prefix
    assert names(q="qwen #work") == ["qwen2.5-0.5b", "qwen2.5-coder-1.5b"]
    assert names(q="qwen #favorites") == ["qwen2.5-0.5b"]
    assert names(q="  QWEN  ", group="kind:coder") == ["qwen2.5-coder-1.5b"]  # search narrows the chosen group
    assert names(q="nothing like this") == []


@needs_node
def test_counts_for_the_groups_column():
    c = node('m.counts(M, ["work", "favorites", "empty"])')
    assert c == {"all": 5, "running": 2, "kinds": {"chat": 3, "coder": 1, "vision": 0, "embeddings": 1, "image": 0},
                 "labels": {"work": 2, "favorites": 1, "empty": 0}}


@needs_node
def test_a_label_chip_is_on_for_all_ticked_ring_for_some_off_for_none():
    for ticked, want in ((["qwen2.5-0.5b", "qwen2.5-coder-1.5b"], "all"), (["qwen2.5-0.5b", "bge-small"], "some"), (["bge-small", "llama-3.2-1b"], "none")):
        assert node(f"m.bulkState(M, {json.dumps(ticked)}, 'work')") == want, ticked


@needs_node
def test_tapping_a_chip_adds_to_all_ticked_and_tapping_again_removes():
    state = '{labels: ["work"], models: M}'
    add = node(f'(() => {{ const r = m.toggleOn({state}, ["qwen2.5-0.5b", "bge-small"], "work"); return [r.action, r.state.models.map(x => x.labels)]; }})()')
    assert add[0] == "add" and add[1][0] == ["work", "favorites"] and add[1][3] == ["experiments", "work"]  # added where missing, nothing doubled
    rem = node(f'(() => {{ const r = m.toggleOn({state}, ["qwen2.5-0.5b", "qwen2.5-coder-1.5b"], "work"); return [r.action, r.state.models.map(x => x.labels)]; }})()')
    assert rem[0] == "remove" and rem[1][0] == ["favorites"] and rem[1][1] == []
    assert node(f'm.toggleOn({state}, ["qwen2.5-0.5b", "bge-small"], "work").action') == "add"  # a ring (some have it) fills the rest in


@needs_node
def test_label_changes_return_new_state_and_leave_the_old_one_alone():
    s = '{labels: ["work", "favorites"], models: M}'
    r = node(f'(() => {{ const s = {s}; const n = m.renamed(s, "work", "job"); return [n.labels, n.models[0].labels, n.models[1].labels, s.labels, s.models[0].labels]; }})()')
    assert r == [["job", "favorites"], ["job", "favorites"], ["job"], ["work", "favorites"], ["work", "favorites"]]
    d = node(f'(() => {{ const n = m.deleted({s}, "work"); return [n.labels, n.models[0].labels, n.models[1].labels]; }})()')
    assert d == [["favorites"], ["favorites"], []]
    assert node(f'm.created({s}, "new").labels') == ["work", "favorites", "new"]
    assert node(f'm.ordered({s}, ["favorites", "work"]).labels') == ["favorites", "work"]


@needs_node
def test_label_names_are_cleaned_to_what_the_server_accepts():
    for raw, want in (("Work", "work"), ("  my label ", "my-label"), ("Client_ACME!", "clientacme"), ("--x--", "x"), ("#fav", "fav"), ("!!!", ""), ("a" * 50, "a" * 40)):
        assert node(f"m.cleanLabel({json.dumps(raw)})") == want, raw


@needs_node
def test_reordering_by_dragging_a_label_before_another_or_to_the_end():
    L = '["a", "b", "c", "d"]'
    assert node(f'm.reorder({L}, "d", "b")') == ["a", "d", "b", "c"]
    assert node(f'm.reorder({L}, "a", null)') == ["b", "c", "d", "a"]
    assert node(f'm.reorder({L}, "b", "c")') == ["a", "b", "c", "d"]  # already there


@needs_node
def test_dragging_a_ticked_row_takes_all_ticked_but_an_unticked_row_goes_alone():
    assert node('m.dragNames("a", ["a", "b", "c"])') == ["a", "b", "c"]
    assert node('m.dragNames("z", ["a", "b"])') == ["z"]
    assert node('m.dragNames("a", ["a"])') == ["a"]


@needs_node
def test_a_failed_request_puts_the_old_state_back_and_says_why():
    got = run_node("""
      let state = {labels: ["a"]}; const seen = []; const toasts = [];
      const run = (request) => m.optimistic({get: () => state, set: (s) => { state = s; seen.push(s.labels.join()); },
        next: (s) => ({labels: [...s.labels, "b"]}), request, onError: (msg) => toasts.push(msg)});
      const ok = await run(async () => {});
      const bad = await run(async () => { throw new Error("a label named b already exists"); });
      console.log(JSON.stringify({ok, bad, seen, state: state.labels, toasts}));
    """)
    assert got == {"ok": True, "bad": False, "seen": ["a,b", "a,b,b", "a,b"], "state": ["a", "b"], "toasts": ["a label named b already exists"]}


async def test_the_library_files_are_served_and_linked(http):
    for name in ("library.js", "library-logic.js", "library.css"):
        assert name in CONSOLE_ASSETS and (await http.get(f"/console/{name}")).status_code == 200, name
    assert 'href="/console/library.css"' in (await http.get("/console")).text


def test_library_css_braces_balance_and_phone_rules_exist():
    css = (CONSOLE_DIR / "library.css").read_text()
    assert css.count("{") == css.count("}")
    phone = css[css.index("@media (max-width: 760px)"):]
    assert ".groups.editing" in phone and ".lib:has(.groups.editing) .res { display: none; }" in phone  # Edit labels is full height


def test_the_fake_models_script_fills_a_scratch_store_and_refuses_the_real_one(tmp_path, monkeypatch):
    import runpy
    import sys

    from mxconsole.store import Store
    script = str(CONSOLE_DIR.parents[1] / "scripts" / "fake_models.py")
    monkeypatch.setenv("MXC_HOME", str(tmp_path / "scratch"))
    runpy.run_path(script, run_name="not_main")["main"](100)
    store = Store(tmp_path / "scratch" / "mst.db")
    assert len(store.models()) == 100 and [l.name for l in store.labels()] == ["work", "favorites", "experiments", "client-acme"]
    monkeypatch.delenv("MXC_HOME")
    with pytest.raises(SystemExit):
        runpy.run_path(script, run_name="not_main")["main"](100)
