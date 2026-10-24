"""F-056: the wide model switcher, Recent, and labels on a model's Overview. Run for real under node on the tiny fake DOM
(tests/mxconsole/fake_dom.mjs); every assert is what a person using the switcher would see or get."""
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
      const pk = await import({json.dumps((CONSOLE_DIR / 'picker.js').as_uri())});
      const lg = await import({json.dumps((CONSOLE_DIR / 'library-logic.js').as_uri())});
      const lib = await import({json.dumps((CONSOLE_DIR / 'library.js').as_uri())});
      const prefs = await import({json.dumps((CONSOLE_DIR / 'prefs.js').as_uri())});
      {body}
    """
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


# -- the pure parts ---------------------------------------------------------------------------------
def test_recent_keeps_the_last_three_models_newest_first_each_once():
    got = run("""
      let r = [];
      for (const n of ["a", "b", "c", "d", "b"]) r = lg.pushRecent(r, n);
      console.log(JSON.stringify(r));
    """)
    assert got == ["b", "d", "c"]


def test_recent_survives_a_blocked_or_corrupt_storage():
    got = run("""
      const bad = { getItem: () => "{not json", setItem: () => { throw new Error("blocked"); } };
      const odd = { getItem: () => '{"a":1}', setItem: () => {} };
      prefs.writeRecent(bad, ["x"]);
      console.log(JSON.stringify([prefs.readRecent(bad), prefs.readRecent(odd), prefs.readRecent(null)]));
    """)
    assert got == [[], [], []]


SECTION_MODELS = """
  const models = [
    { name: "alpha", state: "ready", kind: "chat", labels: ["work"] }, { name: "beta", state: "cold", kind: "coder", labels: [] },
    { name: "gamma", state: "cold", kind: "chat", labels: [] }, { name: "delta", state: "cold", kind: "vision", labels: ["work"] },
    { name: "odd", state: "cold", kind: null, labels: [] },
  ];
  const names = (secs) => secs.map((s) => [s.id, s.models.map((m) => m.name)]);
"""


def test_unfiltered_the_switcher_shows_recent_first_then_every_model_grouped_by_kind():
    got = run(SECTION_MODELS + 'console.log(JSON.stringify(names(lg.switcherSections(models, { recent: ["delta", "ghost", "alpha"] }))));')
    assert got == [["recent", ["delta", "alpha"]], ["kind:chat", ["alpha", "gamma"]], ["kind:coder", ["beta"]], ["kind:vision", ["delta"]], ["kind:other", ["odd"]]]


def test_with_a_search_or_a_group_the_switcher_is_one_flat_list_of_matches():
    got = run(SECTION_MODELS + """
      console.log(JSON.stringify([names(lg.switcherSections(models, { q: "gam", recent: ["alpha"] })),
        names(lg.switcherSections(models, { group: "label:work", recent: ["alpha"] })), names(lg.switcherSections(models, { q: "zzz" }))]));
    """)
    assert got == [[["results", ["gamma"]]], [["results", ["alpha", "delta"]]], []]


# -- the switcher on the fake DOM --------------------------------------------------------------------
SETUP = """
  core.store.models = [
    { name: "alpha", state: "ready", kind: "chat", labels: ["work"] }, { name: "beta", state: "cold", kind: "coder", labels: [] },
    { name: "gamma", state: "cold", kind: "chat", labels: [] }, { name: "delta", state: "cold", kind: "vision", labels: ["work"] },
  ];
  core.store.metrics = { by_model: { alpha: { decode_tps_p50: 41.6 } } };
  const posts = [];
  globalThis.fetch = async (url, init = {}) => {
    if (init.method === "POST") posts.push([url, JSON.parse(init.body)]);
    const data = url === "/admin/labels" ? [{ name: "work" }, { name: "later" }] : [];
    return { ok: true, status: 200, headers: { get: () => "application/json" }, json: async () => data, text: async () => "" };
  };
  const toasts = [], toastEl = document.createElement("div"); document.getElementById = () => toastEl;
  const tick = () => new Promise((r) => setTimeout(r, 0));
  const anchor = document.createElement("button"); document.body.append(anchor);
  const app = document.createElement("div"); app.classList.add("app"); document.body.append(app);
  app.getBoundingClientRect = () => ({ left: 0, top: 0, bottom: 800, width: 1068, height: 800 });
  document.querySelector = (s) => (s === ".app" ? app : null);
  let route = { page: "model", model: "alpha", tab: "logs" };
  const routeListeners = new Set(), went = [];
  const memory = {}, storage = { getItem: (k) => (k in memory ? memory[k] : null), setItem: (k, v) => { memory[k] = v; } };
  prefs.writeRecent(storage, ["gamma", "alpha"]);
  const env = { currentRoute: () => route, go: (r) => went.push(r), storage: () => storage,
    onRoute: (fn) => { routeListeners.add(fn); fn(route); return () => routeListeners.delete(fn); } };
  const panel = () => app.querySelector(".pmenu");
  const input = () => panel().querySelector("input");
  const cardNames = () => panel().querySelectorAll(".pr").map((c) => c.getAttribute("data-pick"));
  const kbName = () => panel().querySelectorAll(".pr").filter((c) => c.classList.contains("kb")).map((c) => c.getAttribute("data-pick"));
  const heads = () => panel().querySelectorAll(".grp").map((g) => g.textContent);
  const type = (text) => { input().value = text; input().dispatch("input"); };
  const open = async () => { pk.openPicker(anchor, env); await tick(); };
  await open();
"""


def test_it_opens_as_a_modal_wide_panel_with_search_focused_and_recent_first_then_kinds():
    got = run(SETUP + """
      console.log(JSON.stringify({ modal: panel().getAttribute("aria-modal"), sheet: panel().classList.contains("sheet"),
        focus: document.activeElement === input(), heads: heads(), cards: cardNames(), stay: panel().querySelector(".stay").textContent }));
    """)
    assert got == {"modal": "true", "sheet": False, "focus": True, "stay": "Switching keeps you on logs",
                   "heads": ["Recent", "Chat · 2", "Coder · 1", "Vision · 1"],
                   "cards": ["gamma", "alpha", "alpha", "gamma", "beta", "delta"]}


def test_a_card_shows_the_name_the_state_and_tok_per_s_when_running():
    got = run(SETUP + """
      const text = (name) => panel().querySelectorAll(".pr").filter((c) => c.getAttribute("data-pick") === name)[0].textContent;
      console.log(JSON.stringify([text("alpha"), text("beta")]));
    """)
    assert got == ["alphaChat#work42 tok/s", "betaCoderstopped"]


def test_arrow_keys_move_across_cards_and_enter_opens_one_on_the_same_tab():
    got = run(SETUP + """
      const start = kbName();
      const down = document.key("ArrowDown"), down2 = document.key("ArrowDown");
      const afterTwo = kbName();
      document.key("ArrowUp");
      const afterUp = kbName();
      document.key("ArrowDown"); document.key("ArrowDown"); document.key("ArrowDown");
      const enter = document.key("Enter");
      console.log(JSON.stringify({ start, afterTwo, afterUp, prevented: [down.defaultPrevented, enter.defaultPrevented], went, focus: document.activeElement === anchor }));
    """)
    # the cards are gamma, alpha, alpha, gamma, beta, delta (Recent first); the keyboard starts on the current model's first card
    assert got == {"start": ["alpha"], "afterTwo": ["gamma"], "afterUp": ["alpha"], "prevented": [True, True],
                   "went": [{"page": "model", "model": "delta", "tab": "logs"}], "focus": True}


def test_arrows_walk_the_card_order_and_the_keyboard_never_runs_past_the_ends():
    got = run(SETUP + """
      const seen = [];
      for (let i = 0; i < 8; i++) { document.key("ArrowDown"); seen.push(panel().querySelectorAll(".pr").findIndex((c) => c.classList.contains("kb"))); }
      for (let i = 0; i < 8; i++) document.key("ArrowUp");
      seen.push(panel().querySelectorAll(".pr").findIndex((c) => c.classList.contains("kb")));
      console.log(JSON.stringify(seen));
    """)
    assert got == [2, 3, 4, 5, 5, 5, 5, 5, 0]


def test_enter_on_the_current_model_just_closes_and_left_right_leave_the_search_caret_alone():
    got = run(SETUP + """
      const side = document.key("ArrowRight");
      const enter = document.key("Enter");
      console.log(JSON.stringify({ sideKept: side.defaultPrevented, went, closed: document.key("Escape").defaultPrevented }));
    """)
    assert got == {"sideKept": False, "went": [], "closed": False}


def test_a_group_and_a_search_narrow_the_cards_and_say_what_matched():
    got = run(SETUP + """
      panel().querySelector('[data-group="kind:chat"]').click();
      const chat = { heads: heads(), cards: cardNames() };
      type("gam");
      const both = { heads: heads(), cards: cardNames() };
      type("zzz");
      const none = { heads: heads(), none: panel().querySelector(".none").textContent };
      console.log(JSON.stringify({ chat, both, none }));
    """)
    assert got == {"chat": {"heads": ["Chat · 2"], "cards": ["alpha", "gamma"]},
                   "both": {"heads": ["Chat · 1 matching “gam”"], "cards": ["gamma"]},
                   "none": {"heads": [], "none": "No model matches “zzz”."}}


def test_your_labels_are_in_the_column_and_filter_the_cards_and_can_be_edited_there():
    got = run(SETUP + """
      const column = panel().querySelectorAll(".gi").filter((g) => g.getAttribute("data-lab")).map((g) => g.getAttribute("data-lab"));
      panel().querySelector('[data-group="label:work"]').click();
      const cards = cardNames();
      panel().querySelector('[data-act="edit-labels"]').click();
      console.log(JSON.stringify({ column, cards, editing: panel().querySelectorAll(".gi.edit").map((g) => g.getAttribute("data-lab")) }));
    """)
    assert got == {"column": ["work", "later"], "cards": ["alpha", "delta"], "editing": ["work", "later"]}


def test_typing_a_search_resets_the_keyboard_to_the_first_match():
    got = run(SETUP + 'document.key("ArrowDown"); type("a"); console.log(JSON.stringify([cardNames(), kbName()]));')
    assert got[1] == [got[0][0]]


def test_tab_stays_inside_the_switcher_and_escape_closes_it_and_returns_focus_to_the_crumb():
    got = run(SETUP + """
      const stops = [];
      for (let i = 0; i < 40; i++) { const e = document.key("Tab"); stops.push([document.activeElement.tagName, e.defaultPrevented]); if (i > 0 && document.activeElement === input()) break; }
      const back = document.key("Tab", { shiftKey: true });
      const last = document.activeElement.getAttribute("data-act");
      const esc = document.key("Escape");
      console.log(JSON.stringify({ wrapped: stops.length > 3, inside: stops.every(([, p]) => p), last, esc: esc.defaultPrevented, focusBack: document.activeElement === anchor, count: stops.length }));
    """)
    assert got["wrapped"] is True and got["inside"] is True and got["last"] == "add-model" and got["esc"] is True and got["focusBack"] is True


def test_add_a_model_in_the_footer_goes_to_the_add_page():
    got = run(SETUP + 'panel().querySelector(\'[data-act="add-model"]\').click(); console.log(JSON.stringify({ went, focus: document.activeElement === anchor }));')
    assert got == {"went": [{"page": "add"}], "focus": True}


def test_a_route_change_closes_it_and_nothing_is_left_listening():
    got = run(SETUP + """
      const listening = routeListeners.size;
      route = { page: "model", model: "alpha", tab: "benchmark" };
      routeListeners.forEach((fn) => fn(route));
      const list = panel().querySelector(".plist");
      const before = list.textContent;
      core.store.models = [...core.store.models, { name: "late", state: "cold", kind: "chat", labels: [] }]; core.emitData();
      const esc = document.key("Escape");
      console.log(JSON.stringify({ listening, routes: routeListeners.size, resize: window.listening("resize"), frozen: list.textContent === before, escapeIgnored: esc.defaultPrevented, focus: document.activeElement === anchor }));
    """)
    assert got == {"listening": 1, "routes": 0, "resize": 0, "frozen": True, "escapeIgnored": False, "focus": True}


def test_a_height_only_resize_keeps_it_open_with_the_search_text_and_a_menu_sheet_flip_closes_it():
    """A phone's keyboard fires resize with the same width: the switcher and what was typed must stay. Crossing 760 px closes it."""
    got = run(SETUP + """
      type("gam");
      app.getBoundingClientRect = () => ({ left: 0, top: 0, bottom: 700, width: 1068, height: 700 });
      window.resize();
      const kept = { open: document.key("Escape").defaultPrevented === true };
      await open();
      app.getBoundingClientRect = () => ({ left: 0, top: 0, bottom: 800, width: 390, height: 800 });
      window.resize();
      console.log(JSON.stringify({ kept, afterFlip: { open: document.key("Escape").defaultPrevented, listening: window.listening("resize") } }));
    """)
    assert got == {"kept": {"open": True}, "afterFlip": {"open": False, "listening": 0}}


def test_on_a_phone_it_is_a_sheet_and_the_search_is_not_focused_so_the_keyboard_stays_down():
    got = run(SETUP.replace("await open();", "") + """
      app.getBoundingClientRect = () => ({ left: 0, top: 0, bottom: 800, width: 390, height: 800 });
      await open();
      console.log(JSON.stringify({ sheet: panel().classList.contains("sheet"), searchFocused: document.activeElement === input(), panelFocused: document.activeElement === panel() }));
    """)
    assert got == {"sheet": True, "searchFocused": False, "panelFocused": True}


def test_an_open_switcher_closes_when_the_crumb_is_pressed_again():
    got = run(SETUP + 'pk.openPicker(anchor, env); console.log(JSON.stringify(document.key("Escape").defaultPrevented));')
    assert got is False


def test_visiting_models_fills_recent_and_other_pages_do_not():
    got = run(SETUP + """
      const s = { getItem: (k) => memory2[k] ?? null, setItem: (k, v) => { memory2[k] = v; } }, memory2 = {};
      for (const r of [{ page: "model", model: "a", tab: "overview" }, { page: "keys" }, { page: "model", model: "b", tab: "logs" }, { page: "home" }, { page: "model", model: "a", tab: "logs" }]) pk.rememberVisit(r, s);
      console.log(JSON.stringify(prefs.readRecent(s)));
    """)
    assert got == ["a", "b"]


# -- review round 1 ---------------------------------------------------------------------------------
def test_the_search_is_a_combobox_over_one_listbox_and_points_at_the_highlighted_card():
    got = run(SETUP + """
      const attrs = Object.fromEntries(["role", "aria-controls", "aria-expanded", "aria-autocomplete", "aria-haspopup"].map((k) => [k, input().getAttribute(k)]));
      document.key("ArrowDown");
      const lists = panel().querySelectorAll(".plist").concat(panel().querySelectorAll(".pgrid")).map((e) => e.getAttribute("role"));
      const kbCard = panel().querySelectorAll(".pr").filter((c) => c.classList.contains("kb"))[0];
      const target = panel().querySelectorAll(".plist")[0];
      console.log(JSON.stringify({ attrs, listbox: [target.getAttribute("id"), target.getAttribute("role")], lists, active: input().getAttribute("aria-activedescendant") === kbCard.getAttribute("id"),
        groups: panel().querySelectorAll(".plist")[0].children.map((g) => g.getAttribute("aria-label")) }));
    """)
    assert got["attrs"] == {"role": "combobox", "aria-controls": "pk-list", "aria-expanded": "true", "aria-autocomplete": "list", "aria-haspopup": "listbox"}
    assert got["listbox"] == ["pk-list", "listbox"] and got["lists"] == ["listbox", None, None, None, None] and got["active"] is True
    assert got["groups"] == ["Recent", "Chat · 2", "Coder · 1", "Vision · 1"]


def test_a_poll_that_changes_nothing_leaves_the_cards_and_scroll_alone_and_a_change_does_not_scroll():
    got = run(SETUP + """
      let scrolls = 0;
      Object.getPrototypeOf(anchor).scrollIntoView = () => { scrolls++; };
      document.key("ArrowDown");              // the keyboard moved: this one scrolls
      const afterKey = scrolls;
      const list = panel().querySelector(".plist");
      list.scrollTop = 120;
      const before = panel().querySelectorAll(".pr");
      core.emitData(); core.emitData();
      const same = panel().querySelectorAll(".pr").every((c, i) => c === before[i]);
      core.store.metrics = { by_model: { alpha: { decode_tps_p50: 77 } } };   // a real change: the card text moves, the scroll does not
      core.emitData();
      console.log(JSON.stringify({ afterKey, scrolls, same, top: list.scrollTop, text: panel().querySelectorAll(".pr").filter((c) => c.getAttribute("data-pick") === "alpha")[0].textContent }));
    """)
    assert got == {"afterKey": 1, "scrolls": 1, "same": True, "top": 120, "text": "alphaChat#work77 tok/s"}


def test_a_poll_during_a_drag_does_not_rebuild_the_dragged_card_and_catches_up_after():
    got = run(SETUP + """
      const card = panel().querySelectorAll(".pr")[1];
      card.dispatch("dragstart", { dataTransfer: { setData() {}, effectAllowed: "" } });
      core.store.models = [...core.store.models, { name: "late", state: "cold", kind: "chat", labels: [] }]; core.emitData();
      const during = { kept: card.isConnected, late: cardNames().includes("late") };
      card.dispatch("dragend");
      console.log(JSON.stringify({ during, after: cardNames().includes("late") }));
    """)
    assert got == {"during": {"kept": True, "late": False}, "after": True}


def test_recent_is_a_group_in_the_column_with_its_count_and_filters_in_recent_order():
    got = run(SETUP + """
      const item = panel().querySelector('[data-group="recent"]');
      item.click();
      console.log(JSON.stringify({ label: item.textContent, heads: heads(), cards: cardNames() }));
    """)
    assert got == {"label": "Recent2", "heads": ["Recent · 2"], "cards": ["gamma", "alpha"]}


def test_my_models_has_no_recent_group_in_its_column():
    got = run(SETUP.replace("await open();", "") + """
      const l = lib.createLibrary({ onChange() {} });
      l.update();
      console.log(JSON.stringify(l.groups.querySelectorAll(".gi").map((g) => g.getAttribute("data-group")).filter((g) => g === "recent")));
    """)
    assert got == []


def test_escape_in_the_new_label_field_does_not_close_the_switcher_and_in_the_search_clears_first_then_closes():
    got = run(SETUP + """
      panel().querySelector('[data-act="new-label"]').click();
      const field = panel().querySelectorAll("input").filter((i) => i.getAttribute("aria-label") === "New label name")[0]; field.focus();
      const inField = document.key("Escape");
      const stillOpen = !!panel() && panel().classList.contains("open");
      type("gam"); input().focus();
      const first = document.key("Escape");
      const afterFirst = { value: input().value, cards: cardNames().length, open: panel().classList.contains("open") };
      const second = document.key("Escape");
      console.log(JSON.stringify({ inFieldPrevented: inField.defaultPrevented, stillOpen, afterFirst, secondClosed: second.defaultPrevented, closedClass: panel().classList.contains("open") }));
    """)
    assert got == {"inFieldPrevented": False, "stillOpen": True, "afterFirst": {"value": "", "cards": 6, "open": True}, "secondClosed": True, "closedClass": False}


def test_on_a_phone_shift_tab_from_the_panel_goes_to_the_last_control_and_slash_focuses_the_search():
    got = run(SETUP.replace("await open();", "") + """
      app.getBoundingClientRect = () => ({ left: 0, top: 0, bottom: 800, width: 390, height: 800 });
      await open();
      const back = document.key("Tab", { shiftKey: true });
      const last = document.activeElement.getAttribute("data-act");
      panel().focus();
      const fwd = document.key("Tab");
      const first = document.activeElement === input();
      panel().focus();
      document.key("/");
      console.log(JSON.stringify({ last, first, slash: document.activeElement === input() }));
    """)
    assert got == {"last": "add-model", "first": True, "slash": True}


def test_up_and_down_move_by_row_and_left_right_by_one_card():
    got = run(SETUP + """
      globalThis.getComputedStyle = () => ({ gridTemplateColumns: "100px 100px" });
      const at = () => panel().querySelectorAll(".pr").findIndex((c) => c.classList.contains("kb"));
      const path = [at()];                       // Recent: gamma alpha | Chat: alpha gamma | Coder: beta | Vision: delta
      for (const k of ["ArrowDown", "ArrowDown", "ArrowDown", "ArrowDown", "ArrowUp", "ArrowUp", "ArrowUp", "ArrowRight", "ArrowLeft", "ArrowLeft"]) {
        if (k === "ArrowRight" || k === "ArrowLeft") input().blur();
        if (k === "ArrowRight" || k === "ArrowLeft") panel().focus();
        document.key(k); path.push(at());
      }
      console.log(JSON.stringify(path));
    """)
    assert got == [1, 3, 4, 5, 5, 4, 2, 0, 1, 0, 0]


# -- opening it from the keyboard --------------------------------------------------------------------
def test_slash_and_ctrl_k_open_it_on_a_model_page_but_slash_never_steals_typing():
    got = run(SETUP.replace("await open();", "") + """
      pk.listenForSwitcher(() => anchor, env);
      const typingField = document.createElement("input"); document.body.append(typingField); typingField.focus();
      const whileTyping = document.key("/").defaultPrevented;
      const ctrlK = document.key("k", { ctrlKey: true });
      const viaCtrlK = !!panel();
      document.key("Escape");
      document.activeElement = anchor;
      const slash = document.key("/");
      console.log(JSON.stringify({ whileTyping, ctrlK: ctrlK.defaultPrevented, viaCtrlK, slash: slash.defaultPrevented, viaSlash: !!panel() }));
    """)
    assert got == {"whileTyping": False, "ctrlK": True, "viaCtrlK": True, "slash": True, "viaSlash": True}


def test_ctrl_k_opens_from_anywhere_except_a_multi_line_editor():
    got = run(SETUP.replace("await open();", "") + """
      pk.listenForSwitcher(() => anchor, env);
      const area = document.createElement("textarea"); document.body.append(area); area.focus();
      const inTextarea = document.key("k", { ctrlKey: true });
      const editable = document.createElement("div"); editable.isContentEditable = true; document.body.append(editable); editable.focus();
      const inEditable = document.key("k", { metaKey: true });
      const line = document.createElement("input"); document.body.append(line); line.focus();
      const inInput = document.key("k", { ctrlKey: true });
      console.log(JSON.stringify({ textarea: inTextarea.defaultPrevented, editable: inEditable.defaultPrevented, input: inInput.defaultPrevented, open: !!panel() }));
    """)
    assert got == {"textarea": False, "editable": False, "input": True, "open": True}


def test_the_keys_do_nothing_off_a_model_page():
    got = run(SETUP.replace("await open();", "") + """
      route = { page: "home" };
      pk.listenForSwitcher(() => anchor, env);
      document.activeElement = anchor;
      const a = document.key("/"), b = document.key("k", { metaKey: true });
      console.log(JSON.stringify({ prevented: [a.defaultPrevented, b.defaultPrevented], open: !!panel() }));
    """)
    assert got == {"prevented": [False, False], "open": False}


# -- "+ label" on a model's Overview -----------------------------------------------------------------
def test_plus_label_adds_a_new_label_to_the_model_at_once_and_asks_the_daemon():
    got = run(SETUP.replace("await open();", "") + """
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
    got = run(SETUP.replace("await open();", "") + """
      const ed = lib.labelsEditor(() => "beta");
      ed.querySelector('[data-act="add-label"]').click();
      const first = ed.querySelector("input");
      await tick(); await tick();
      console.log(JSON.stringify({ same: ed.querySelector("input") === first, suggestions: ed.querySelectorAll("option").map((o) => o.getAttribute("value")) }));
    """)
    assert got == {"same": True, "suggestions": ["work", "later"]}


def test_removing_a_label_takes_it_off_and_a_refusal_puts_it_back_with_the_reason():
    got = run(SETUP.replace("await open();", "") + """
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
