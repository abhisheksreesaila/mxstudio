"""`mst`: the MX Studio command line."""
from __future__ import annotations

import subprocess

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


@app.command("learn")
def learn_model(
    model: str = typer.Argument(..., help="A registered model name or a Hugging Face repo id."),
    no_open: bool = typer.Option(False, "--no-open", help="Write the notebook but don't launch Jupyter Lab."),
) -> None:
    """Build a notebook about a model from Hugging Face metadata and open it in Jupyter Lab."""
    from mst import learn
    try:
        path = learn.write_notebook(model)
    except Exception as exc:
        typer.echo(f"could not build a notebook for {model}: {exc}", err=True)
        raise typer.Exit(1)
    typer.echo(f"wrote {path}")
    if not no_open:
        subprocess.Popen(["jupyter", "lab", str(path)], start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        typer.echo("opening in Jupyter Lab")


def main() -> None:
    app()
