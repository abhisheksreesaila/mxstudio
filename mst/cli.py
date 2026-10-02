"""`mst`: the MX Studio command line."""
from __future__ import annotations

import typer

from mst.store import Store, format_duration

app = typer.Typer(help="MX Studio: local models on Modular MAX you can see into and reshape.",
                  no_args_is_help=True, add_completion=False)


@app.callback()
def _root() -> None:
    """Keeps `mst` a command group even while it has one command."""


@app.command("ls")
def list_models() -> None:
    """List registered models."""
    models = Store.open().models()
    if not models:
        typer.echo("no models yet. Try: mst serve qwen2.5-0.5b")
        return
    typer.echo(f"{'NAME':<24} {'SOURCE':<13} {'PINNED':<7} {'KEEP-ALIVE':<10} REPO")
    for m in models:
        keep = "-" if m.pinned else format_duration(m.keep_alive)
        typer.echo(f"{m.name:<24} {m.source:<13} {'yes' if m.pinned else 'no':<7} {keep:<10} {m.repo_id}")


def main() -> None:
    app()
