"""mxstudio CLI: download, serve, chat and benchmark models from the terminal."""

from __future__ import annotations

import time
import json

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
    return json.dumps(obj, indent=2)


# Endpoint-first deployment operations are additive; legacy commands above stay
# available for existing local workflows.
def _deployment_payload(run: dict) -> dict:
    payload = dict(run)
    if payload.get("state") == "needs_setup":
        payload["setup_approval_required"] = True
        payload["resume_command"] = f"pixi run mxstudio runs resume {payload.get('run_id', '')}".rstrip()
    return payload


def _deployment_result(run: dict, *, json_output: bool) -> bool:
    """Print a run and return whether it completed in a blocked/error state."""
    payload = _deployment_payload(run)
    state = str(payload.get("state", "unknown"))
    if json_output:
        typer.echo(json_dumps(payload))
    else:
        typer.echo(f"State: {state}")
        for key in ("stage", "model", "job_id"):
            if payload.get(key) is not None:
                typer.echo(f"{key.replace('_', ' ').title()}: {payload[key]}")
        if state == "needs_setup":
            typer.echo("First-time service installation requires explicit approval.")
            recovery = payload.get("recovery")
            if recovery:
                typer.echo("Recovery plan:")
                typer.echo(json_dumps(recovery) if not isinstance(recovery, str) else recovery)
            typer.echo(f"Resume with: {payload['resume_command']}")
        elif payload.get("recovery"):
            typer.echo("Recovery plan:")
            recovery = payload["recovery"]
            typer.echo(json_dumps(recovery) if not isinstance(recovery, str) else recovery)
        if payload.get("error"):
            typer.echo(f"Error: {payload['error']}")
    return state in {
        "blocked", "busy", "failed", "error", "cancelled", "interrupted",
        "created", "needs_input", "needs_setup", "awaiting_client_verification",
    }


def _wait_for_run(run: dict, *, wait: bool, json_output: bool) -> dict:
    current = run
    if wait:
        from notebooks.mxstudio import deployments

        run_id = current.get("run_id")
        while current.get("state") in deployments.ACTIVE_STATES:
            time.sleep(1)
            latest = deployments.get_run(run_id)
            if latest is None:
                current = {
                    **current,
                    "state": "failed",
                    "error": "deployment run disappeared before completion",
                }
                break
            current = latest
            if current.get("state") in deployments.ACTIVE_STATES and not json_output:
                typer.echo(f"  {current.get('stage') or current.get('state')} ...")
    return current


def _create_deployment(
    query: str,
    *,
    mode: str,
    revision: str | None,
    hostname: str,
    ca_file: str | None,
    wait: bool,
    json_output: bool,
) -> None:
    from notebooks.mxstudio import deployments

    try:
        created = deployments.create_run(
            query,
            revision=revision,
            mode=mode,
            hostname=hostname,
            ca_file=ca_file,
        )
        run_id = created["run_id"]
    except (KeyError, ValueError, RuntimeError) as exc:
        typer.echo(f"Could not create deployment run: {exc}", err=True)
        raise typer.Exit(1) from None

    if not json_output:
        typer.echo(f"Run ID: {run_id}")
    try:
        enqueued = deployments.enqueue(run_id)
    except (ValueError, RuntimeError) as exc:
        typer.echo(f"Could not enqueue deployment run: {exc}", err=True)
        raise typer.Exit(1) from None

    # In JSON mode stdout remains machine-readable; the early identifier is
    # emitted on stderr so callers can retain it while a long run is polling.
    if json_output:
        typer.echo(json.dumps({"run_id": run_id}), err=True)
    run = enqueued if isinstance(enqueued, dict) else created
    run.setdefault("run_id", run_id)
    run = _wait_for_run(run, wait=wait, json_output=json_output)
    if _deployment_result(run, json_output=json_output):
        raise typer.Exit(1)


@app.command("pull")
def pull_model(
    model: str,
    revision: str = typer.Option(None, help="Hugging Face revision to fetch."),
    no_wait: bool = typer.Option(False, "--no-wait", help="Return after scheduling the run."),
    json_output: bool = typer.Option(False, "--json", help="Print the result as JSON."),
) -> None:
    """Resolve and download a model through the durable deployment workflow."""
    _create_deployment(
        model, mode="pull", revision=revision, hostname="api.mxstudio.local",
        ca_file=None, wait=not no_wait, json_output=json_output,
    )


@app.command("up")
def bring_up_model(
    model: str,
    local_only: bool = typer.Option(False, "--local-only", help="Prepare and verify local inference without LAN publication."),
    revision: str = typer.Option(None, help="Hugging Face revision to use."),
    hostname: str = typer.Option("api.mxstudio.local", help="LAN HTTPS hostname."),
    ca_file: str = typer.Option(None, "--ca-file", help="Optional CA bundle used for HTTPS verification."),
    no_wait: bool = typer.Option(False, "--no-wait", help="Return after scheduling the run."),
    json_output: bool = typer.Option(False, "--json", help="Print the result as JSON."),
) -> None:
    """Prepare a model for local use or an authenticated LAN endpoint."""
    _create_deployment(
        model, mode="local" if local_only else "lan", revision=revision,
        hostname=hostname, ca_file=ca_file, wait=not no_wait,
        json_output=json_output,
    )


runs_app = typer.Typer(help="Inspect and resume deployment runs.")
app.add_typer(runs_app, name="runs")


@runs_app.command("list")
def list_runs(json_output: bool = typer.Option(False, "--json", help="Print the result as JSON.")) -> None:
    from notebooks.mxstudio import deployments

    runs = [_deployment_payload(run) for run in deployments.list_runs()]
    if json_output:
        typer.echo(json_dumps(runs))
    else:
        for run in runs:
            typer.echo(f"{run.get('run_id')}  {run.get('state')}  {run.get('model')}  {run.get('stage') or ''}")


@runs_app.command("inspect")
def inspect_run(run_id: str, json_output: bool = typer.Option(False, "--json", help="Print the result as JSON.")) -> None:
    from notebooks.mxstudio import deployments

    run = deployments.get_run(run_id)
    if run is None:
        typer.echo(f"Deployment run not found: {run_id}", err=True)
        raise typer.Exit(1)
    if _deployment_result(run, json_output=json_output):
        raise typer.Exit(1)


@runs_app.command("resume")
def resume_run(
    run_id: str,
    no_wait: bool = typer.Option(False, "--no-wait", help="Return after scheduling the run."),
    json_output: bool = typer.Option(False, "--json", help="Print the result as JSON."),
) -> None:
    from notebooks.mxstudio import deployments

    try:
        run = deployments.resume(run_id)
    except (ValueError, RuntimeError) as exc:
        typer.echo(f"Could not resume deployment run: {exc}", err=True)
        raise typer.Exit(1) from None
    if not json_output:
        typer.echo(f"Run ID: {run_id}")
    run = _wait_for_run(run, wait=not no_wait, json_output=json_output)
    if _deployment_result(run, json_output=json_output):
        raise typer.Exit(1)


@app.command("catalog")
def model_catalog(json_output: bool = typer.Option(False, "--json", help="Print the result as JSON.")) -> None:
    """Show the normalized MAX model catalog and its provenance."""
    from notebooks.mxstudio import inventory

    catalog = inventory.catalog()
    if json_output:
        typer.echo(json_dumps(catalog))
    else:
        typer.echo(json_dumps(catalog))


endpoints_app = typer.Typer(help="Inspect and manage recorded inference endpoints.")
app.add_typer(endpoints_app, name="endpoint")


@endpoints_app.command("list")
def list_endpoints(json_output: bool = typer.Option(False, "--json", help="Print the result as JSON.")) -> None:
    from notebooks.mxstudio import deployments

    endpoints = deployments.list_endpoints()
    typer.echo(json_dumps(endpoints) if json_output else json_dumps(endpoints))


@endpoints_app.command("verify")
def verify_endpoint(
    run_id: str,
    ca_file: str = typer.Option(None, "--ca-file", help="Optional CA bundle used for HTTPS verification."),
    json_output: bool = typer.Option(False, "--json", help="Print the result as JSON."),
) -> None:
    from notebooks.mxstudio import deployments

    try:
        result = deployments.verify_endpoint(run_id, ca_file=ca_file)
    except (ValueError, RuntimeError) as exc:
        typer.echo(f"Endpoint verification failed: {exc}", err=True)
        raise typer.Exit(1) from None
    typer.echo(json_dumps(result) if json_output else json_dumps(result))


@endpoints_app.command("stop")
def stop_endpoint(
    run_id: str,
    json_output: bool = typer.Option(False, "--json", help="Print the result as JSON."),
) -> None:
    from notebooks.mxstudio import deployments

    try:
        result = deployments.stop_endpoint(run_id)
    except (ValueError, RuntimeError) as exc:
        typer.echo(f"Endpoint stop failed: {exc}", err=True)
        raise typer.Exit(1) from None
    typer.echo(json_dumps(result) if json_output else json_dumps(result))


def _redact_proof(value):
    secret_names = {"api_key", "key", "token", "authorization", "secret", "password"}
    if isinstance(value, dict):
        return {
            key: _redact_proof(item)
            for key, item in value.items()
            if str(key).casefold() not in secret_names
        }
    if isinstance(value, list):
        return [_redact_proof(item) for item in value]
    return value


def _read_client_evidence(path: str) -> dict:
    from pathlib import Path

    evidence_limit = 64 * 1024
    try:
        with Path(path).open("rb") as source:
            raw = source.read(evidence_limit + 1)
    except OSError:
        typer.echo("Cannot read client evidence file.", err=True)
        raise typer.Exit(1) from None
    if len(raw) > evidence_limit:
        typer.echo("Client evidence file exceeds the 64 KiB limit.", err=True)
        raise typer.Exit(1)
    try:
        evidence = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        typer.echo("Client evidence must be valid UTF-8 JSON.", err=True)
        raise typer.Exit(1) from None
    if (
        not isinstance(evidence, dict)
        or evidence.get("vantage") != "client"
        or not isinstance(evidence.get("machine_fingerprint"), str)
        or not evidence["machine_fingerprint"].strip()
        or evidence.get("stream_complete") is not True
        or not isinstance(evidence.get("content_bytes"), int)
        or isinstance(evidence.get("content_bytes"), bool)
        or evidence["content_bytes"] <= 0
    ):
        typer.echo("File does not contain a complete client probe proof.", err=True)
        raise typer.Exit(1)
    return evidence


@endpoints_app.command("probe")
def probe_endpoint(
    url: str = typer.Option(..., "--url", help="HTTPS OpenAI base URL ending in /v1."),
    model: str = typer.Option(..., "--model", help="Advertised model ID to verify."),
    ca_file: str = typer.Option(None, "--ca-file", help="Optional trusted CA bundle."),
) -> None:
    """Run an authenticated streamed HTTPS proof from this client machine."""
    import os
    import httpx
    from notebooks.mxstudio import deployments

    key = os.environ.get("MXSTUDIO_NUCLEUS_API_KEY", "").strip()
    if not key:
        typer.echo("Set MXSTUDIO_NUCLEUS_API_KEY in this client environment.", err=True)
        raise typer.Exit(1)
    try:
        proof = deployments.probe(
            url, model, key, ca_file=ca_file, require_https=True
        )
    except (ValueError, RuntimeError, TimeoutError, OSError, httpx.HTTPError) as exc:
        typer.echo(
            f"Endpoint probe failed ({type(exc).__name__}); check HTTPS trust, credentials, URL, and endpoint availability.",
            err=True,
        )
        raise typer.Exit(1) from None
    if (
        not isinstance(proof, dict)
        or not isinstance(proof.get("machine_fingerprint"), str)
        or not proof["machine_fingerprint"].strip()
    ):
        typer.echo("Probe did not return bound client evidence.", err=True)
        raise typer.Exit(1)
    # The domain probe is also used server-side; this adapter records the
    # distinct vantage from which this CLI invocation ran.
    proof = {**proof, "vantage": "client"}
    typer.echo(json_dumps(_redact_proof(proof)))


@endpoints_app.command("accept-client")
def accept_client_evidence(
    run_id: str,
    evidence: str = typer.Option(..., "--evidence", help="JSON proof file produced by endpoint probe."),
) -> None:
    """Import a second-client proof into the authenticated local controller."""
    import os
    import httpx

    proof = _read_client_evidence(evidence)
    key = os.environ.get("MXSTUDIO_NUCLEUS_API_KEY", "").strip()
    if not key:
        typer.echo("Set MXSTUDIO_NUCLEUS_API_KEY in this operator environment.", err=True)
        raise typer.Exit(1)
    try:
        with httpx.Client(
            base_url="http://127.0.0.1:8080",
            timeout=10,
            trust_env=False,
        ) as client:
            response = client.post(
                f"/nucleus/endpoints/{run_id}/client-evidence",
                headers={"Authorization": f"Bearer {key}"},
                json=proof,
            )
    except httpx.HTTPError:
        typer.echo("Could not reach the local authenticated Nucleus API.", err=True)
        raise typer.Exit(1) from None
    if response.status_code >= 400:
        typer.echo(
            f"Client evidence was rejected by the local API (HTTP {response.status_code}).",
            err=True,
        )
        raise typer.Exit(1)
    try:
        result = response.json()
    except ValueError:
        typer.echo("Local API returned an invalid evidence response.", err=True)
        raise typer.Exit(1) from None
    typer.echo(json_dumps(_redact_proof(result)))


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
