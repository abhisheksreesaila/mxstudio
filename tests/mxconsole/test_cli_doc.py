"""docs/CLI.md is the command reference: every command and every long option (both forms of a --x/--no-x flag) must
have its section and options-table row, and the doc must not describe commands or options that no longer exist.
Short aliases (-n, -f) are not checked."""
import re
from pathlib import Path

import typer

from mxconsole.cli import app

DOC = (Path(__file__).parents[2] / "docs" / "CLI.md").read_text()
HEADING = re.compile(r"^### `mxc ([a-z -]+)`", re.M)
OPTION = re.compile(r"`(--[a-z][a-z-]*)")


def commands() -> dict:
    out = {}
    for name, cmd in typer.main.get_command(app).commands.items():
        subs = getattr(cmd, "commands", None)
        out.update({f"{name} {s}": c for s, c in subs.items()} if subs else {name: cmd})
    return out


def long_opts(cmd) -> set[str]:
    return {o for p in cmd.params for o in [*p.opts, *p.secondary_opts] if o.startswith("--") and o != "--help"}


def table_opts(name: str) -> set[str]:
    m = re.search(rf"^### `mxc {re.escape(name)}`$(.*?)(?=^##)", DOC + "\n##", re.M | re.S)
    assert m, f"docs/CLI.md has no '### `mxc {name}`' section"
    rows = [line.split("|")[1] for line in m.group(1).splitlines() if line.startswith("| `")]
    return {o for row in rows for o in OPTION.findall(row)}


def test_every_command_has_a_section_and_every_section_a_command():
    assert set(HEADING.findall(DOC)) == set(commands())


def test_each_sections_options_table_matches_the_command():
    for name, cmd in commands().items():
        assert table_opts(name) == long_opts(cmd), f"`mxc {name}`: options table differs from the command"
