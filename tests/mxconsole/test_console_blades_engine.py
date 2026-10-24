"""F-023 review fixes: blocked storage, keyed in-place updates, the blade stack, icons, accessibility, the contract."""
import re

from mxconsole.gateway import CONSOLE_DIR
from test_console_blades import needs_node, node

# -- blocked storage ----------------------------------------------------------------------------
@needs_node
def test_a_blocked_localstorage_never_throws_out_of_safe_storage():
    blocked = "(globalThis.window = {get localStorage() { throw new Error('SecurityError'); }}, m.safeStorage())"
    assert node(blocked, "prefs.js") is None
    assert node("(globalThis.window = undefined, m.safeStorage())", "prefs.js") is None
    assert node("(globalThis.window = {localStorage: {tag: 'ls'}}, m.safeStorage().tag)", "prefs.js") == "ls"
    # the readers and writers accept the null it returns
    assert node("[m.readView(null), m.readTheme(null), m.writeView(null, 'cards'), m.writeTheme(null, 'dark')]", "prefs.js") == ["list", None, None, None]


def test_nothing_in_the_new_code_touches_localstorage_directly():
    for name in ("topbar.js", "blade-home.js", "blades.js"):
        assert not re.search(r"\blocalStorage\b", (CONSOLE_DIR / name).read_text()), name


# -- keyed, in-place updates (focus survives the 3 s poll) ---------------------------------------
FAKE_PARENT = """(() => {
  const mk = (n) => ({ n, parent: null, remove() { if (this.parent) { this.parent.children.splice(this.parent.children.indexOf(this), 1); this.parent = null; } } });
  const parent = { children: [], insertBefore(el, ref) { el.remove(); const i = ref ? this.children.indexOf(ref) : this.children.length; this.children.splice(i < 0 ? this.children.length : i, 0, el); el.parent = this; } };
  return { mk, parent };
})()"""


@needs_node
def test_the_keyed_plan_says_what_to_add_and_remove():
    assert node("m.planKeyed(['a','b','c'], ['b','c','d'])", "keyed.js") == {"remove": ["a"], "add": ["d"]}
    assert node("m.planKeyed([], ['a'])", "keyed.js") == {"remove": [], "add": ["a"]}


@needs_node
def test_a_keyed_sync_keeps_the_same_elements_so_focus_and_hover_survive():
    script = f"""(() => {{
      const {{ mk, parent }} = {FAKE_PARENT};
      const cache = new Map(), updated = [];
      const create = (v) => mk(v.name), update = (el, v) => updated.push(v.name);
      m.syncKeyed(parent, cache, [{{name:'a'}}, {{name:'b'}}, {{name:'c'}}], create, update);
      const first = parent.children.slice();
      m.syncKeyed(parent, cache, [{{name:'a'}}, {{name:'c'}}, {{name:'d'}}], create, update);
      return {{ order: parent.children.map((e) => e.n), same: parent.children[0] === first[0] && parent.children[1] === first[2],
               gone: first[1].parent === null, cached: [...cache.keys()], updatedCount: updated.length }};
    }})()"""
    r = node(script, "keyed.js")
    assert r["order"] == ["a", "c", "d"] and r["same"] and r["gone"] and r["cached"] == ["a", "c", "d"] and r["updatedCount"] == 6


@needs_node
def test_a_keyed_sync_follows_a_new_order_and_leaves_a_trailing_node_last():
    script = f"""(() => {{
      const {{ mk, parent }} = {FAKE_PARENT};
      const cache = new Map(), tail = mk('add'); parent.insertBefore(tail, null);
      const create = (v) => mk(v.name);
      m.syncKeyed(parent, cache, [{{name:'a'}}, {{name:'b'}}], create, () => {{}});
      m.syncKeyed(parent, cache, [{{name:'b'}}, {{name:'a'}}, {{name:'c'}}], create, () => {{}});
      return parent.children.map((e) => e.n);
    }})()"""
    assert node(script, "keyed.js") == ["b", "a", "c", "add"]


# -- the blade stack ----------------------------------------------------------------------------
@needs_node
def test_closing_blades_runs_each_onclose_once_newest_first_and_the_home_blade_stays():
    script = """(() => {
      const log = [], mk = (n) => ({ n, alive: true, onClose() { log.push(n); } });
      const stack = [mk('home'), mk('model'), mk('logs'), mk('bench')];
      const gone = m.closeFrom(stack, 1);
      const again = m.closeFrom(stack, 1);
      const zero = m.closeFrom(stack, 0);
      return { log, left: stack.map((b) => b.n), gone: gone.map((b) => b.alive), again: again.length, zero: zero.length };
    })()"""
    r = node(script, "blade-stack.js")
    assert r == {"log": ["bench", "logs", "model"], "left": ["home"], "gone": [False, False, False], "again": 0, "zero": 0}


@needs_node
def test_one_blade_that_throws_while_closing_does_not_strand_the_others():
    script = """(() => {
      const log = [], keep = console.error; console.error = () => {};
      const stack = [{ n: 'home' }, { n: 'a', onClose() { log.push('a'); } }, { n: 'b', onClose() { throw new Error('boom'); } }, { n: 'c', onClose() { log.push('c'); } }];
      const gone = m.closeFrom(stack, 1, (b) => log.push('removed ' + b.n));
      console.error = keep;
      return { log, alive: gone.map((b) => b.alive) };
    })()"""
    r = node(script, "blade-stack.js")
    assert r["log"] == ["c", "removed c", "removed b", "a", "removed a"] and r["alive"] == [False, False, False]


@needs_node
def test_opening_a_blade_after_an_index_closes_everything_to_its_right_and_docks_it_last():
    script = """(() => {
      const mk = (n) => ({ n, alive: true }), stack = [mk('home'), mk('model'), mk('logs')];
      m.openAt(stack, mk('security'), 1);
      const afterChild = stack.map((b) => b.n);
      m.openAt(stack, mk('add'), 0);
      return { afterChild, afterHome: stack.map((b) => b.n) };
    })()"""
    assert node(script, "blade-stack.js") == {"afterChild": ["home", "model", "security"], "afterHome": ["home", "add"]}


@needs_node
def test_escape_closes_the_newest_blade_but_never_the_last_one():
    assert node("[m.escapeTarget(1), m.escapeTarget(2), m.escapeTarget(4), m.escapeTarget(0)]", "blade-stack.js") == [None, 1, 3, None]


# -- icons later tickets need -------------------------------------------------------------------
def test_every_icon_the_later_blades_need_is_already_in_icons_js():
    wanted = """trash-2 pause play square rotate-cw refresh-cw shield shield-check lock unlock key-round copy external-link gauge scroll-text
      book-open notebook terminal cpu download search circle-alert triangle-alert check x chevron-right plus git-branch route sun moon wrench
      hammer file-code flask-conical eye eye-off clock activity""".split()
    text = (CONSOLE_DIR / "icons.js").read_text()
    assert [n for n in wanted if f'"{n}":[' not in text] == []


# -- accessibility and the contract -------------------------------------------------------------
def test_the_topbar_marks_the_current_crumb_and_stays_on_tokens():
    js = (CONSOLE_DIR / "topbar.js").read_text()
    assert "aria-current" in js
    css = (CONSOLE_DIR / "topbar.css").read_text()
    assert not re.findall(r"#[0-9a-fA-F]{3,6}\b", css), "topbar.css uses omxqua tokens, not raw hex"
    # F-052: the bar only says where you are (the places live in the nav, the theme in Settings); at phone width the crumb fills it
    phone = css[css.index("@container (max-width: 760px)"):]
    assert ".crumbs" in phone and ".crumbs { flex: 1; }" in phone
    assert '"Theme"' not in js and "setTheme" not in js
    # the bar is the crumbs and the Beacon, nothing else: no tool buttons, no menu
    assert 'h("nav", { class: "crumbs"' in js and "mountBeacon(right" in js and "cx-tools" not in js and "menu" not in js
    assert "Appearance" in (CONSOLE_DIR / "blade-settings.js").read_text()


def test_rows_and_cards_are_real_buttons_and_rails_can_be_focused():
    home = (CONSOLE_DIR / "blade-home.js").read_text()
    assert 'h("button", { class: "card' in home and 'class: "namebtn' in home
    assert "tabindex" in (CONSOLE_DIR / "blades.js").read_text() and "Enter" in (CONSOLE_DIR / "blades.js").read_text()


def test_the_contract_in_blades_js_names_the_rules_later_blades_must_follow():
    doc = (CONSOLE_DIR / "blades.js").read_text().split("import { h, icon")[0]
    for phrase in ("exactly one JS file and one CSS file", "stack.indexOf(blade)", "onClose", "onData"):
        assert phrase in doc, phrase
    assert "onClose" in (CONSOLE_DIR / "blade-logs.js").read_text()  # the stub shows the pattern


def test_header_tools_accept_one_node_a_list_of_nodes_or_nothing():
    """The contract says `tools: [node, ...]`; a list must not render as "[object HTMLButtonElement]"."""
    import subprocess, json as _json
    from mxconsole.gateway import CONSOLE_DIR
    script = f"""
      globalThis.Node = class {{}};
      const m = await import({_json.dumps((CONSOLE_DIR / "blades.js").as_uri())});
      const a = new Node(), b = new Node();
      const r = [m.asNodes(a).length, m.asNodes([a, null, [b]]).length, m.asNodes(undefined).length,
                 m.asNodes([a, b]).every(x => x instanceof Node), m.asNodes("hi")];
      console.log(JSON.stringify(r));
    """
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert _json.loads(out.stdout) == [1, 2, 0, True, ["hi"]]
