"""`mxc`: the mxconsole command line."""
from __future__ import annotations

import sys
import time
import subprocess
from pathlib import Path
from typing import NoReturn

import typer

from mxconsole import expose, keys
from mxconsole import report, resolve
from mxconsole.store import Store, format_duration, parse_duration

app = typer.Typer(help="omxconsole: local models on Modular MAX you can see into and reshape.",
                  no_args_is_help=True, add_completion=False,
                  epilog="Exit codes: 0 ok, 1 refused (one `error:` line on stderr), 2 bad usage.")


keys_app = typer.Typer(help="Per-app API keys.", no_args_is_help=True)
app.add_typer(keys_app, name="keys")


JSON = typer.Option(False, "--json", help="Print JSON only, for scripts and agents.")


def _fail(message: str) -> NoReturn:
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(1)


@app.callback()
def _root() -> None:
    """Keeps `mxc` a command group even while it has one command."""


@app.command("ls")
def list_models(as_json: bool = JSON) -> None:
    """List registered models."""
    from mxconsole import serve
    models = Store.open().models()
    states = serve.live_states()
    if as_json:
        typer.echo(report.dump(report.models_data(models, states, serve.daemon_running())))
        return
    if not models:
        typer.echo("no models yet. Try: mxc serve qwen2.5-0.5b")
        return
    typer.echo(f"{'NAME':<24} {'STATE':<9} {'SOURCE':<13} {'PINNED':<7} {'KEEP-ALIVE':<10} REPO")
    for m in models:
        keep = "-" if m.pinned else format_duration(m.keep_alive)
        state = states.get(m.name, "-")
        typer.echo(f"{m.name:<24} {state:<9} {m.source:<13} {'yes' if m.pinned else 'no':<7} {keep:<10} {m.repo_id}")
    typer.echo(f"\nconsole  {report.console_link('models')}")


@app.command("search")
def search_models(query: str = typer.Argument("", help="Part of a name, an architecture, or a Hugging Face repo id (owner/name)"),
                  as_json: bool = JSON) -> None:
    """Find a model (curated names, the MAX catalog, a Hugging Face repo) and see whether it fits this machine."""
    from mxconsole import search
    if len(query) > search.MAX_QUERY:
        _fail("the search text is too long")
    try:
        out = search.search(query)
    except resolve.ResolveError as e:
        _fail(str(e))
    if as_json:
        typer.echo(report.dump(out))
        return
    mem = out["memory"]
    typer.echo(f"memory: {mem['free_gb']} GB free of {mem['total_gb']} GB on {mem['name']}")
    for w in out["warnings"]:
        typer.echo(f"warning: {w}", err=True)
    if not out["results"]:
        typer.echo("no models match. Try a Hugging Face repo id (owner/name), or a shorter search.")
        return

    def num(v, unit=""):
        return "?" if v is None else f"{v:g}{unit}"
    typer.echo(f"{'ID':<34} {'FIT':<6} {'PARAMS':<8} {'SIZE':<9} {'EST VRAM':<9} {'SOURCE':<7} ARCHITECTURE")
    for r in out["results"]:
        params = "?" if r["params"] is None else f"{r['params'] / 1e9:g}B"
        typer.echo(f"{r['id'][:34]:<34} {r['fit'] or '?':<6} {params:<8} {num(r['size_gb'], ' GB'):<9} "
                   f"{num(r['est_vram_gb'], ' GB'):<9} {r['source']:<7} {r['architecture'] or '?'}")
        if r["fit"] == "port":
            typer.echo(f"{'':<34} MAX does not support this architecture yet: mxc port {r['repo_id']}")
        for s in r["suggestions"]:
            typer.echo(f"{'':<34} too big here; smaller: {s['id']} (~{s['est_vram_gb']:g} GB)")
    typer.echo("pull one with: mxc pull <id>")


@app.command("rm")
def remove_model(model: str = typer.Argument(..., help="A registered model name"),
                 purge: bool = typer.Option(False, "--purge", help="Also delete its weights from the Hugging Face cache."),
                 as_json: bool = JSON) -> None:
    """Stop a model, unregister it and delete its compiled-graph cache. Weights stay unless --purge."""
    from mxconsole import removal, serve
    store = Store.open()
    if store.get_model(model) is None:
        _fail(f"the model `{model}` is not registered. See: mxc ls")
    if serve.daemon_running():  # the daemon owns the running process: let it stop the model first
        try:
            status, body = serve.admin_call("DELETE", f"models/{model}", params={"purge": "1"} if purge else None, timeout=None)
        except serve.DaemonError as e:
            _fail(str(e))
        if status != 200:
            _fail(serve.error_message(status, body))
        result = body
    else:
        result = removal.remove(store, model, purge=purge)
    if as_json:
        typer.echo(report.dump(result))
        return
    typer.echo(f"removed {model}" + (" and deleted its weights" if result["purged"] else ""))
    if result.get("purge_note"):
        typer.echo(f"note: {result['purge_note']}")
    if not purge:
        typer.echo("its weights are still in the Hugging Face cache (mxc rm --purge deletes them too)")


@app.command("set")
def set_model(model: str = typer.Argument(..., help="A registered model name"),
              pin: bool = typer.Option(False, "--pin", help="Keep it loaded; never unload when idle."),
              unpin: bool = typer.Option(False, "--unpin", help="Let it unload when idle again."),
              keep_alive: str = typer.Option(None, "--keep-alive", help="Unload after this idle time: 90s, 10m, 1h."),
              as_json: bool = JSON) -> None:
    """Change a model's pin and keep-alive (what the console's model blade and Settings show)."""
    if pin and unpin:
        _fail("--pin and --unpin together make no sense")
    if not (pin or unpin or keep_alive):
        _fail("nothing to change. Use --pin, --unpin or --keep-alive 10m")
    try:
        seconds = parse_duration(keep_alive) if keep_alive else None
    except ValueError:
        _fail(f"--keep-alive {keep_alive!r} is not a duration (try 90s, 10m or 1h)")
    store = Store.open()
    if store.get_model(model) is None:
        _fail(f"no model named {model}. See: mxc ls")
    store.set_lifecycle(model, pinned=True if pin else False if unpin else None, keep_alive=seconds)
    m = store.get_model(model)
    if as_json:
        from mxconsole import serve
        running = serve.daemon_running()
        typer.echo(report.dump(report.model_row(m, serve.live_states() if running else {}, running)))
        return
    typer.echo(f"{m.name}: {report.lifecycle(m)}")
    typer.echo(f"console  {report.console_link('model', m.name)}")


@app.command("logs")
def logs_cmd(model: str = typer.Argument(..., help="A registered model name"),
             lines: int = typer.Option(50, "--lines", "-n", min=1, help="How many trailing lines."),
             follow: bool = typer.Option(False, "--follow", "-f", help="Keep printing new lines until Ctrl-C.")) -> None:
    """Tail a model's server log (mxconsole's own log file; works without the daemon)."""
    from mxconsole.store import mxc_home
    if Store.open().get_model(model) is None:
        _fail(f"no model named {model}. See: mxc ls")
    path = mxc_home() / "logs" / f"{model}.log"
    try:
        with open(path, "rb") as f:
            tail = f.read().decode(errors="replace").splitlines()[-lines:]
            for line in tail:
                typer.echo(line)
            while follow:
                chunk = f.readline()
                if chunk:
                    typer.echo(chunk.decode(errors="replace").rstrip("\n"))
                else:
                    time.sleep(0.5)
    except FileNotFoundError:
        _fail(f"no log yet for {model}. Start it with: mxc serve {model}")
    except KeyboardInterrupt:
        pass


@app.command("metrics")
def metrics_cmd(range_: str = typer.Option("5m", "--range", help="Window: " + ", ".join(report.RANGES) + "."),
                as_json: bool = JSON) -> None:
    """The console's numbers: requests, errors, latency, per model, and GPUs. Works without the daemon."""
    from mxconsole import metrics
    if range_ not in report.RANGES:
        _fail(f"--range {range_!r} is not one of {', '.join(report.RANGES)}")
    data = metrics.summary(Store.open(), window=report.RANGES[range_])
    gpus = metrics.gpus()
    if as_json:
        typer.echo(report.dump({"range": range_, **data, "gpus": gpus}))
        return
    typer.echo(report.metrics_text(data, range_, gpus))
    typer.echo(f"\nconsole  {report.console_link('home')}")


@app.command("requests")
def requests_cmd(limit: int = typer.Option(20, "--limit", min=1, help="How many recent requests."),
                 as_json: bool = JSON) -> None:
    """The activity log, newest first. Works without the daemon."""
    from mxconsole import metrics
    rows = metrics.recent(Store.open(), limit)
    if as_json:
        typer.echo(report.dump(rows))
        return
    if not rows:
        typer.echo("no requests yet")
    else:
        typer.echo(report.requests_text(rows))
    typer.echo(f"\nconsole  {report.console_link('gateway')}")


@app.command("bench")
def bench_cmd(models: list[str] = typer.Argument(None, help="Registered model names (see: mxc ls)."),
              all_: bool = typer.Option(False, "--all", help="Benchmark every registered model."),
              runs: int = typer.Option(5, "--runs", min=1, max=50, help="Timed runs per model, after one warm-up."),
              as_json: bool = JSON) -> None:
    """Race your models: the same streamed workload for each, through the daemon, fastest first."""
    from mxconsole import bench, serve
    if all_ and models:
        _fail("name models or use --all, not both")
    if not all_ and not models:
        _fail("name models or use --all (see: mxc ls)")
    store = Store.open()
    unknown = [m for m in models or [] if store.get_model(m) is None]
    if unknown:
        _fail(f"no model named {unknown[0]}. See: mxc ls")
    if all_ and not store.models():
        _fail("no models yet. Try: mxc serve qwen2.5-0.5b")
    try:
        if serve.ensure_daemon() and not as_json:
            typer.echo("started the omxconsole daemon")
        started = bench.start(None if all_ else list(dict.fromkeys(models)), runs)
        if not as_json:
            typer.echo(f"benchmarking {len(started['models'])} model{'s' if len(started['models']) != 1 else ''}: "
                       f"~{bench.estimate_tokens(bench.PROMPT)} tokens in, up to {bench.MAX_TOKENS} out, batch 1, "
                       f"{runs} runs after a warm-up")
        seen: dict[str, str] = {}

        def show(progress: dict) -> None:
            if as_json:
                return
            for m in progress["models"]:
                line = bench.progress_line(m, progress["runs"])
                if seen.get(m["name"]) != line:
                    seen[m["name"]] = line
                    typer.echo(line)

        run = bench.follow(started["id"], show)
        if not as_json:  # the last states, in case the run finished between two polls
            show({"runs": run["runs"], "models": [{"name": r["model"], "state": r["status"], "done": r["runs_ok"],
                                                    "error": r["error"], "decode_tps": r["decode_tps"]} for r in run["results"]]})
    except (serve.DaemonError, bench.BenchError) as e:
        _fail(str(e))
    if as_json:
        typer.echo(report.dump(run))
    else:
        typer.echo("\n" + bench.results_text(run))
        typer.echo(f"\nconsole  {report.console_link('benchmarks')}")
    if run["status"] == "failed":
        raise typer.Exit(1)


@app.command("status")
def status_cmd(as_json: bool = JSON) -> None:
    """One screen: daemon, endpoints, models, keys, MAX version. Works without the daemon."""
    from mxconsole import serve
    info = serve.daemon_info()
    data = report.status_data(Store.open(), info, serve.live_states() if info else {})
    typer.echo(report.dump(data) if as_json else report.status_text(data))


@app.command("ps")
def ps_cmd(as_json: bool = JSON) -> None:
    """The crew: daemon, Caddy, LiteLLM and each `max serve`, with pid, port, memory and uptime. Works without the daemon."""
    from mxconsole import metrics, serve, system
    crew = system.collect(health=system.daemon_answers)
    if metrics.gpus():  # models get `vram_gb` (in --json) when there is an NVIDIA GPU to ask
        system.add_vram(crew, metrics.gpu_processes())
    states = serve.live_states()
    for c in crew:  # the daemon knows starting/ready; pid files only know "running"
        if c["kind"] == "model" and c["name"] in states:
            c["state"] = states[c["name"]]
    typer.echo(report.dump(crew) if as_json else system.ps_text(crew))
    if not as_json:
        typer.echo(f"\nconsole  {report.console_link('daemon')}")


@app.command("console")
def console_cmd(page: str = typer.Argument("home", help="One of: " + ", ".join(report.PAGES)),
                model: str = typer.Argument(None, help="The model, for model, security, bench, logs and notebook."),
                no_open: bool = typer.Option(False, "--no-open", help="Only print the link.")) -> None:
    """Print the console link for a page and open it in the browser. Starts the daemon if needed.

    Pages are the console's blades (keys, gateway, settings, add, model, security, bench, logs, notebook); the old
    page names still work (overview, models, daemon, pull, ...). `mxc console api` opens the API reference."""
    import webbrowser

    from mxconsole import serve
    if page not in report.PAGES:
        _fail(f"no console page {page!r}. Pages: {', '.join(report.PAGES)}")
    try:
        serve.ensure_daemon()
    except serve.DaemonError as e:
        _fail(str(e))
    url = report.console_link(page, model)
    typer.echo(url)
    if not no_open:
        webbrowser.open(url)


@keys_app.command("new")
def keys_new(app_name: str = typer.Argument(..., metavar="APP"),
             rotate: bool = typer.Option(False, "--rotate", help="Revoke the app's current key and issue a new one.")) -> None:
    """Create a key for an app. It is printed once."""
    try:
        key = keys.create_key(Store.open(), app_name, rotate=rotate)
    except ValueError as e:
        _fail(str(e))
    except keys.KeyExists as e:
        _fail(str(e))
    typer.echo(key)
    typer.echo("This key will not be shown again. Copy it now.")


@keys_app.command("rotate")
def keys_rotate(app_name: str = typer.Argument(..., metavar="APP"), as_json: bool = JSON) -> None:
    """Issue a new key for an app and revoke the old one. The new key is printed once."""
    try:
        key, revived = keys.rotate_key(Store.open(), app_name)
    except keys.KeyNotFound as e:
        _fail(str(e))
    if as_json:
        typer.echo(report.dump({"app": app_name, "key": key, "revived": revived}))
        return
    typer.echo(key)
    typer.echo("This key will not be shown again. Copy it now. The old key no longer works.")
    if revived:
        typer.echo(f"note: the old key had been revoked, so {app_name} is active again with this new key.")


@keys_app.command("ls")
def keys_ls(as_json: bool = JSON) -> None:
    """List keys (never the secret)."""
    rows = [k for k in keys.list_keys(Store.open()) if not k.app.startswith("_")]  # hide mxconsole's own keys
    if as_json:
        typer.echo(report.dump(report.keys_data(rows)))
        return
    if not rows:
        typer.echo("no keys yet. Try: mxc keys new <app>")
        return

    when = report.when
    typer.echo(f"{'APP':<20} {'PREFIX':<13} {'CREATED':<17} {'LAST USED':<17} STATUS")
    for k in rows:
        status = "active" if k.revoked_at is None else "revoked"
        typer.echo(f"{k.app:<20} {k.prefix:<13} {when(k.created_at):<17} {when(k.last_used_at):<17} {status}")


@keys_app.command("revoke")
def keys_revoke(app_name: str = typer.Argument(..., metavar="APP")) -> None:
    """Disable an app's key."""
    if app_name.startswith("_"):
        _fail(f"{app_name} is managed by omxconsole (mxc expose --stop revokes it)")
    if not keys.revoke_key(Store.open(), app_name):
        _fail(f"no active key for {app_name}")
    typer.echo(f"revoked the key for {app_name}")


@app.command("pull")
def pull(ref: str = typer.Argument(..., help="Short name (qwen2.5-0.5b) or Hugging Face repo id"),
         dry_run: bool = typer.Option(False, "--dry-run", help="Only resolve; download nothing"),
         name: str = typer.Option(None, "--name", help="Register under this name"),
         port_dir: Path = typer.Option(None, "--port", help="Register a finished port (from `mxc port`) instead"),
         as_json: bool = typer.Option(False, "--json", help="One JSON object per progress event, for scripts and agents.")) -> None:
    """Resolve a model, download it at a pinned revision (with live progress) and register it.

    Runs in the daemon (started if needed), so the console's Pull page shows it too; Ctrl-C cancels (exit 130)."""
    if port_dir is not None:
        from mxconsole import port
        try:
            model = port.register(ref, port_dir, name=name)
        except resolve.PullError as e:
            _fail(e.resolution.message)
        except (port.PortError, resolve.ResolveError) as e:
            _fail(str(e))
        typer.echo(f"registered port {model.name} ({model.repo_id}@{model.revision[:8]}, custom architecture {model.custom_arch_path})")
        return
    try:
        if dry_run:
            res = resolve.resolve(ref, catalog=resolve.load_catalog(), hf=resolve.HubClient())
            if not res.supported:
                _fail(f"{res.kind}: {res.message}")
            typer.echo(f"{res.kind}: {res.message}")
            return
    except resolve.PullError as e:
        _fail(e.resolution.message)
    except resolve.ResolveError as e:
        _fail(str(e))
    from mxconsole import pull_cli
    code, text = pull_cli.run(ref, name, as_json)
    if text:
        typer.echo(text, err=code != 0)
    if code:
        raise typer.Exit(code)


@app.command("port")
def port_model(ref: str = typer.Argument(..., help="Short name or Hugging Face repo id MAX does not support yet"),
               auto: bool = typer.Option(False, "--auto", help="Run the port unattended with Claude Code (headless) in the daemon."),
               max_attempts: int = typer.Option(3, "--max-attempts", help="With --auto: tries before giving up (a failed build or parity check is fed back)."),
               unsandboxed: bool = typer.Option(False, "--unsandboxed", help="With --auto: run even without bubblewrap (no confinement; not recommended)."),
               as_json: bool = typer.Option(False, "--json", help="With --auto: one JSON object per change, for scripts and agents.")) -> None:
    """Set up a workspace to port an unsupported architecture to MAX with Claude Code (--auto does it for you)."""
    if auto:
        from mxconsole import port_cli
        code, text = port_cli.run(ref, None, as_json, max_attempts, unsandboxed=unsandboxed)
        if text:
            typer.echo(text, err=code != 0)
        if code:
            raise typer.Exit(code)
        return
    from mxconsole import port
    try:
        result = port.prepare(ref)
    except (port.PortError, resolve.ResolveError) as e:
        _fail(str(e))
    if result.workspace is None:
        typer.echo(result.message)
        return
    typer.echo(f"Modular skills: {', '.join(result.skills)}")
    typer.echo(f"Closest supported donor: {result.donor.architecture}")
    typer.echo(f"Workspace: {result.workspace} (config.json, PORT.md, .claude/skills)")
    typer.echo("Start the port with:")
    typer.echo(f"  {result.command}")
    typer.echo(f"When it works: mxc pull {ref} --port <port dir>")


@app.command("learn")
def learn_model(
    model: str = typer.Argument(..., help="A registered model name or a Hugging Face repo id."),
    no_open: bool = typer.Option(False, "--no-open", help="Write the notebook but don't launch Jupyter Lab."),
    as_json: bool = typer.Option(False, "--json", help="Print the notebook (path, cells, architecture) as JSON; never launches Jupyter."),
) -> None:
    """Build a notebook about a model from Hugging Face metadata and open it in Jupyter Lab."""
    from mxconsole import learn
    if as_json:
        try:
            typer.echo(report.dump(learn.notebook_payload(model)))
        except Exception as exc:
            _fail(f"could not build a notebook for {model}: {exc}")
        return
    try:
        path = learn.write_notebook(model)
    except Exception as exc:
        _fail(f"could not build a notebook for {model}: {exc}")
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
    from mxconsole import expose, serve
    try:  # before any download: a typo shouldn't cost gigabytes first
        seconds = parse_duration(keep_alive) if keep_alive else None
    except ValueError:
        _fail(f"--keep-alive {keep_alive!r} is not a duration (try 90s, 10m or 1h)")
    store = Store.open()
    found = store.get_model(model) or next((m for m in store.models() if m.repo_id == model), None)
    if found is None:
        try:
            found = resolve.pull(model)
        except resolve.PullError as e:
            _fail(e.resolution.message)
        except resolve.ResolveError as e:
            _fail(str(e))
        typer.echo(f"pulled {found.name} ({found.repo_id}@{found.revision[:8]})")
        try:
            if expose.refresh_if_running(store):
                typer.echo("restarted LiteLLM so the LAN endpoint lists it")
        except expose.ExposeError as e:
            typer.echo(f"warning: could not refresh the LAN endpoint ({e}). Run: mxc expose", err=True)
    name = found.name
    if pin or seconds is not None:
        store.set_lifecycle(name, pinned=pin, keep_alive=seconds)
    try:
        if serve.ensure_daemon():
            typer.echo("started the omxconsole daemon")
        if not no_wait:
            typer.echo(f"loading {name} (the first load compiles the model and can take a minute)…")
            started = time.monotonic()
            serve.load(name)
            typer.echo(f"{name} is ready ({time.monotonic() - started:.1f}s)")
    except serve.DaemonError as e:
        _fail(str(e))
    typer.echo("")
    typer.echo(serve.usage(name, serve.first_key(store)))
    typer.echo(f"\nmanage it in the console: {report.console_link('model', name)}   (or: mxc status, mxc set {name} --pin)")


@app.command("stop")
def stop_model(model: str = typer.Argument(None, help="Model to unload"),
               all_: bool = typer.Option(False, "--all", help="Unload every model and stop the daemon.")) -> None:
    """Unload a model now, or stop everything with --all."""
    from mxconsole import serve
    if all_:
        try:
            stopped = serve.shutdown()
        except serve.DaemonError as e:
            _fail(str(e))
        typer.echo("stopped the daemon and every model" if stopped else "the daemon isn't running")
        return
    if not model:
        _fail("name a model, or use --all")
    if not serve.daemon_running():
        typer.echo("the daemon isn't running; nothing is loaded")
        return
    try:
        serve.stop(model)
    except serve.DaemonError as e:
        _fail(str(e))
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
        _fail(str(e))
    typer.echo(f"endpoint   https://{host}:{expose.EXPOSE_PORT}/v1")
    typer.echo(f"configs    {configs['caddyfile']}  {configs['litellm']}")
    if litellm:
        typer.echo(f"litellm    http://127.0.0.1:{expose.LITELLM_PORT}/ui (this machine only)")
    typer.echo("keys       mxc keys new <app>   (send it as: Authorization: Bearer <key>)")
    typer.echo(f"trust      copy {expose.ca_root_path()} to each client (this machine too) and trust it once")
    typer.echo("           Linux: sudo trust anchor root.crt · or pass it as the CA bundle (curl --cacert root.crt)")


@app.command("tray")
def tray_cmd() -> None:
    """An omxconsole icon in the system tray: starts the daemon, shows what's running, opens the console."""
    import signal

    from mxconsole import app as desktop, serve, tray
    from mxconsole.store import mxc_home
    lock = tray.single_instance(mxc_home() / "tray.lock")
    if lock is None:
        _fail("the omxconsole tray is already running")
    controller = tray.Controller(fetch=tray.fetch, ensure_daemon=serve.ensure_daemon, shutdown=serve.shutdown,
                                 open_console=tray.open_console, get_autostart=lambda: False,
                                 set_autostart=tray.autostart_not_yet)
    icon = Path(desktop.__file__).with_name("console") / "favicon.svg"
    try:
        child = subprocess.Popen(tray.qt_command(desktop.project_dir(), icon), stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, text=True)
    except OSError as e:
        _fail(f"could not start the tray (is the pixi `app` environment installed? `mxc app --install`): {e}")

    def on_term(signum, frame):  # logout or `systemctl --user stop`: leave as Quit would
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, on_term)
    code = tray.run(controller, child)
    if code:
        _fail(f"the tray icon closed unexpectedly (exit {code}). Is a tray host running in your bar? "
              "Run `pixi run -e app python mxconsole/tray_qt.py mxconsole/console/favicon.svg` to see why.")


@app.command("app")
def app_cmd(install: bool = typer.Option(False, "--install", help="Linux: add an omxconsole launcher to the app menu."),
            uninstall: bool = typer.Option(False, "--uninstall", help="Linux: remove that launcher.")) -> None:
    """Open the console in its own window (starts the daemon if needed; closing the window stops nothing)."""
    from mxconsole import app as desktop, report, serve
    try:
        if install and uninstall:
            _fail("pass --install or --uninstall, not both")
        if install or uninstall:
            if install:
                desktop._require_linux(sys.platform)  # before the Qt env download, not after it
                desktop.prepare_env(desktop.project_dir())
            for path in (desktop.install(desktop.project_dir()) if install else desktop.uninstall()):
                typer.echo(("installed " if install else "removed ") + str(path))
            return
        how = desktop.run(serve.ensure_daemon, desktop.open_native, desktop.open_browser_app,
                          report.console_link(), say=typer.echo)
        typer.echo(f"window: {how}")
    except (desktop.AppError, serve.DaemonError) as e:
        _fail(str(e))


@app.command("daemon")
def daemon() -> None:
    """Run the gateway in the foreground (logs to stdout). Ctrl-C stops it and unloads every model."""
    import uvicorn

    from mxconsole.gateway import create_app
    from mxconsole.settings import DAEMON_HOST, DAEMON_PORT, env
    from mxconsole.supervisor import Supervisor

    import os
    import shlex

    store = Store.open()
    max_command = tuple(shlex.split(env("MAX_CMD", "max")))
    port = int(env("PORT", str(DAEMON_PORT)))
    uvicorn.run(create_app(store, Supervisor(store, max_command=max_command)), host=DAEMON_HOST, port=port,
                log_level="info")


def main() -> None:
    app()
