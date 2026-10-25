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
def test_a_refused_change_is_undone_alone_and_says_why():
    got = run_node("""
      let state = {labels: ["a"]}; const toasts = [];
      const add = (name) => ({next: (s) => ({labels: [...s.labels, name]}), undo: (s) => ({labels: s.labels.filter((l) => l !== name)})});
      const run = (name, request) => m.optimistic({get: () => state, set: (s) => { state = s; }, ...add(name), request, onError: (msg) => toasts.push(msg)});
      const ok = await run("b", async () => {});
      const bad = await run("c", async () => { throw new Error("label c refused"); });
      console.log(JSON.stringify({ok, bad, state: state.labels, toasts}));
    """)
    assert got == {"ok": True, "bad": False, "state": ["a", "b"], "toasts": ["label c refused"]}


@needs_node
def test_when_the_first_of_two_overlapping_changes_fails_the_second_survives():
    got = run_node("""
      let state = {labels: ["a"]}; const toasts = [];
      const add = (name) => ({next: (s) => ({labels: [...s.labels, name]}), undo: (s) => ({labels: s.labels.filter((l) => l !== name)})});
      const run = (name, request) => m.optimistic({get: () => state, set: (s) => { state = s; }, ...add(name), request, onError: (msg) => toasts.push(msg)});
      const slowFail = run("b", () => new Promise((_, no) => setTimeout(() => no(new Error("b refused")), 30)));
      const quickOk = run("c", async () => {});
      const during = state.labels.join();
      await Promise.all([slowFail, quickOk]);
      console.log(JSON.stringify({during, state: state.labels, toasts}));
    """)
    assert got == {"during": "a,b,c", "state": ["a", "c"], "toasts": ["b refused"]}


@needs_node
def test_undoing_a_delete_puts_the_label_back_where_it_was_on_the_models_that_had_it():
    s = '{labels: ["work", "favorites", "experiments"], models: M}'
    got = node(f'(() => {{ const s0 = {s}; const had = s0.models.filter(x => x.labels.includes("work")).map(x => x.name); '
               f'const gone = m.deleted(s0, "work"); const back = m.restoredLabel(gone, "work", 0, had); return [back.labels, back.models.map(x => x.labels)]; }})()')
    assert got == [["work", "favorites", "experiments"], [["favorites", "work"], ["work"], [], ["experiments"], ["experiments"]]]
    # the same models that were added to in the meantime keep their new labels
    extra = node(f'(() => {{ const s0 = {s}; const gone = m.deleted(s0, "work"); const later = m.added(gone, ["bge-small"], "favorites"); '
                 f'return m.restoredLabel(later, "work", 1, ["qwen2.5-0.5b"]).models[3].labels; }})()')
    assert extra == ["experiments", "favorites"]


@needs_node
def test_undoing_a_reorder_follows_the_old_order_and_keeps_labels_made_since():
    assert node('m.restoredOrder({labels: ["c", "a", "b", "new"], models: []}, ["a", "b", "c"]).labels') == ["a", "b", "c", "new"]
    assert node('m.restoredOrder({labels: ["c", "a"], models: []}, ["a", "b", "c"]).labels') == ["a", "c"]  # b was deleted meanwhile


DOM_SHIM = """
  class Node {}
  class Text extends Node { constructor(d) { super(); this.data = String(d); } }
  class El extends Node {
    constructor(tag) { super(); this.tag = tag; this.children = []; this.attrs = {}; this.listeners = {}; this.cls = new Set(); this.style = { setProperty() {}, cssText: "" }; this.dataset = {}; this.scrollWidth = 0; this.clientWidth = 0;
      this.classList = { toggle: (c, on) => { (on === undefined ? !this.cls.has(c) : on) ? this.cls.add(c) : this.cls.delete(c); }, add: (c) => this.cls.add(c), remove: (c) => this.cls.delete(c), contains: (c) => this.cls.has(c) }; }
    setAttribute(k, v) { this.attrs[k] = v; } addEventListener(t, f) { (this.listeners[t] ||= []).push(f); } removeEventListener() {}
    append(...c) { this.children.push(...c); } replaceChildren(...c) { this.children = c; }
    querySelector() { return null; } querySelectorAll() { return []; } focus() { document.activeElement = this; }
    contains(n) { return n === this || this.children.some((c) => c.contains && c.contains(n)); }
  }
  const docListeners = {};
  globalThis.Node = Node;
  globalThis.document = { activeElement: { tagName: "BODY" }, createElement: (t) => new El(t), createElementNS: (_, t) => new El(t), createTextNode: (d) => new Text(d),
    getElementById: () => toastEl, addEventListener(t, f) { docListeners[t] = f; }, removeEventListener(t) { delete docListeners[t]; } };
  const toastEl = new El("div");
  const walk = (n, out = []) => { out.push(n); (n.children || []).forEach((c) => walk(c, out)); return out; };
  const core = await import(CORE);
  const { createLibrary, tagsEl } = await import(LIB);
"""


def run_dom(body: str):
    pre = f"const CORE = {json.dumps((CONSOLE_DIR / 'core.js').as_uri())}, LIB = {json.dumps((CONSOLE_DIR / 'library.js').as_uri())};"
    script = pre + DOM_SHIM + body
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@needs_node
def test_slash_focuses_the_search_unless_you_are_already_typing():
    got = run_dom("""
      const lib = createLibrary({ onChange() {} }); lib.listen();
      const press = (active) => { document.activeElement = active; const e = { key: "/", prevented: false, preventDefault() { this.prevented = true; } }; docListeners.keydown(e); return e.prevented; };
      const a = press({ tagName: "BODY" }); const focused = document.activeElement.tag;
      const b = press({ tagName: "INPUT" });
      console.log(JSON.stringify({ a, focused, b }));
    """)
    assert got == {"a": True, "focused": "input", "b": False}


@needs_node
def test_a_refused_delete_or_rename_keeps_you_on_the_label_you_were_viewing():
    got = run_dom("""
      globalThis.fetch = async () => ({ ok: false, status: 409, headers: { get: () => "application/json" }, json: async () => ({ error: { message: "refused" } }) });
      core.store.models = [{ name: "m", kind: "chat", state: "cold", labels: ["work"] }];
      const lib = createLibrary({ onChange() {} });
      const press = (label) => walk(lib.groups).find((n) => n.attrs && n.attrs["aria-label"] === label).listeners.click[0]();
      const later = () => new Promise((r) => setTimeout(r, 20));
      const out = {};
      lib.labels = ["work"]; lib.group = "label:work"; lib.editing = true; lib.update();
      press("Delete work"); out.during = lib.group; await later();
      out.afterDelete = [lib.group, lib.labels, core.store.models[0].labels];
      const field = walk(lib.groups).find((n) => n.attrs && n.attrs["aria-label"] === "Rename work");
      field.value = "job"; field.listeners.change[0](); await later();
      out.afterRename = [lib.group, lib.labels, core.store.models[0].labels];
      console.log(JSON.stringify(out));
    """)
    assert got["during"] == "all"  # the column falls back to All while the delete is pending
    assert got["afterDelete"] == ["label:work", ["work"], ["work"]]
    assert got["afterRename"] == ["label:work", ["work"], ["work"]]


@needs_node
def test_label_and_model_names_render_as_text_never_as_html():
    got = run_dom("""
      const evil = "<img src=x onerror=alert(1)>";
      core.store.models = [{ name: "m", kind: "chat", state: "cold", labels: [evil] }];
      const lib = createLibrary({ onChange() {} });
      lib.labels = [evil]; lib.ticked.add("m"); lib.update();
      const nodes = [...walk(lib.groups), ...walk(lib.bar), ...walk(tagsEl(core.store.models[0]))];
      console.log(JSON.stringify({ text: nodes.filter((n) => n instanceof Text).map((n) => n.data).filter((d) => d.includes("img")),
        elements: nodes.filter((n) => n.tag).map((n) => n.tag).filter((t) => t === "img" || t === "script") }));
    """)
    assert got["elements"] == [] and "<img src=x onerror=alert(1)>" in got["text"] and "#<img src=x onerror=alert(1)>" in got["text"]


async def test_the_library_files_are_served_and_linked(http):
    for name in ("library.js", "library-logic.js", "library.css"):
        assert name in CONSOLE_ASSETS and (await http.get(f"/console/{name}")).status_code == 200, name
    assert 'href="/console/library.css"' in (await http.get("/console")).text


def test_library_css_braces_balance_and_phone_rules_exist():
    css = (CONSOLE_DIR / "library.css").read_text()
    assert css.count("{") == css.count("}")
    phone = css[css.index("@media (max-width: 760px)"):]
    assert ".groups.editing" in phone and ".lib:has(.groups.editing) .res { display: none; }" in phone  # Edit labels is full height


def test_touch_targets_reach_44px_on_the_phone_through_hit_areas_not_bigger_boxes():
    phone = (CONSOLE_DIR / "library.css").read_text().split("@media (max-width: 760px)")[1]
    for sel, inset in ((".cb", "-10.5px"), (".chip", "-6px 0"), (".gi:not(.edit):not(.new)", "-4px 0"), (".hdl", "-2px -5px"), (".gi .x", "-4px"), (".gh .ed", "-4px 0")):
        assert re.search(re.escape(sel) + r"::after \{ inset: " + re.escape(inset) + r"; \}", phone), sel


def test_select_all_shown_reports_its_state_like_the_row_ticks():
    assert 'selAll.setAttribute("aria-pressed"' in (CONSOLE_DIR / "blade-home.js").read_text()


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


@needs_node
def test_sizes_read_like_a_file_manager_and_never_say_1000_mb():
    got = node("[0, 360e6, 999.4e6, 999.6e6, 1e9, 1.34e9, 29.04e9].map(m.sizeLabel)")
    assert got == ["–", "360 MB", "999 MB", "1.0 GB", "1.0 GB", "1.3 GB", "29.0 GB"]
