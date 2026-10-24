"""F-044: blades in omxqua. What a user sees: floating blades that settle in, flat buttons, a laptop for Local, one filled verb."""
import re

import pytest

from mxconsole.gateway import CONSOLE_DIR
from test_console_blades import needs_node, node

pytestmark = needs_node
CSS = lambda name: (CONSOLE_DIR / name).read_text()  # noqa: E731


def test_the_layout_spends_only_what_the_floating_blades_leave():
    # 14px margins on both sides and a 12px gap between neighbours
    assert node("m.spendable(1280, 1)", "blade-layout.js") == 1252
    assert node("m.spendable(1280, 3)", "blade-layout.js") == 1228
    assert node("m.spendable(1280, 0)", "blade-layout.js") == 1252


def test_local_models_get_a_laptop_and_secure_ones_a_lock():
    assert node("m.accessIcon('local')", "blade-home.js") == "laptop"
    assert node("m.accessIcon('secure')", "blade-home.js") == "lock"


@pytest.mark.parametrize("state", ["ready", "cold", "crashed", "starting"])
@pytest.mark.parametrize("secure", [False, True])
def test_a_command_bar_has_exactly_one_filled_verb_and_it_is_the_power_one(state, secure):
    cmds = node(f"m.commandsFor('{state}', {str(secure).lower()})", "blade-model.js")
    filled = [c["key"] for c in cmds if c.get("primary")]
    assert len(filled) == 1 and filled[0] in ("start", "stop", "restart")
    assert filled[0] == cmds[0]["key"]


def test_a_blade_arrives_with_settle_a_12px_rise_and_no_sideways_slide():
    css = CSS("blades.css")
    frames = re.search(r"@keyframes bladeIn \{.*", css)
    assert frames, "bladeIn keyframes missing"
    body = frames.group(0)
    assert "translateY(12px)" in body and "translateX" not in body
    assert re.search(r"\.blade\.enter \{[^}]*animation: bladeIn \.28s cubic-bezier\(\.2, ?0, ?0, ?1\)", css)


def test_blades_float_on_surface_1_under_the_islands():
    css = CSS("blades.css")
    journey = re.search(r"\.journey \{([^}]*)\}", css).group(1)
    assert "flex: 1" in journey and "padding: 0 14px 14px" in journey  # F-052: one page beside the nav, not a row of blades
    blade = re.search(r"\n\.blade \{([^}]*)\}", css).group(1)
    assert "var(--card)" in blade and "var(--oq-radius-card)" in blade and "var(--oq-shadow-1)" in blade
    assert re.search(r"--top: 72px", css)


def test_omxquas_sidebar_rule_cannot_pad_or_float_a_docked_blade():
    css = CSS("blades.css")
    rule = re.search(r"\.blade\.side \{([^}]*)\}", css)
    assert rule and "padding: 0" in rule.group(1) and "gap: 0" in rule.group(1)


def test_every_blade_is_a_flat_button_panel_and_close_is_the_standard_round_x():
    js = CSS("blades.js")
    assert "oq-flat" in js
    x = re.search(r"\n\.x \{([^}]*)\}", CSS("blades.css")).group(1)
    assert "width: 32px" in x and "height: 32px" in x and "border-radius: 50%" in x
