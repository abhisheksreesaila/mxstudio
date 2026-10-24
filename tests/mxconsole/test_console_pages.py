"""F-052 (ADR 0005): the console is plain pages. One nav with five places, a top bar that says where you are, routes by hash."""
import re

from test_gateway import http, key, store, sup  # noqa: F401  (shared fixtures)

from mxconsole.gateway import CONSOLE_ASSETS, CONSOLE_DIR

NEW_FILES = ["pages.js", "nav.js", "shell.css"]


def text(name: str) -> str:
    return (CONSOLE_DIR / name).read_text()


async def test_the_new_files_are_allowlisted_and_served(http):
    for name in NEW_FILES:
        assert name in CONSOLE_ASSETS, name
        r = await http.get(f"/console/{name}")
        assert r.status_code == 200, name


async def test_the_page_is_a_nav_beside_a_top_bar_and_one_page(http):
    page = (await http.get("/console")).text
    assert 'href="/console/shell.css"' in page
    assert re.search(r'<nav class="cx-nav" id="nav"', page)
    assert 'id="topbar"' in page and 'id="journey"' in page
    assert page.index('id="nav"') < page.index('id="topbar"') < page.index('id="journey"')  # one DOM: rail on the left, tab bar below at phone width


def test_the_nav_has_the_five_places_once_each_with_a_hash():
    js = text("nav.js")
    places = re.findall(r'\["(\w+)", "(\w+)", "(#/\w*)"', js)
    assert places == [("home", "Models", "#/"), ("keys", "Keys", "#/keys"), ("gateway", "Gateway", "#/gateway"),
                      ("security", "Security", "#/security"), ("settings", "Settings", "#/settings")]
    assert "aria-current" in js


def test_nav_targets_are_at_least_40px_in_both_shapes():
    css = text("shell.css")
    rail = re.search(r"\.cx-nav \.it \{([^}]*)\}", css).group(1)
    assert "height: 40px" in rail and "min-height: 40px" in rail
    phone = css[css.index("@container (max-width: 760px)"):]
    assert re.search(r"\.cx-nav \.it \{[^}]*height: 54px", phone)
    assert "flex-direction: row" in phone and "order: 2" in phone  # the same nav, as a bar below the page


def test_the_old_top_bar_tools_are_gone_and_the_theme_lives_in_settings():
    bar = text("topbar.js")
    assert "blades.js" not in bar and "cx-tools" not in bar and "setTheme" not in bar
    assert "Appearance" in text("blade-settings.js") and "top bar" not in text("blade-settings.js")


def test_main_routes_pages_and_no_longer_stacks_blades():
    main = text("main.js")
    assert "startPages" in main and "mountJourney" not in main and "push(" not in main
    pages = text("pages.js")
    assert "setNavigator" in pages and "hashchange" in pages and "history.replaceState" in pages  # unknown routes are tidied, not stacked


def test_where_you_are_is_text_not_a_button_and_the_picker_chevron_waits_for_the_picker():
    js = text("topbar.js")
    assert 'h("span", { class: `cur ${cls}`, "aria-current": "page" }' in js
    assert "chevron-down" not in js and 'aria-current": i === last' not in js


def test_back_only_uses_history_when_the_previous_entry_is_in_the_console():
    pages = text("pages.js")
    assert "history.state" in pages and "steps" not in pages
