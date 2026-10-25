"""F-052 (ADR 0005): the console is plain pages: routes by hash, one page at a time. (F-061 moved the places into the top bar: test_console_topbar.py.)"""
import re

from test_gateway import http, key, store, sup  # noqa: F401  (shared fixtures)

from mxconsole.gateway import CONSOLE_ASSETS, CONSOLE_DIR

NEW_FILES = ["pages.js", "nav.js", "shell.css", "topbar.css"]


def text(name: str) -> str:
    return (CONSOLE_DIR / name).read_text()


async def test_the_new_files_are_allowlisted_and_served(http):
    for name in NEW_FILES:
        assert name in CONSOLE_ASSETS, name
        r = await http.get(f"/console/{name}")
        assert r.status_code == 200, name


def test_the_old_top_bar_tools_are_gone_and_the_theme_lives_in_settings():
    bar = text("topbar.js")
    assert "blades.js" not in bar and "cx-tools" not in bar and "setTheme" not in bar
    assert "Appearance" in text("blade-settings.js") and "top bar" not in text("blade-settings.js")


def test_main_routes_pages_and_no_longer_stacks_blades():
    main = text("main.js")
    assert "startPages" in main and "mountJourney" not in main and "push(" not in main
    pages = text("pages.js")
    assert "setNavigator" in pages and "hashchange" in pages and "history.replaceState" in pages  # unknown routes are tidied, not stacked


def test_back_only_uses_history_when_the_previous_entry_is_in_the_console():
    """The decision is routes.backUsesHistory (tested under node); pages.js stamps entries and asks it."""
    pages = text("pages.js")
    assert "backUsesHistory(history.state) ? history.back() : go(parent)" in pages
    assert 'history.replaceState({ mxc: 0 }, "")' in pages and 'history.replaceState({ mxc: 1 }, "")' in pages
