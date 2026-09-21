"""mxstudio CLI: download, serve, chat and benchmark models from the terminal."""

from __future__ import annotations

import time

from notebooks.mxstudio import downloads, hwinfo, notebooks
import typer

from notebooks.mxstudio import registry
from notebooks.mxstudio.bench import MANAGER as BENCH
from notebooks.mxstudio.downloads import MANAGER as DOWNLOADS
from notebooks.mxstudio.servers import MANAGER as SERVERS, MODEL_PORT

app = typer.Typer(
    name="mxstudio",
    help="A thin LM Studio-style layer on top of Modular MAX.",
    no_args_is_help=True,
)


def _resolve(query: str) -> registry.ModelEntry:
    entry = registry.resolve(query)
    if entry is None:
        typer.echo(f"No unique model match for '{query}'. Try 'mxs list'.")
        raise typer.Exit(1)
    return entry


def _fmt_bytes(n: int | None) -> str:
    if n is None:
        return "?"
    return f"{n / 1e9:.2f} GB"


@app.command("list")
def list_models() -> None:
    """List curated models with install/serve status."""
    server = SERVERS.status()
    for entry in registry.REGISTRY:
        installed = "installed" if downloads.is_installed(entry.id) else "not installed"
        flags = []
        if entry.experimental:
            flags.append("experimental")
        if server.get("model_id") == entry.id:
            flags.append(server["state"])
        suffix = f"  [{', '.join(flags)}]" if flags else ""
        typer.echo(f"{entry.id}")
        typer.echo(
            f"    {entry.name} · {entry.params} · ~{entry.approx_gb} GB · {installed}{suffix}"
        )


@app.command()
def download(model: str) -> None:
    """Download a model from Hugging Face (with progress)."""
    entry = _resolve(model)
    if downloads.is_installed(entry.id):
        typer.echo(f"{entry.id} is already installed.")
        return
    DOWNLOADS.total_size_bytes(entry.id)
    DOWNLOADS.start(entry.id)
    typer.echo(f"Downloading {entry.id} ...")
    while True:
        status = DOWNLOADS.status(entry.id)
        if status is None:
            break
        pct = f"{status['pct']}%" if status["pct"] is not None else "?"
        typer.echo(
            f"  {pct}  {_fmt_bytes(status['done_bytes'])} / {_fmt_bytes(status['total_bytes'])}",
        )
        if status["state"] == "done":
            typer.echo("Download complete.")
            return
        if status["state"] == "error":
            typer.echo(f"Download failed: {status['error']}")
            raise typer.Exit(1)
        time.sleep(3)


@app.command()
def serve(
    model: str,
    wait: bool = typer.Option(True, help="Poll until the server is healthy."),
) -> None:
    """Start `max serve` for a model (stops any currently running model)."""
    entry = _resolve(model)
    if not downloads.is_installed(entry.id):
        typer.echo(f"{entry.id} is not downloaded. Run: mxs download '{entry.id}'")
        raise typer.Exit(1)
    try:
        SERVERS.start(entry)
    except RuntimeError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from None
    typer.echo(f"Starting {entry.id} on http://localhost:{MODEL_PORT} ...")
    if not wait:
        return
    while True:
        status = SERVERS.status()
        if status["state"] == "running":
            typer.echo(f"Ready: http://localhost:{MODEL_PORT}/v1 (OpenAI-compatible)")
            return
        if status["state"] == "crashed":
            typer.echo("Server crashed. Last log lines:")
            typer.echo(status.get("log_tail", ""))
            raise typer.Exit(1)
        typer.echo(f"  {status['state']} ({status.get('uptime_s', 0)}s) ...")
        time.sleep(5)


@app.command()
def stop() -> None:
    """Stop the running model server."""
    try:
        SERVERS.stop()
    except RuntimeError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from None
    typer.echo("Stopped.")


@app.command()
def status() -> None:
    """Show model server status."""
    info = SERVERS.status()
    typer.echo(json_dumps(info))


@app.command()
def logs(lines: int = 50) -> None:
    """Tail the model server log."""
    typer.echo(SERVERS.logs(lines))


@app.command()
def chat(
    prompt: str = typer.Argument(None, help="One-shot prompt (omit for a REPL)."),
    temperature: float = typer.Option(0.7),
    top_p: float = typer.Option(0.95),
    max_tokens: int = typer.Option(256),
) -> None:
    """Chat with the running model (streaming)."""
    from openai import OpenAI

    info = SERVERS.status()
    if info["state"] != "running":
        typer.echo(f"No running model server (state: {info['state']}).")
        raise typer.Exit(1)
    client = OpenAI(base_url=f"http://127.0.0.1:{MODEL_PORT}/v1", api_key="EMPTY")
    messages: list[dict] = []

    def turn(text: str) -> None:
        try:
            with SERVERS.inference_reservation(info["model_id"]):
                messages.append({"role": "user", "content": text})
                with client.chat.completions.create(
                    model=info["model_id"],
                    messages=messages,
                    temperature=temperature,
                    top_p=top_p,
                    max_tokens=max_tokens,
                    stream=True,
                ) as stream:
                    reply = ""
                    for chunk in stream:
                        if chunk.choices and chunk.choices[0].delta.content:
                            piece = chunk.choices[0].delta.content
                            reply += piece
                            typer.echo(piece, nl=False)
                typer.echo("")
                messages.append({"role": "assistant", "content": reply})
        except RuntimeError as exc:
            typer.echo(str(exc))
            raise typer.Exit(1) from None

    try:
        if prompt:
            turn(prompt)
            return
        typer.echo(f"Chatting with {info['model_id']} (Ctrl-C or 'exit' to quit)")
        while True:
            try:
                text = typer.prompt("you")
            except typer.Abort:
                return
            if text.strip().lower() in {"exit", "quit"}:
                return
            turn(text)
    finally:
        client.close()


@app.command()
def info(model: str, card: bool = typer.Option(False, help="Print full model card.")) -> None:
    """Show model description, innovation summary and links."""
    entry = _resolve(model)
    typer.echo(f"{entry.name}  ({entry.id})")
    typer.echo(f"  {entry.params} · ~{entry.approx_gb} GB · {entry.license}")
    typer.echo(f"\n{entry.description}\n")
    typer.echo(f"Innovation: {entry.innovation}\n")
    for label, url in entry.links.items():
        typer.echo(f"  {label}: {url}")
    if card:
        from notebooks.mxstudio.modelinfo import get_model_card

        text = get_model_card(entry.id)
        typer.echo("\n" + (text or "(model card not available)"))


@app.command()
def notebook(model: str) -> None:
    """Generate a Jupyter notebook for a model."""
    entry = _resolve(model)
    path = notebooks.write_notebook(entry)
    typer.echo(f"Notebook written: {path}")
    typer.echo("Open it in Cursor/VS Code, or run: pixi run jupyter lab")


@app.command()
def bench(
    runs: int = typer.Option(3),
    max_tokens: int = typer.Option(128),
) -> None:
    """Benchmark the running model server (TTFT + tokens/sec)."""
    info = SERVERS.status()
    if info["state"] != "running":
        typer.echo(f"No running model server (state: {info['state']}).")
        raise typer.Exit(1)
    BENCH.start(info["model_id"], runs=runs, max_tokens=max_tokens)
    typer.echo(f"Benchmarking {info['model_id']} ({runs} runs + warmup) ...")
    while True:
        job = BENCH.status()
        if job["state"] == "done":
            for i, r in enumerate(job["runs"], 1):
                typer.echo(
                    f"  run {i}: TTFT {r['ttft_s']}s · {r['tokens']} tokens · {r['tok_per_s']} tok/s"
                )
            s = job["summary"]
            typer.echo(
                f"Mean: TTFT {s['mean_ttft_s']}s · {s['mean_tok_per_s']} tok/s"
            )
            return
        if job["state"] == "error":
            typer.echo(f"Benchmark failed: {job['error']}")
            raise typer.Exit(1)
        typer.echo(f"  {len(job.get('runs', []))}/{runs} runs done ...")
        time.sleep(4)


@app.command()
def hw() -> None:
    """Show hardware summary."""
    typer.echo(json_dumps(hwinfo.summary()))


@app.command()
def ui(
    port: int = typer.Option(8080, help="Port for the mxstudio web UI."),
    host: str = typer.Option("0.0.0.0"),
) -> None:
    """Launch the mxstudio web UI."""
    import uvicorn

    typer.echo(f"mxstudio UI: http://localhost:{port}")
    uvicorn.run("mxstudio.api:app", host=host, port=port, log_level="warning")


def json_dumps(obj) -> str:
    import json

    return json.dumps(obj, indent=2)


# Nucleus is an opt-in deployment profile; existing commands remain unchanged.
nucleus_app = typer.Typer(help="Authenticated OpenAI-compatible Nucleus deployment profile.")
app.add_typer(nucleus_app, name="nucleus")


@nucleus_app.command("status")
def nucleus_status() -> None:
    """Show Nucleus readiness, transition and request lease state."""
    from notebooks.mxstudio.nucleus import MANAGER
    typer.echo(json_dumps(MANAGER.status()))


@nucleus_app.command("prepare")
@nucleus_app.command("start")
def nucleus_start(model: str = typer.Option(None, help="Downloaded model id (defaults to NUCLEUS_MODEL_ID)."), timeout: float = typer.Option(600.0, min=0.1, max=600.0)) -> None:
    """Prepare the configured model without installing services or changing host config."""
    import asyncio
    from notebooks.mxstudio import nucleus
    try:
        result = asyncio.run(nucleus.MANAGER.prepare(model, timeout=timeout))
    except (ValueError, RuntimeError, TimeoutError) as exc:
        typer.echo(f"Nucleus preparation failed: {exc}")
        raise typer.Exit(1)
    typer.echo(json_dumps(result))


@nucleus_app.command("run")
def nucleus_run(
    host: str = typer.Option("127.0.0.1", help="Private loopback interface; expose only through Caddy."),
    port: int = typer.Option(8080, min=1, max=65535),
    workers: int = typer.Option(1, min=1, max=1),
    prewarm: bool = typer.Option(True, help="Queue preparation of NUCLEUS_MODEL_ID on startup."),
) -> None:
    """Run the authenticated inference profile with private Studio administration."""
    import os
    import uvicorn

    if host != "127.0.0.1":
        raise typer.BadParameter("Nucleus must bind 127.0.0.1; use Caddy for LAN access")
    if not os.environ.get("MXSTUDIO_NUCLEUS_API_KEY", "").strip():
        raise typer.BadParameter("MXSTUDIO_NUCLEUS_API_KEY is required")
    if not os.environ.get("NUCLEUS_MODEL_ID", "").strip():
        raise typer.BadParameter("NUCLEUS_MODEL_ID must name a downloaded model")
    os.environ["NUCLEUS_PREWARM"] = "1" if prewarm else "0"
    uvicorn.run("mxstudio.api:app", host=host, port=port, workers=workers, log_level="info")


@nucleus_app.command("lesson")
def nucleus_lesson(output: str = typer.Option("nucleus-ttft.ipynb")) -> None:
    """Generate an editable first-request versus warm-token timing notebook."""
    from notebooks.mxstudio.nucleus_lesson import write_lesson
    try:
        destination = write_lesson(output)
    except OSError as exc:
        typer.echo(f"Cannot write lesson: {exc}")
        raise typer.Exit(1) from None
    typer.echo(str(destination))


@nucleus_app.command("render-config")
def nucleus_render_config(output: str = typer.Option(None, help="Write deployment config manifest to this path.")) -> None:
    """Render safe deployment paths and required environment variables."""
    from pathlib import Path
    payload = {"profile": "nucleus", "model_env": "NUCLEUS_MODEL_ID", "api_key_env": "MXSTUDIO_NUCLEUS_API_KEY", "studio_bind": "127.0.0.1:8080", "max_bind": "127.0.0.1:8100", "public_ingress": "Caddy only; GET /v1/models and POST /v1/chat/completions"}
    text = json_dumps(payload) + "\n"
    if output:
        try:
            with Path(output).open("x", encoding="utf-8") as destination:
                destination.write(text)
        except OSError as exc:
            typer.echo(f"Cannot write config: {exc}")
            raise typer.Exit(1) from None
    else:
        typer.echo(text, nl=False)


if __name__ == "__main__":
    app()
