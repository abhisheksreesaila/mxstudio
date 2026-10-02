"""`mst`: the MX Studio command line."""
from __future__ import annotations

import time

import typer

from mst import keys
from mst.store import Store, format_duration

app = typer.Typer(help="MX Studio: local models on Modular MAX you can see into and reshape.",
                  no_args_is_help=True, add_completion=False)


keys_app = typer.Typer(help="Per-app API keys.", no_args_is_help=True)
app.add_typer(keys_app, name="keys")


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


@keys_app.command("new")
def keys_new(app_name: str = typer.Argument(..., metavar="APP"),
             rotate: bool = typer.Option(False, "--rotate", help="Revoke the app's current key and issue a new one.")) -> None:
    """Create a key for an app. It is printed once."""
    try:
        key = keys.create_key(Store.open(), app_name, rotate=rotate)
    except keys.KeyExists as e:
        typer.echo(str(e))
        raise typer.Exit(1)
    typer.echo(key)
    typer.echo("This key will not be shown again. Copy it now.")


@keys_app.command("ls")
def keys_ls() -> None:
    """List keys (never the secret)."""
    rows = keys.list_keys(Store.open())
    if not rows:
        typer.echo("no keys yet. Try: mst keys new <app>")
        return

    def when(t: float | None) -> str:
        return "never" if t is None else time.strftime("%Y-%m-%d %H:%M", time.localtime(t))

    typer.echo(f"{'APP':<20} {'PREFIX':<13} {'CREATED':<17} {'LAST USED':<17} STATUS")
    for k in rows:
        status = "active" if k.revoked_at is None else "revoked"
        typer.echo(f"{k.app:<20} {k.prefix:<13} {when(k.created_at):<17} {when(k.last_used_at):<17} {status}")


@keys_app.command("revoke")
def keys_revoke(app_name: str = typer.Argument(..., metavar="APP")) -> None:
    """Disable an app's key."""
    if not keys.revoke_key(Store.open(), app_name):
        typer.echo(f"no active key for {app_name}")
        raise typer.Exit(1)
    typer.echo(f"revoked the key for {app_name}")


def main() -> None:
    app()
