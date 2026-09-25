"""mxstudio backend: REST API + static web UI."""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Literal

import anyio
import httpx
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from notebooks.mxstudio import agents, architectures, compat, downloads, fhsaas, hwinfo, jobs, modelinfo, notebooks, registry, settings as studio_settings
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from notebooks.mxstudio import (
    setup,
)
from notebooks.mxstudio.bench import MANAGER as BENCH
from notebooks.mxstudio.downloads import MANAGER as DOWNLOADS
from notebooks.mxstudio.servers import MANAGER as SERVERS, MODEL_PORT
from notebooks.mxstudio import nucleus, discovery

WEB_DIR = Path(__file__).parent / "web"

@asynccontextmanager
async def _lifespan(_app):
    advertiser = None
    host = os.environ.get("NUCLEUS_DISCOVERY_HOST", "").strip()
    if host:
        advertiser = discovery.NucleusDiscovery(
            host, int(os.environ.get("NUCLEUS_DISCOVERY_PORT", "443")),
            address=os.environ.get("NUCLEUS_DISCOVERY_ADDRESS") or None,
        )
        await asyncio.to_thread(advertiser.start)
    try:
        if os.environ.get("NUCLEUS_PREWARM") == "1":
            model_id = nucleus.MANAGER.resolve()
            job = await asyncio.to_thread(
                jobs.enqueue_process, "nucleus_prepare", f"Prepare {model_id}",
                {"model_id": model_id, "timeout_s": 600.0},
            )
            _app.state.nucleus_prewarm_job_id = job["job_id"]
        yield
    finally:
        if advertiser:
            await asyncio.to_thread(advertiser.stop)


app = FastAPI(title="mxstudio", version="0.1.1", lifespan=_lifespan)

# fh-saas base: logging config + the additive /api/jobs job-state bridge.
fhsaas.setup()
fhsaas.register_jobs_api(app)


def _entry_or_404(model_id: str) -> registry.ModelEntry:
    entry = registry.get(model_id)
    if entry is None:
        raise HTTPException(404, f"unknown model: {model_id}")
    return entry


def _queued(job: dict) -> dict:
    return {"job_id": job["job_id"], "job": job, "queued": True}


@app.get("/api/env")
def env_info() -> dict:
    """HF token / cache / disk readiness for downloads."""
    return compat.env_status()


@app.put("/api/setup")
def confirm_setup() -> dict:
    """Acknowledge only the project environment running this backend."""
    environment = compat.env_status()
    if not environment["project_environment"]:
        raise HTTPException(
            409,
            {
                "message": "Restart mxstudio in its isolated Pixi environment.",
                "commands": ["pixi install", "pixi run studio"],
            },
        )
    setup.acknowledge(
        environment["environment_kind"],
        environment["environment_path"],
    )
    return {**environment, "setup_confirmed": True}


@app.get("/api/settings")
def get_settings() -> dict:
    return studio_settings.public_settings()


class SettingsUpdate(BaseModel):
    hf_token: str | None = None
    clear_hf_token: bool = False


@app.put("/api/settings")
def update_settings(req: SettingsUpdate) -> dict:
    """Save HF token to ~/.mxstudio/secrets.json (never echoed back raw)."""
    if req.clear_hf_token:
        return studio_settings.clear_hf_token()
    if req.hf_token is None:
        raise HTTPException(400, "provide hf_token or clear_hf_token=true")
    return studio_settings.set_hf_token(req.hf_token)


@app.get("/api/models")
def list_models() -> list[dict]:
    server = SERVERS.status()
    result = []
    seen: set[str] = set()
    for entry in registry.REGISTRY:
        seen.add(entry.id)
        result.append(
            {
                **entry.to_dict(),
                "installed": downloads.is_installed(entry.id),
                "local_path": str(downloads.repo_dir(entry.id))
                if downloads.is_installed(entry.id)
                else None,
                "download": DOWNLOADS.status(entry.id),
                "server_state": (
                    server["state"]
                    if server.get("model_id") == entry.id
                    else None
                ),
            }
        )
    # Surface custom HF downloads from disk, including downloads from older sessions.
    custom_ids = set(downloads.installed_model_ids())
    custom_ids.update(DOWNLOADS._jobs)  # noqa: SLF001
    for model_id in sorted(custom_ids, key=str.casefold):
        if model_id in seen:
            continue
        metadata = downloads.local_model_metadata(model_id)
        result.append(
            {
                "id": model_id,
                "name": model_id.split("/")[-1],
                "params": "local",
                "approx_gb": metadata["approx_gb"],
                "context": metadata["context"],
                "license": "?",
                "description": "Locally discovered Hugging Face model",
                "innovation": "",
                "links": {"Hugging Face": f"https://huggingface.co/{model_id}"},
                "experimental": True,
                "custom_arch": None,
                "cuda_devices": "0",
                "family": metadata["family"],
                "architecture": metadata["architecture"],
                "when_to_use": "",
                "key_ideas": [],
                "learning_goals": [],
                "installed": downloads.is_installed(model_id),
                "local_path": str(downloads.repo_dir(model_id))
                if downloads.is_installed(model_id)
                else None,
                "download": DOWNLOADS.status(model_id),
                "server_state": (
                    server["state"] if server.get("model_id") == model_id else None
                ),
            }
        )
    return result


class HfCheckRequest(BaseModel):
    model_id: str
    sync: bool = False  # debug only — runs HF check on the request thread


class HfDownloadRequest(BaseModel):
    model_id: str
    force: bool = False  # allow download even if modular_compatible / VRAM risk


class CuratedDownloadRequest(BaseModel):
    force: bool = False


@app.post("/api/models/check")
def check_model(req: HfCheckRequest) -> dict:
    """Enqueue HF check (or sync path when sync=true for debug)."""
    mid = req.model_id.strip()
    if req.sync:
        return compat.check_hf_model(mid)
    job = jobs.start_kind("hf_check", model_id=mid)
    return _queued(job)


@app.post("/api/models/download-hf")
def download_hf_model(req: HfDownloadRequest) -> dict:
    """Enqueue download + gate check in a background job (no HF on request thread)."""
    mid = req.model_id.strip()
    if not mid:
        raise HTTPException(400, "model_id required")
    job = jobs.start_kind("download", model_id=mid, force=bool(req.force), curated=False)
    return _queued(job)


@app.post("/api/models/{model_id:path}/download")
def download_model(
    model_id: str, req: CuratedDownloadRequest = CuratedDownloadRequest()
) -> dict:
    entry = _entry_or_404(model_id)
    job = jobs.start_kind(
        "download",
        model_id=entry.id,
        force=bool(req.force),
        curated=True,
    )
    return _queued(job)


@app.delete("/api/models/{model_id:path}")
def uninstall_model(model_id: str) -> dict:
    """Remove a downloaded model from the local HF cache to free space."""
    server = SERVERS.status()
    if server.get("state") in ("running", "starting") and server.get("model_id") == model_id:
        raise HTTPException(409, "stop the server before uninstalling this model")
    job = DOWNLOADS.status(model_id)
    if job and job.get("state") == "downloading":
        raise HTTPException(409, "download still in progress")
    for queued in jobs.list_public(processing_only=True):
        if queued.get("kind") == "download" and model_id in str(queued.get("label") or ""):
            raise HTTPException(409, "download worker still in progress")
    result = downloads.uninstall(model_id)
    if not result.get("ok"):
        raise HTTPException(409, result.get("error") or "could not remove model files")
    DOWNLOADS._jobs.pop(model_id, None)  # noqa: SLF001
    DOWNLOADS._total_cache.pop(model_id, None)  # noqa: SLF001
    return result


@app.get("/api/models/{model_id:path}/local")
def model_local(model_id: str) -> dict:
    """Where weights live on disk."""
    return downloads.local_info(model_id)


@app.post("/api/models/{model_id:path}/open-folder")
def model_open_folder(model_id: str) -> dict:
    """Open the Hugging Face cache folder for this model in the OS file manager."""
    return downloads.open_containing_folder(model_id)

@app.get("/api/models/{model_id:path}/card")
def model_card(model_id: str) -> dict:
    entry = _entry_or_404(model_id)
    return {"markdown": modelinfo.get_model_card(entry.id)}


@app.get("/api/models/{model_id:path}/slides")
def model_slides(model_id: str) -> dict:
    """Return a cached Reveal deck, rebuilding a missing/corrupt sidecar."""
    from notebooks.mxstudio import slides as slide_mod

    try:
        notebooks.resolve_model_entry(model_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    notebook_path = notebooks.find_notebook(model_id)
    if notebook_path is None:
        return {
            **slide_mod.build_slides(model_id),
            "cached": False,
            "artifact_path": None,
        }
    return slide_mod.load_slides(notebook_path, model_id)


@app.post("/api/models/{model_id:path}/notebook")
def make_notebook(model_id: str, background: bool = False) -> dict:
    """Generate notebook + slides; background mode uses the persistent SQL queue."""
    try:
        entry = notebooks.resolve_model_entry(model_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    if background:
        return _queued(jobs.start_kind("notebook_generate", model_id=entry.id))
    # Compatibility for the existing client, which immediately opens ``name``.
    return notebooks.generate_notebook(entry.id)


@app.get("/api/notebooks")
def list_notebooks() -> list[dict]:
    return notebooks.list_notebooks()


@app.get("/api/notebooks/job")
def notebook_job() -> dict:
    """Must be registered before /api/notebooks/{name}."""
    return notebooks.NOTEBOOK_JOBS.status()


@app.post("/api/notebooks/{name}/open-folder")
def notebook_open_folder(name: str) -> dict:
    try:
        return notebooks.open_notebook_folder(name)
    except FileNotFoundError:
        raise HTTPException(404, f"notebook not found: {name}")
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/notebooks/{name}")
def get_notebook(name: str) -> dict:
    try:
        return notebooks.load_notebook(name)
    except FileNotFoundError:
        raise HTTPException(404, f"notebook not found: {name}")
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/notebooks/{name}/slides")
def get_notebook_slides(name: str) -> dict:
    """Retrieve a notebook sidecar and rebuild it from model metadata if absent."""
    from notebooks.mxstudio import slides as slide_mod

    try:
        notebook = notebooks.load_notebook(name)
        model_id = (notebook.get("metadata", {}).get("mxstudio") or {}).get(
            "model_id"
        )
        if not model_id:
            raise HTTPException(
                409, "notebook metadata.mxstudio.model_id is required for slides"
            )
        path = Path(notebook["path"])
        return slide_mod.load_slides(path, model_id)
    except FileNotFoundError:
        raise HTTPException(404, f"notebook not found: {name}")
    except ValueError as exc:
        raise HTTPException(400, str(exc))


class NotebookCell(BaseModel):
    index: int
    cell_type: str
    source: str


class RunCellRequest(BaseModel):
    cell_index: int | None = None  # None = run all code cells
    reset: bool = False
    code: str | None = None  # edited source for a single cell
    cells: list[NotebookCell] | None = None  # edited sources for run-all


class SaveNotebookRequest(BaseModel):
    cells: list[NotebookCell]


@app.post("/api/notebooks/{name}/run")
def run_notebook(name: str, req: RunCellRequest) -> dict:
    """Start a notebook run in the background (poll GET /api/notebooks/job)."""
    try:
        notebooks.load_notebook(name)  # validate
    except FileNotFoundError:
        raise HTTPException(404, f"notebook not found: {name}")
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    if notebooks.NOTEBOOK_JOBS.job.get("state") == "running":
        raise HTTPException(409, "a notebook job is already running")

    cell_dicts = (
        [c.model_dump() for c in req.cells] if req.cells is not None else None
    )
    if req.reset and req.cell_index is not None:
        notebooks.reset_kernel(name)
    return notebooks.NOTEBOOK_JOBS.start(
        name,
        cell_index=req.cell_index,
        code=req.code,
        cells=cell_dicts,
    )


@app.put("/api/notebooks/{name}")
def save_notebook(name: str, req: SaveNotebookRequest) -> dict:
    try:
        path = notebooks.save_notebook(
            name, [c.model_dump() for c in req.cells]
        )
        return {"ok": True, "path": str(path)}
    except FileNotFoundError:
        raise HTTPException(404, f"notebook not found: {name}")
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/notebooks/{name}/reset")
def reset_notebook(name: str) -> dict:
    try:
        notebooks.load_notebook(name)  # validate name
        notebooks.reset_kernel(name)
        return {"ok": True}
    except FileNotFoundError:
        raise HTTPException(404, f"notebook not found: {name}")
    except ValueError as exc:
        raise HTTPException(400, str(exc))


class ServeRequest(BaseModel):
    model_id: str


def _nucleus_auth(request: Request) -> None:
    if not nucleus.authorized(request.headers.get("authorization"), request.headers.get("x-api-key")):
        raise HTTPException(401, "invalid or missing Nucleus API key", headers={"WWW-Authenticate": "Bearer"})


class NucleusRunRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=256)
    revision: str | None = Field(None, min_length=1, max_length=200)
    mode: Literal["pull", "local", "lan"] = "lan"
    hostname: str = Field("api.mxstudio.local", min_length=1, max_length=253)
    ca_file: str | None = Field(None, min_length=1, max_length=4096)


class NucleusEndpointVerifyRequest(BaseModel):
    ca_file: str | None = Field(None, min_length=1, max_length=4096)


def _deployment_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", value):
        raise HTTPException(400, "invalid deployment identifier")
    return value


def _validate_nucleus_run(req: NucleusRunRequest) -> None:
    if not req.query.strip():
        raise HTTPException(400, "query must not be blank")
    if req.revision is not None and (
        not re.fullmatch(r"[A-Za-z0-9._/-]{1,200}", req.revision)
    ):
        raise HTTPException(400, "invalid revision")
    hostname_labels = req.hostname.split(".")
    if not hostname_labels or not all(
        re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
        for label in hostname_labels
    ):
        raise HTTPException(400, "invalid hostname")
    if req.ca_file is not None and ("\x00" in req.ca_file or not req.ca_file.strip()):
        raise HTTPException(400, "invalid CA file path")


def _validate_client_evidence(evidence: dict) -> None:
    if (
        evidence.get("vantage") != "client"
        or not isinstance(evidence.get("machine_fingerprint"), str)
        or not evidence["machine_fingerprint"].strip()
        or not isinstance(evidence.get("base_url"), str)
        or not evidence["base_url"].startswith("https://")
        or not isinstance(evidence.get("model_id"), str)
        or not evidence["model_id"].strip()
        or not isinstance(evidence.get("content_bytes"), int)
        or isinstance(evidence.get("content_bytes"), bool)
        or evidence["content_bytes"] <= 0
        or evidence.get("stream_complete") is not True
        or evidence.get("authentication_verified") is not True
        or evidence.get("tls_verified") is not True
        or evidence.get("private_routes_verified") is not True
    ):
        raise HTTPException(400, "invalid client probe evidence")


@app.post("/nucleus/runs", status_code=202)
def nucleus_create_run(request: Request, req: NucleusRunRequest) -> dict:
    _nucleus_auth(request)
    _validate_nucleus_run(req)
    from notebooks.mxstudio import deployments

    try:
        created = deployments.create_run(
            req.query.strip(),
            revision=req.revision,
            mode=req.mode,
            hostname=req.hostname.strip(),
            ca_file=req.ca_file,
        )
        run_id = created["run_id"]
        run = deployments.enqueue(run_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)[:300]) from None
    except (KeyError, RuntimeError):
        raise HTTPException(409, "deployment run could not be scheduled") from None
    return {
        "run_id": run_id,
        "job_id": run.get("job_id") if isinstance(run, dict) else None,
        "run": run if isinstance(run, dict) else created,
    }


@app.get("/nucleus/runs")
def nucleus_list_runs(request: Request) -> list[dict]:
    _nucleus_auth(request)
    from notebooks.mxstudio import deployments

    return deployments.list_runs()


@app.get("/nucleus/runs/{run_id}")
def nucleus_get_run(request: Request, run_id: str) -> dict:
    _nucleus_auth(request)
    run_id = _deployment_id(run_id)
    from notebooks.mxstudio import deployments

    run = deployments.get_run(run_id)
    if run is None:
        raise HTTPException(404, "deployment run not found")
    return run


@app.post("/nucleus/runs/{run_id}/resume", status_code=202)
def nucleus_resume_run(request: Request, run_id: str) -> dict:
    _nucleus_auth(request)
    run_id = _deployment_id(run_id)
    from notebooks.mxstudio import deployments

    try:
        run = deployments.resume(run_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)[:300]) from None
    except RuntimeError:
        raise HTTPException(409, "deployment run could not be resumed") from None
    return {
        "run_id": run.get("run_id", run_id),
        "job_id": run.get("job_id"),
        "run": run,
    }


@app.get("/nucleus/endpoints")
def nucleus_list_endpoints(request: Request) -> list[dict]:
    _nucleus_auth(request)
    from notebooks.mxstudio import deployments

    return deployments.list_endpoints()


@app.post("/nucleus/endpoints/{run_id}/verify", status_code=202)
def nucleus_verify_endpoint(
    request: Request,
    run_id: str,
    req: NucleusEndpointVerifyRequest | None = None,
) -> dict:
    _nucleus_auth(request)
    run_id = _deployment_id(run_id)
    ca_file = req.ca_file if req else None
    if ca_file is not None and ("\x00" in ca_file or not ca_file.strip()):
        raise HTTPException(400, "invalid CA file path")
    from notebooks.mxstudio import deployments

    try:
        run = deployments.get_run(run_id)
        if run is None or not run.get("endpoint"):
            raise HTTPException(404, "deployment has no endpoint")
        job = jobs.enqueue_process(
            "endpoint_verify",
            f"Verify endpoint {run_id}",
            {"run_id": run_id, "ca_file": ca_file},
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)[:300]) from None
    except RuntimeError:
        raise HTTPException(503, "endpoint verification could not be scheduled") from None
    return {"run_id": run_id, "job_id": job["job_id"], "queued": True}


@app.post("/nucleus/endpoints/{run_id}/stop", status_code=202)
def nucleus_stop_endpoint(request: Request, run_id: str) -> dict:
    _nucleus_auth(request)
    run_id = _deployment_id(run_id)
    from notebooks.mxstudio import deployments

    try:
        run = deployments.get_run(run_id)
        if run is None or not run.get("endpoint"):
            raise HTTPException(404, "deployment has no endpoint")
        job = jobs.enqueue_process(
            "endpoint_stop",
            f"Stop endpoint {run_id}",
            {"run_id": run_id},
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)[:300]) from None
    except RuntimeError:
        raise HTTPException(503, "endpoint stop could not be scheduled") from None
    return {"run_id": run_id, "job_id": job["job_id"], "queued": True}


@app.post("/nucleus/endpoints/{run_id}/client-evidence")
def nucleus_accept_client_evidence(
    request: Request,
    run_id: str,
    evidence: dict = Body(...),
) -> dict:
    _nucleus_auth(request)
    run_id = _deployment_id(run_id)
    _validate_client_evidence(evidence)
    from notebooks.mxstudio import deployments

    try:
        return deployments.record_client_evidence(run_id, evidence)
    except ValueError:
        raise HTTPException(400, "invalid client evidence or deployment binding") from None
    except RuntimeError:
        raise HTTPException(409, "client evidence could not be accepted") from None


@app.get("/nucleus/status")
def nucleus_status(request: Request) -> dict:
    _nucleus_auth(request)
    return nucleus.MANAGER.status()


class NucleusPrepareRequest(BaseModel):
    model: str | None = None
    timeout_s: float = Field(600.0, ge=0.1, le=600.0)


@app.post("/nucleus/prepare", status_code=202)
def nucleus_prepare(request: Request, req: NucleusPrepareRequest) -> dict:
    _nucleus_auth(request)
    try:
        entry = nucleus.MANAGER._entry(req.model)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from None
    return _queued(jobs.enqueue_process(
        "nucleus_prepare", f"Prepare {entry.id}",
        {"model_id": entry.id, "timeout_s": req.timeout_s},
    ))


@app.get("/nucleus/jobs/{job_id}")
def nucleus_job(request: Request, job_id: str) -> dict:
    _nucleus_auth(request)
    job = jobs.get(job_id)
    if not job or job.get("kind") != "nucleus_prepare":
        raise HTTPException(404, "preparation job not found")
    return job


@app.get("/v1/models")
def nucleus_models(request: Request) -> dict:
    _nucleus_auth(request)
    status = SERVERS.status()
    canonical_model = nucleus.model_id() or status.get("model_id")
    return {"object": "list", "data": [
        {
            "id": canonical_model,
            "model_id": canonical_model,
            "object": "model",
            "created": 0,
            "owned_by": "mxstudio",
            "revision": status.get("revision"),
        }
    ] if canonical_model else []}


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
    if request.url.path.startswith("/v1/"):
        error_type = "invalid_request_error" if exc.status_code < 500 else "server_error"
        content = {"error": {"message": str(exc.detail), "type": error_type, "param": None, "code": str(exc.status_code)}}
    else:
        content = {"detail": exc.detail}
    return JSONResponse(content, status_code=exc.status_code, headers=exc.headers)


class _LeasedStreamingResponse(StreamingResponse):
    def __init__(self, content, cleanup, **kwargs):
        super().__init__(content, **kwargs)
        self._cleanup = cleanup

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                await self._cleanup()


@app.post("/v1/chat/completions")
async def nucleus_chat(request: Request) -> Response:
    _nucleus_auth(request)
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "invalid JSON body") from None
    if not isinstance(body, dict) or not isinstance(body.get("messages"), list) or not body["messages"]:
        raise HTTPException(400, "messages must be a nonempty list")
    if not isinstance(body.get("model"), str) or not body["model"].strip():
        raise HTTPException(400, "model must be a nonempty string")
    if "stream" in body and not isinstance(body["stream"], bool):
        raise HTTPException(400, "stream must be a boolean")
    requested_model = body["model"]
    try:
        initial_status = await asyncio.to_thread(SERVERS.status)
        canonical_model = nucleus.model_id() or initial_status.get("model_id")
        advertised_model = initial_status.get("advertised_model_id") or canonical_model
        if (
            canonical_model
            and advertised_model
            and requested_model == advertised_model
        ):
            body["model"] = canonical_model
        lease = nucleus.MANAGER.lease(body["model"])
        admitted = await lease.__aenter__()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except (TimeoutError, RuntimeError) as exc:
        raise HTTPException(503, str(exc), headers={"Retry-After": "5"}) from None
    server = admitted.get("server") or {}
    body["model"] = (
        server.get("advertised_model_id")
        or server.get("model_id")
        or admitted["model_id"]
    )
    try:
        upstream = await nucleus.proxy_openai("/v1/chat/completions", body)
    except BaseException as exc:
        with anyio.CancelScope(shield=True):
            await lease.__aexit__(None, None, None)
        if isinstance(exc, httpx.TimeoutException):
            raise HTTPException(504, "MAX request timed out") from None
        if isinstance(exc, (httpx.HTTPError, RuntimeError)):
            raise HTTPException(502, "MAX request failed; inspect local diagnostics") from None
        raise
    if isinstance(upstream, tuple):
        upstream_response, upstream_client = upstream

        async def release_stream():
            async for chunk in upstream_response.aiter_bytes():
                yield chunk

        async def cleanup():
            try:
                await nucleus.close_upstream(upstream_response, upstream_client)
            finally:
                await lease.__aexit__(None, None, None)

        return _LeasedStreamingResponse(
            release_stream(), cleanup, media_type="text/event-stream",
            status_code=upstream_response.status_code,
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    try:
        return Response(
            content=upstream.content, status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "application/json").split(";")[0],
            headers={key: upstream.headers[key] for key in ("retry-after", "x-request-id") if key in upstream.headers},
        )
    finally:
        with anyio.CancelScope(shield=True):
            await lease.__aexit__(None, None, None)


@app.get("/api/server")
def server_status() -> dict:
    return SERVERS.status()


@app.post("/api/server/start")
def server_start(req: ServeRequest) -> dict:
    entry = registry.get(req.model_id)
    if entry is None:
        # Allow serving a custom HF download that isn't in the curated registry.
        if not downloads.is_installed(req.model_id):
            raise HTTPException(409, "model is not downloaded yet")
        entry = registry.ModelEntry(
            id=req.model_id,
            name=req.model_id.split("/")[-1],
            params="?",
            approx_gb=0,
            context=0,
            license="?",
            description="Custom Hugging Face model",
            innovation="",
            custom_arch=architectures.custom_arch_relative(req.model_id),
        )
    elif not downloads.is_installed(entry.id):
        raise HTTPException(409, "model is not downloaded yet")
    if not entry.serve_enabled:
        raise HTTPException(
            409,
            entry.serve_disabled_reason or "serving is disabled for this model",
        )
    try:
        return SERVERS.start(entry)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from None


@app.post("/api/server/stop")
def server_stop() -> dict:
    try:
        SERVERS.stop()
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from None
    return {"state": "stopped"}


@app.get("/api/server/logs")
def server_logs(lines: int = 100) -> dict:
    return {"logs": SERVERS.logs(lines)}


class ChatRequest(BaseModel):
    messages: list[dict]
    temperature: float = Field(0.7, ge=0.0, le=2.0)
    top_p: float = Field(0.95, gt=0.0, le=1.0)
    max_tokens: int = Field(256, ge=1, le=4096)


async def _studio_stream(model_id, content):
    reservation = SERVERS.inference_reservation(model_id)
    try:
        reservation.__enter__()
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from None
    try:
        client = AsyncOpenAI(base_url=f"http://127.0.0.1:{MODEL_PORT}/v1", api_key="EMPTY")
    except BaseException:
        reservation.__exit__(None, None, None)
        raise

    async def cleanup():
        try:
            await client.close()
        finally:
            reservation.__exit__(None, None, None)

    return _LeasedStreamingResponse(content(client), cleanup, media_type="text/event-stream")


@app.post("/api/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    status = SERVERS.status()
    if status["state"] != "running":
        raise HTTPException(
            409, f"model server is not running (state: {status['state']})"
        )
    model_id = status["model_id"]

    async def generate(client):
        try:
            stream = await client.chat.completions.create(
                model=model_id,
                messages=req.messages,
                temperature=req.temperature,
                top_p=req.top_p,
                max_tokens=req.max_tokens,
                stream=True,
            )
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    payload = json.dumps({"delta": chunk.choices[0].delta.content})
                    yield f"data: {payload}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'error': str(exc)[:300]})}\n\n"
        yield f"data: {json.dumps({'done': True})}\n\n"

    return await _studio_stream(model_id, generate)


class AgentRequest(ChatRequest):
    max_steps: int = Field(4, ge=1, le=8)


@app.post("/api/agent")
async def run_agent(req: AgentRequest) -> StreamingResponse:
    status = SERVERS.status()
    if status["state"] != "running":
        raise HTTPException(
            409, f"model server is not running (state: {status['state']})"
        )
    model_id = status["model_id"]
    return await _studio_stream(
        model_id,
        lambda client: agents.agent_events(
            client,
            model_id=model_id,
            messages=req.messages,
            temperature=req.temperature,
            top_p=req.top_p,
            max_tokens=req.max_tokens,
            max_steps=req.max_steps,
        ),
    )


@app.get("/api/hardware")
def hardware() -> dict:
    return hwinfo.summary()


@app.get("/api/bench/readiness")
def bench_ready() -> dict:
    """Prerequisites + hardware warnings before running max benchmark."""
    status = SERVERS.status()
    approx = None
    mid = status.get("model_id")
    if mid:
        entry = registry.get(mid)
        if entry:
            approx = entry.approx_gb
        else:
            # Best-effort from a fresh HF check (may be slow); skip on failure.
            try:
                report = compat.check_hf_model(mid)
                if report.get("ok"):
                    approx = report.get("approx_gb")
            except Exception:
                pass
    ready = compat.bench_readiness(approx)
    ready["server"] = {
        "state": status.get("state"),
        "model_id": mid,
        "serving": status.get("state") == "running",
    }
    if status.get("state") != "running":
        ready["ok"] = False
        ready["warnings"] = [
            "Serve a model first — max benchmark needs a live endpoint on :8100.",
            *ready.get("warnings", []),
        ]
    return ready


class ArchAnalyzeRequest(BaseModel):
    model_id: str


class ArchJobRequest(BaseModel):
    model_id: str
    kind: str = "scaffold"  # scaffold | smoke | inspect | implement


class ArchGateRequest(BaseModel):
    model_id: str
    architecture: str
    gate_id: str
    status: str
    artifacts: list[str] = Field(default_factory=list)
    notes: str = ""


@app.get("/api/architectures/wizard")
def arch_wizard_meta() -> dict:
    return architectures.wizard_meta()


@app.post("/api/architectures/open-folder")
def arch_open_folder(req: ArchAnalyzeRequest) -> dict:
    result = architectures.open_package_folder(req.model_id.strip())
    if result.get("target") is None:
        raise HTTPException(409, result.get("error") or "could not open scaffold")
    return result


@app.post("/api/architectures/analyze")
def arch_analyze(req: ArchAnalyzeRequest) -> dict:
    job = jobs.start_kind("arch_analyze", model_id=req.model_id.strip())
    return _queued(job)


@app.post("/api/architectures/approve")
def arch_approve(req: ArchAnalyzeRequest) -> dict:
    result = architectures.approve_plan(req.model_id.strip())
    if not result.get("ok"):
        raise HTTPException(400, result.get("error") or "plan approval failed")
    return result


@app.put("/api/architectures/gate")
def arch_update_gate(req: ArchGateRequest) -> dict:
    result = architectures.update_validation_gate(
        req.model_id.strip(),
        req.architecture.strip(),
        req.gate_id.strip(),
        req.status.strip().lower(),
        req.artifacts,
        req.notes,
        source=(
            "managed_max_compile"
            if req.gate_id == "strict_load" and req.status == "passed"
            else "managed_max_generation"
            if req.gate_id == "compile" and req.status == "passed"
            else "mxstudio_api"
        ),
    )
    if not result.get("ok"):
        raise HTTPException(400, result.get("error") or "validation evidence update failed")
    return result


@app.post("/api/architectures/scaffold")
def arch_scaffold(req: ArchAnalyzeRequest) -> dict:
    """Synchronous scaffold (prefer /job for UI)."""
    result = architectures.scaffold(req.model_id)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error") or "scaffold failed")
    return result


@app.post("/api/architectures/smoke")
def arch_smoke(req: ArchAnalyzeRequest) -> dict:
    return architectures.smoke_check(req.model_id)


_ARCH_KIND_MAP = {
    "scaffold": "arch_scaffold",
    "smoke": "arch_smoke",
    "arch_scaffold": "arch_scaffold",
    "arch_smoke": "arch_smoke",
    "inspect": "arch_stage",
    "implement": "arch_stage",
}


@app.post("/api/architectures/job")
def arch_job_start(req: ArchJobRequest) -> dict:
    requested = req.kind.strip().lower()
    mapped = _ARCH_KIND_MAP.get(requested)
    if not mapped:
        raise HTTPException(400, f"unknown architecture job kind: {req.kind}")
    payload = {"model_id": req.model_id.strip()}
    if mapped == "arch_stage":
        payload["stage"] = requested
    job = jobs.start_kind(mapped, **payload)
    return _queued(job)


@app.get("/api/architectures/job")
def arch_job_status(job_id: str | None = None) -> dict:
    if job_id:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, f"unknown job: {job_id}")
        return job
    # Prefer latest processing arch_* job; else most recent arch_* row.
    for j in jobs.list_public(processing_only=True):
        if str(j.get("kind") or "").startswith("arch_"):
            return j
    for j in jobs.list_public(limit=40):
        if str(j.get("kind") or "").startswith("arch_"):
            return j
    return {"state": "idle"}


class BenchRequest(BaseModel):
    runs: int = Field(3, ge=1, le=10)
    max_tokens: int = Field(128, ge=16, le=1024)
    num_prompts: int = Field(12, ge=4, le=200)
    max_concurrency: int = Field(4, ge=1, le=64)
    use_max_benchmark: bool = True
    force: bool = False


@app.post("/api/bench")
def bench_start(req: BenchRequest) -> dict:
    status = SERVERS.status()
    if status["state"] != "running":
        raise HTTPException(
            409, f"model server is not running (state: {status['state']})"
        )
    mid = status["model_id"]
    # Fast readiness: registry approx only (no HF on request thread).
    entry = registry.get(mid)
    approx = entry.approx_gb if entry else None
    ready = compat.bench_readiness(approx)
    if not ready.get("ok") and not req.force:
        raise HTTPException(
            409,
            {
                "message": "hardware/prerequisites look unsafe for benchmarking",
                "readiness": ready,
            },
        )
    job = jobs.start_kind(
        "bench",
        model_id=mid,
        runs=req.runs,
        max_tokens=req.max_tokens,
        num_prompts=req.num_prompts,
        max_concurrency=req.max_concurrency,
        use_max_benchmark=req.use_max_benchmark,
    )
    return {**_queued(job), "readiness": ready}


@app.get("/api/bench")
def bench_status() -> dict:
    return BENCH.status()


@app.get("/api/bench/history")
def bench_history() -> list[dict]:
    return BENCH.history()


@app.get("/api/activity")
def activity() -> dict:
    """Aggregate background jobs for the global activity bar."""
    chips: list[dict] = []
    for j in jobs.list_public(processing_only=True):
        chips.append(
            {
                "id": j["job_id"],
                "kind": j["kind"],
                "label": j["label"],
                "detail": j.get("detail") or f"{int(j.get('progress') or 0)}%",
                "state": "running",
            }
        )
    server = SERVERS.status()
    if server.get("state") == "starting":
        chips.append(
            {
                "id": "serve",
                "kind": "serve",
                "label": f"Starting {server.get('model_id', 'model')}…",
                "detail": f"{server.get('uptime_s', 0)}s",
                "state": "running",
            }
        )
    architecture = {"state": "idle"}
    for candidate in jobs.list_public(limit=40):
        if str(candidate.get("kind") or "").startswith("arch_"):
            architecture = candidate
            break
    return {
        "jobs": chips,
        "server": server,
        "notebook": notebooks.NOTEBOOK_JOBS.status(),
        "bench": BENCH.status(),
        "architecture": architecture,
        "sql_jobs": True,
    }


# Static UI last so /api/* wins.
app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
