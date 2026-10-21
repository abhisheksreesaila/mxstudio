"""F-045: the other blades in omxqua. What ships: omxqua parts, flat buttons, no Graphite-era leftovers."""
import re

import pytest

from mxconsole.gateway import CONSOLE_DIR

NAMES = ["add", "security", "keys", "bench", "logs", "notebook", "gateway", "settings"]
JS = {n: (CONSOLE_DIR / f"blade-{n}.js").read_text() for n in NAMES}
CSS = {n: (CONSOLE_DIR / f"blade-{n}.css").read_text() for n in NAMES}
OMXQUA = (CONSOLE_DIR / "omxqua" / "src" / "components.css").read_text()


def class_tokens(js: str) -> set[str]:
    """Every class name written as class: "a b" or class: `a ${x} b` in a blade."""
    found = set()
    for m in re.finditer(r"""\bclass:\s*(?:"([^"]*)"|`([^`]*)`)""", js):
        text = re.sub(r"\$\{[^}]*\}", " ", m.group(1) or m.group(2))
        found.update(t for t in text.split() if re.fullmatch(r"[A-Za-z][\w-]*", t))
    for m in re.finditer(r"""className\s*=\s*(?:"([^"]*)"|`([^`]*)`)""", js):
        text = re.sub(r"\$\{[^}]*\}", " ", m.group(1) or m.group(2))
        found.update(t for t in text.split() if re.fullmatch(r"[A-Za-z][\w-]*", t))
    found.update(re.findall(r"""classList\.(?:add|toggle|remove)\("([A-Za-z][\w-]*)\"""", js))
    return found


@pytest.mark.parametrize("name", NAMES)
def test_buttons_use_the_omxqua_variants_and_no_graphite_class_is_left(name):
    tokens = class_tokens(JS[name])
    old = {"dark", "ghost", "primary", "port", "ib", "cli", "h3", "notice", "attn", "lbl", "chip-old", "hue-chip"}
    assert not (tokens & old), sorted(tokens & old)
    buttons = [t for t in tokens if t in {"fill", "tonal", "text", "danger"}]
    assert buttons, name


@pytest.mark.parametrize("name", NAMES)
def test_a_blade_is_flat_and_built_from_cards(name):
    js = JS[name]
    assert 'classList.add("oq-flat")' in js, "omxqua's flat panel: no shadow or lift on buttons inside a blade"
    assert "card" in class_tokens(js)
    assert "src" in class_tokens(js), "a card ends in its mono source line"


def test_every_stylesheet_class_is_used_by_a_blade_or_by_omxqua():
    """Graphite-era CSS nothing uses any more is deleted."""
    used = set()
    for n in NAMES:
        used |= class_tokens(JS[n])
    # classes set through classList / template parts / data, or reused from other files, are listed by spelling in the JS
    for n in NAMES:
        css = re.sub(r"/\*.*?\*/", "", CSS[n], flags=re.S)
        for sel in re.findall(r"\.([A-Za-z][\w-]*)", re.sub(r"\{[^}]*\}", " ", css)):
            if re.match(r"b-(add|security|keys|bench|logs|notebook|gateway|settings)$", sel) or sel in {"blade", "bh", "oq-flat", "lv2"}:
                continue  # the blade frame's own classes
            assert sel in used or sel in OMXQUA or re.search(rf"""["'`\s]{sel}["'`\s:]""", " ".join(JS.values())), (n, sel)  # state names (done, tight, hf) are written in the JS as data


@pytest.mark.parametrize("name", NAMES)
def test_no_raw_hex_in_the_blade_stylesheets(name):
    # code panes use the --code tokens; every other colour is an omxqua token
    assert not re.findall(r"#[0-9a-fA-F]{3,6}\b", CSS[name])
