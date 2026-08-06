"""mxstudio backend: REST API + static web UI."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from notebooks.mxstudio import architectures, compat, downloads, fhsaas, hwinfo, jobs, modelinfo, notebooks, registry, settings as studio_settings
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from notebooks.mxstudio import (
    setup,
)
from notebooks.mxstudio.bench import MANAGER as BENCH
from notebooks.mxstudio.downloads import MANAGER as DOWNLOADS
from notebooks.mxstudio.servers import MANAGER as SERVERS, MODEL_PORT

WEB_DIR = Path(__file__).parent / "web"

app = FastAPI(title="mxstudio", version="0.1.1")

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
    return SERVERS.start(entry)


@app.post("/api/server/stop")
def server_stop() -> dict:
    SERVERS.stop()
    return {"state": "stopped"}


@app.get("/api/server/logs")
def server_logs(lines: int = 100) -> dict:
    return {"logs": SERVERS.logs(lines)}


class ChatRequest(BaseModel):
    messages: list[dict]
    temperature: float = Field(0.7, ge=0.0, le=2.0)
    top_p: float = Field(0.95, gt=0.0, le=1.0)
    max_tokens: int = Field(256, ge=1, le=4096)


@app.post("/api/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    status = SERVERS.status()
    if status["state"] != "running":
        raise HTTPException(
            409, f"model server is not running (state: {status['state']})"
        )
    model_id = status["model_id"]

    async def generate():
        client = AsyncOpenAI(
            base_url=f"http://127.0.0.1:{MODEL_PORT}/v1", api_key="EMPTY"
        )
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

    return StreamingResponse(generate(), media_type="text/event-stream")


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
