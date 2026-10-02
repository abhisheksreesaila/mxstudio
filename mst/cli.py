"""`mst`: the MX Studio command line."""
from __future__ import annotations

import time
import subprocess
from pathlib import Path

import typer

from mst import expose, keys
from mst import resolve
from mst.store import Store, format_duration, parse_duration

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
    from mst import serve
    states = serve.live_states()
    typer.echo(f"{'NAME':<24} {'STATE':<9} {'SOURCE':<13} {'PINNED':<7} {'KEEP-ALIVE':<10} REPO")
    for m in models:
        keep = "-" if m.pinned else format_duration(m.keep_alive)
        state = states.get(m.name, "-")
        typer.echo(f"{m.name:<24} {state:<9} {m.source:<13} {'yes' if m.pinned else 'no':<7} {keep:<10} {m.repo_id}")


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
         dry_run: bool = typer.Option(False, "--dry-run", help="Only resolve; download nothing"),
         name: str = typer.Option(None, "--name", help="Register under this name"),
         port_dir: Path = typer.Option(None, "--port", help="Register a finished port (from `mst port`) instead")) -> None:
    """Resolve a model, download it at a pinned revision and register it."""
    if port_dir is not None:
        from mst import port
        try:
            model = port.register(ref, port_dir)
        except (port.PortError, resolve.PullError) as e:
            typer.echo(str(e))
            raise typer.Exit(1)
        typer.echo(f"registered port {model.name} ({model.repo_id}@{model.revision[:8]}, custom architecture {model.custom_arch_path})")
        return
    try:
        if dry_run:
            res = resolve.resolve(ref, catalog=resolve.load_catalog(), hf=resolve.HubClient())
            typer.echo(f"{res.kind}: {res.message}")
            raise typer.Exit(0 if res.supported else 1)
        model = resolve.pull(ref, name=name)
    except resolve.PullError as e:
        typer.echo(e.resolution.message)
        raise typer.Exit(1)
    except resolve.ResolveError as e:
        typer.echo(f"error: {e}")
        raise typer.Exit(1)
    typer.echo(f"pulled {model.name} ({model.repo_id}@{model.revision[:8]}, {model.source})")


@app.command("port")
def port_model(ref: str = typer.Argument(..., help="Short name or Hugging Face repo id MAX does not support yet")) -> None:
    """Set up a workspace to port an unsupported architecture to MAX with Claude Code."""
    from mst import port
    try:
        result = port.prepare(ref)
    except port.PortError as e:
        typer.echo(str(e))
        raise typer.Exit(1)
    if result.workspace is None:
        typer.echo(result.message)
        return
    typer.echo(f"Modular skills: {', '.join(result.skills)}")
    typer.echo(f"Closest supported donor: {result.donor.architecture}")
    typer.echo(f"Workspace: {result.workspace} (config.json, PORT.md, .claude/skills)")
    typer.echo("Start the port with:")
    typer.echo(f"  {result.command}")
    typer.echo(f"When it works: mst pull {ref} --port <port dir>")


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


@app.command("serve")
def serve_model(model: str = typer.Argument(..., help="Short name (qwen2.5-0.5b) or Hugging Face repo id"),
                pin: bool = typer.Option(False, "--pin", help="Keep it loaded; never unload when idle."),
                keep_alive: str = typer.Option(None, "--keep-alive", help="Unload after this idle time: 90s, 10m, 1h."),
                no_wait: bool = typer.Option(False, "--no-wait", help="Register and start the daemon; load on first request.")) -> None:
    """Pull if needed, start the daemon, load the model and print how to call it."""
    from mst import expose, serve
    store = Store.open()
    found = store.get_model(model) or next((m for m in store.models() if m.repo_id == model), None)
    if found is None:
        try:
            found = resolve.pull(model)
        except resolve.PullError as e:
            typer.echo(e.resolution.message)
            raise typer.Exit(1)
        except resolve.ResolveError as e:
            typer.echo(f"error: {e}")
            raise typer.Exit(1)
        typer.echo(f"pulled {found.name} ({found.repo_id}@{found.revision[:8]})")
        expose.refresh_if_running(store)
    name = found.name
    if pin:
        store.set_lifecycle(name, pinned=True)
    elif keep_alive:
        store.set_lifecycle(name, pinned=False, keep_alive=parse_duration(keep_alive))
    try:
        if serve.ensure_daemon():
            typer.echo("started the mst daemon")
        if not no_wait:
            typer.echo(f"loading {name} (the first load compiles the model and can take a minute)…")
            started = time.monotonic()
            serve.load(name)
            typer.echo(f"{name} is ready ({time.monotonic() - started:.1f}s)")
    except serve.DaemonError as e:
        typer.echo(f"error: {e}")
        raise typer.Exit(1)
    typer.echo("")
    typer.echo(serve.usage(name, serve.first_key(store)))


@app.command("stop")
def stop_model(model: str = typer.Argument(None, help="Model to unload"),
               all_: bool = typer.Option(False, "--all", help="Unload every model and stop the daemon.")) -> None:
    """Unload a model now, or stop everything with --all."""
    from mst import serve
    if all_:
        typer.echo("stopped the daemon and every model" if serve.shutdown() else "the daemon isn't running")
        return
    if not model:
        typer.echo("name a model, or use --all")
        raise typer.Exit(1)
    if not serve.daemon_running():
        typer.echo("the daemon isn't running; nothing is loaded")
        return
    serve.stop(model)
    typer.echo(f"unloaded {model}")


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
    try:
        if dry_run:
            configs = expose.write_configs(host=host, litellm=litellm)
        else:
            configs = expose.start(host=host, litellm=litellm)
    except (expose.ExposeError, ValueError) as e:
        typer.echo(f"error: {e}")
        raise typer.Exit(1)
    typer.echo(f"endpoint   https://{host}:{expose.EXPOSE_PORT}/v1")
    typer.echo(f"configs    {configs['caddyfile']}  {configs['litellm']}")
    if litellm:
        typer.echo(f"litellm    http://127.0.0.1:{expose.LITELLM_PORT}/ui (this machine only)")
    typer.echo("keys       mst keys new <app>   (send it as: Authorization: Bearer <key>)")
    typer.echo(f"trust      copy {expose.ca_root_path()} to each client (this machine too) and trust it once")
    typer.echo("           Linux: sudo trust anchor root.crt · or pass it as the CA bundle (curl --cacert root.crt)")


@app.command("daemon")
def daemon() -> None:
    """Run the gateway in the foreground (logs to stdout). Ctrl-C stops it and unloads every model."""
    import uvicorn

    from mst.gateway import create_app
    from mst.settings import DAEMON_HOST, DAEMON_PORT
    from mst.supervisor import Supervisor

    import os
    import shlex

    store = Store.open()
    max_command = tuple(shlex.split(os.environ.get("MST_MAX_CMD", "max")))
    port = int(os.environ.get("MST_PORT", DAEMON_PORT))
    uvicorn.run(create_app(store, Supervisor(store, max_command=max_command)), host=DAEMON_HOST, port=port,
                log_level="info")


def main() -> None:
    app()
