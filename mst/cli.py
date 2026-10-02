"""`mst`: the MX Studio command line."""
from __future__ import annotations

import time
import subprocess

import typer

from mst import expose, keys
from mst import resolve
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
@app.command("pull")
def pull(ref: str = typer.Argument(..., help="Short name (qwen2.5-0.5b) or Hugging Face repo id"),
         dry_run: bool = typer.Option(False, "--dry-run", help="Only resolve; download nothing")) -> None:
    """Resolve a model, download it at a pinned revision and register it."""
    if dry_run:
        res = resolve.resolve(ref, catalog=resolve.load_catalog(), hf=resolve.HubClient())
        typer.echo(f"{res.kind}: {res.message}")
        raise typer.Exit(0 if res.supported else 1)
    try:
        model = resolve.pull(ref)
    except resolve.PullError as e:
        typer.echo(e.resolution.message)
        raise typer.Exit(1)
    typer.echo(f"pulled {model.name} ({model.repo_id}@{model.revision[:8]}, {model.source})")
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
        try:
            subprocess.Popen(["jupyter", "lab", str(path)], start_new_session=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            typer.echo(f"jupyter not found. Open it yourself: pixi run jupyter lab {path}")
            return
        typer.echo("opening in Jupyter Lab")


@app.command("expose")
def expose_cmd(host: str = typer.Option(None, help="Hostname clients use. Default: <this machine>.local"),
               litellm: bool = typer.Option(True, "--litellm/--no-litellm", help="Route through LiteLLM."),
               dry_run: bool = typer.Option(False, "--dry-run", help="Write the configs; start nothing."),
               stop: bool = typer.Option(False, "--stop", help="Stop Caddy and LiteLLM.")) -> None:
    """Serve your models on the LAN over HTTPS, with per-app keys."""
    if stop:
        expose.stop()
        typer.echo("stopped Caddy and LiteLLM")
        return
    host = host or expose.default_host()
    if dry_run:
        configs = expose.write_configs(host=host, litellm=litellm)
    else:
        configs = expose.start(host=host, litellm=litellm)
    typer.echo(f"endpoint   https://{host}:{expose.EXPOSE_PORT}/v1")
    typer.echo(f"configs    {configs['caddyfile']}  {configs['litellm']}")
    if litellm:
        typer.echo(f"litellm    http://127.0.0.1:{expose.LITELLM_PORT}/ui (this machine only)")
    typer.echo("keys       mst keys new <app>   (send it as: Authorization: Bearer <key>)")
    typer.echo(f"trust      copy {expose.ca_root_path()} to each client device and trust it once")
    typer.echo("           (Linux: sudo trust anchor root.crt · macOS: open it in Keychain Access, set Always Trust)")


def main() -> None:
    app()
