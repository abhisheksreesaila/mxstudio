"""F-061: the places live in the top bar, the breadcrumb is a plain trail, and `/` or Ctrl/Cmd+K jump to the My models search.
Run for real under node on the tiny fake DOM (tests/mxconsole/fake_dom.mjs); every assert is what a person would see or get."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from test_gateway import http, key, store, sup  # noqa: F401  (shared fixtures)

from mxconsole.gateway import CONSOLE_ASSETS, CONSOLE_DIR

needs_node = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
FAKE = Path(__file__).with_name("fake_dom.mjs").as_uri()


def run(body: str):
    script = f"""
      import {json.dumps(FAKE)};
      const core = await import({json.dumps((CONSOLE_DIR / 'core.js').as_uri())});
      const nav = await import({json.dumps((CONSOLE_DIR / 'nav.js').as_uri())});
      const tick = () => new Promise((r) => setTimeout(r, 0));
      {body}
    """
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def text(name: str) -> str:
    return (CONSOLE_DIR / name).read_text()


# -- the page: no rail, the switcher's files are gone --------------------------------------------------
async def test_the_page_has_a_top_bar_and_the_pages_but_no_side_rail(http):
    page = (await http.get("/console")).text
    assert 'id="topbar"' in page and 'id="journey"' in page
    assert 'id="nav"' not in page and "cx-nav" not in page
    assert "picker.css" not in page


async def test_the_switcher_files_are_neither_allowlisted_nor_served(http):
    for name in ("picker.js", "picker.css"):
        assert name not in CONSOLE_ASSETS
        assert (await http.get(f"/console/{name}")).status_code == 404
        assert not (CONSOLE_DIR / name).exists()


def test_nothing_in_the_console_remembers_recent_models():
    everything = "".join(p.read_text() for p in CONSOLE_DIR.glob("*") if p.suffix in {".js", ".css", ".html"})
    assert "mxc-recent" not in everything and "pushRecent" not in everything and "switcherSections" not in everything


# -- the four places ------------------------------------------------------------------------------------
PLACES = """
  const listeners = new Set();
  let route = { page: "home" };
  const env = { onRoute: (fn) => { listeners.add(fn); fn(route); return () => listeners.delete(fn); } };
  const go = (r) => { route = r; listeners.forEach((fn) => fn(r)); };
  const box = nav.placesEl(env);
  document.body.append(box);
  const links = () => box.querySelector(".places").querySelectorAll("a");
  const here = () => links().filter((a) => a.getAttribute("aria-current") === "page").map((a) => a.textContent);
  const more = box.querySelector(".more");
  const menu = () => box.querySelector(".places");
  const state = () => [menu().classList.contains("open"), more.getAttribute("aria-expanded")];
"""


@needs_node
def test_the_four_places_are_links_with_a_word_in_the_top_bar_and_the_current_one_is_marked():
    got = run(PLACES + """
      const seen = {};
      for (const r of [{ page: "home" }, { page: "keys" }, { page: "security" }, { page: "settings" }, { page: "model", model: "x", tab: "logs" }, { page: "add" }]) {
        go(r); seen[r.page] = here();
      }
      console.log(JSON.stringify({ links: links().map((a) => [a.textContent, a.getAttribute("href")]), seen }));
    """)
    assert got["links"] == [["Keys", "#/keys"], ["Gateway", "#/gateway"], ["Security", "#/security"], ["Settings", "#/settings"]]
    assert got["seen"] == {"home": [], "keys": ["Keys"], "security": ["Security"], "settings": ["Settings"], "model": [], "add": []}


@needs_node
def test_the_menu_button_opens_and_closes_the_places():
    got = run(PLACES + """
      const out = { start: state() };
      more.click(); out.opened = state();
      more.click(); out.toggled = state();
      console.log(JSON.stringify(out));
    """)
    assert got == {"start": [False, "false"], "opened": [True, "true"], "toggled": [False, "false"]}


@needs_node
def test_escape_an_outside_click_and_a_new_page_close_the_menu_but_a_click_inside_does_not():
    got = run(PLACES + """
      const outside = document.createElement("div"); document.body.append(outside);
      const out = {};
      more.click(); document.key("Escape"); out.esc = state();
      more.click(); document.click(outside); out.outside = state();
      more.click(); document.click(links()[0]); out.inside = state();
      go({ page: "keys" }); out.route = state();
      more.click(); document.key("a"); out.otherKey = state();
      console.log(JSON.stringify(out));
    """)
    assert got == {"esc": [False, "false"], "outside": [False, "false"], "inside": [True, "true"], "route": [False, "false"], "otherKey": [True, "true"]}


@needs_node
def test_the_menu_is_a_disclosure_opening_moves_focus_to_the_first_link_and_escape_returns_it_to_the_button():
    got = run(PLACES + """
      const out = { attrs: [more.getAttribute("aria-controls"), menu().getAttribute("id"), more.getAttribute("aria-haspopup"), menu().getAttribute("role")] };
      more.click(); out.opened = document.activeElement === links()[0];
      document.key("Escape"); out.back = document.activeElement === more;
      more.click(); more.click(); out.toggledOff = document.activeElement === more;
      more.click(); document.click(document.body); out.outside = [state()[0], document.activeElement === links()[0]];
      console.log(JSON.stringify(out));
    """)
    assert got == {"attrs": ["places", "places", None, None], "opened": True, "back": True, "toggledOff": True, "outside": [False, True]}


@needs_node
def test_each_place_link_has_its_own_name_so_the_icon_only_state_still_reads():
    got = run(PLACES + 'console.log(JSON.stringify(links().map((a) => [a.getAttribute("aria-label"), a.getAttribute("title")])));')
    assert got == [[n, n] for n in ("Keys", "Gateway", "Security", "Settings")]


def test_the_places_fold_to_icons_under_960px_and_into_a_menu_with_44px_targets_under_760px():
    css = text("topbar.css")
    assert re.search(r"@container \(max-width: 960px\) \{[^}]*\.places a span \{ display: none", css)
    wide = css[: css.index("@container (max-width: 960px)")]
    assert re.search(r"\.bar \.more \{[^}]*display: none", wide)
    narrow = css[css.index("@container (max-width: 760px)"):]
    assert re.search(r"\.places a \{[^}]*height: 44px", narrow) and re.search(r"\.bar \.more \{[^}]*display: inline-flex", narrow)
    assert re.search(r"\.bar \.more \{[^}]*width: 44px[^}]*height: 44px|\.bar \.more \{[^}]*height: 44px[^}]*width: 44px", narrow)
    assert css.count("{") == css.count("}")


# -- the breadcrumb is a plain trail ---------------------------------------------------------------------
TRAIL = """
  const trail = (r) => { const el = document.createElement("nav"); nav.renderCrumbs(el, r); return el; };
  const describe = (el) => el.children.map((c) => c.tagName === "A" ? ["link", c.textContent, c.getAttribute("href")]
    : c.classList.contains("sep") ? ["sep"] : ["text", c.textContent, c.getAttribute("aria-current")]);
"""


@needs_node
def test_a_model_trail_is_a_link_back_a_separator_and_the_name_as_text_with_no_dropdown():
    got = run(TRAIL + """
      const el = trail({ page: "model", model: "org/tiny", tab: "logs" });
      console.log(JSON.stringify({ parts: describe(el), buttons: el.querySelectorAll("button").length, popups: el.querySelectorAll("[aria-haspopup]").length }));
    """)
    assert got == {"parts": [["link", "My models", "#/"], ["sep"], ["text", "org/tiny", "page"]], "buttons": 0, "popups": 0}


@needs_node
def test_the_other_trails_name_where_you_are_and_only_the_last_part_is_current():
    got = run(TRAIL + """
      console.log(JSON.stringify({ home: describe(trail({ page: "home" })), add: describe(trail({ page: "add" })),
        keys: describe(trail({ page: "keys" })), settings: describe(trail({ page: "settings" })) }));
    """)
    assert got["home"] == [["text", "My models", "page"]]
    assert got["add"] == [["link", "My models", "#/"], ["sep"], ["text", "Add a model", "page"]]
    assert got["keys"] == [["text", "API keys", "page"]] and got["settings"] == [["text", "Settings", "page"]]


@needs_node
def test_redrawing_the_trail_replaces_it_rather_than_adding_to_it():
    got = run(TRAIL + """
      const el = document.createElement("nav");
      nav.renderCrumbs(el, { page: "model", model: "a", tab: "overview" });
      nav.renderCrumbs(el, { page: "keys" });
      console.log(JSON.stringify(describe(el)));
    """)
    assert got == [["text", "API keys", "page"]]


def test_the_trail_has_no_pill_and_no_picker_is_left_in_the_styles_or_routes():
    css = text("topbar.css")
    crumbs = re.search(r"\.crumbs \{([^}]*)\}", css).group(1)
    assert "border-radius: 999px" not in crumbs and "box-shadow" not in crumbs and "background" not in crumbs
    assert "picker" not in css and "picker" not in text("routes.js") and "pickRoute" not in text("routes.js")


# -- `/` and Ctrl/Cmd+K go to My models and focus its search -------------------------------------------------
KEYS = """
  const search = document.createElement("input");
  const wentTo = [];
  let route = { page: "model", model: "m", tab: "overview" };
  document.querySelector = (s) => (route.page === "home" && s === ".psearch input" ? search : null);   // My models is the page that has the search
  const env = { currentRoute: () => route, go: (r) => { wentTo.push(r); route = r; } };
  const stop = nav.listenForSearch(env);
  const field = document.createElement("input"); const area = document.createElement("textarea"); const edit = document.createElement("div");
  edit.isContentEditable = true;
  const focused = () => document.activeElement === search;
"""


@needs_node
def test_slash_from_a_model_page_goes_to_my_models_and_focuses_its_search():
    got = run(KEYS + """
      const e = document.key("/");
      await tick(); await tick();
      console.log(JSON.stringify({ wentTo, focused: focused(), stoppedBrowser: e.defaultPrevented }));
    """)
    assert got == {"wentTo": [{"page": "home"}], "focused": True, "stoppedBrowser": True}


@needs_node
def test_ctrl_and_cmd_k_do_the_same_from_any_page_and_on_my_models_they_only_focus():
    got = run(KEYS + """
      route = { page: "keys" };
      document.key("k", { ctrlKey: true }); await tick(); await tick();
      const first = [wentTo.length, focused()];
      search.blur(); wentTo.length = 0;
      document.key("K", { metaKey: true }); await tick();
      console.log(JSON.stringify({ first, onHome: [wentTo.length, focused()] }));
    """)
    assert got == {"first": [1, True], "onHome": [0, True]}


@needs_node
def test_typing_is_never_stolen_slash_in_a_field_and_ctrl_k_in_a_textarea_or_an_editable_are_left_alone():
    got = run(KEYS + """
      const out = [];
      for (const [el, key, mods] of [[field, "/", {}], [area, "/", {}], [area, "k", { ctrlKey: true }], [edit, "k", { metaKey: true }], [document.body, "/", { ctrlKey: true }], [document.body, "a", {}]]) {
        el.focus(); const e = document.key(key, mods); out.push(e.defaultPrevented);
      }
      await tick();
      console.log(JSON.stringify({ out, wentTo }));
    """)
    assert got == {"out": [False] * 6, "wentTo": []}


@needs_node
def test_ctrl_k_in_a_one_line_field_still_jumps_and_the_listener_can_be_removed():
    got = run(KEYS + """
      field.focus(); document.key("k", { ctrlKey: true }); await tick(); await tick();
      const jumped = [wentTo.length, focused()];
      stop(); route = { page: "keys" }; wentTo.length = 0; document.body.focus();
      const e = document.key("/"); await tick();
      console.log(JSON.stringify({ jumped, after: [wentTo.length, e.defaultPrevented] }));
    """)
    assert got == {"jumped": [1, True], "after": [0, False]}
