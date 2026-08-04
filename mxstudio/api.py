"""mxstudio backend: REST API + static web UI."""

from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from mxstudio import compat, downloads, fhsaas, hwinfo, modelinfo, notebooks, registry
from mxstudio.bench import MANAGER as BENCH
from mxstudio.downloads import MANAGER as DOWNLOADS
from mxstudio.servers import MANAGER as SERVERS, MODEL_PORT

WEB_DIR = Path(__file__).parent / "web"

app = FastAPI(title="mxstudio", version="0.1.0")

# fh-saas base: logging config + the additive /api/jobs job-state bridge.
fhsaas.setup()
fhsaas.register_jobs_api(app)


def _entry_or_404(model_id: str) -> registry.ModelEntry:
    entry = registry.get(model_id)
    if entry is None:
        raise HTTPException(404, f"unknown model: {model_id}")
    return entry


@app.get("/api/env")
def env_info() -> dict:
    """HF token / cache / disk readiness for downloads."""
    return compat.env_status()


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
                "download": DOWNLOADS.status(entry.id),
                "server_state": (
                    server["state"]
                    if server.get("model_id") == entry.id
                    else None
                ),
            }
        )
    # Surface any custom HF downloads that aren't in the curated registry.
    for model_id, job in list(DOWNLOADS._jobs.items()):  # noqa: SLF001
        if model_id in seen:
            continue
        result.append(
            {
                "id": model_id,
                "name": model_id.split("/")[-1],
                "params": "?",
                "approx_gb": 0,
                "context": 0,
                "license": "?",
                "description": "Custom Hugging Face download",
                "innovation": "",
                "links": {"Hugging Face": f"https://huggingface.co/{model_id}"},
                "experimental": True,
                "custom_arch": None,
                "cuda_devices": "0",
                "family": "",
                "architecture": "",
                "when_to_use": "",
                "key_ideas": [],
                "learning_goals": [],
                "streaming_target": False,
                "installed": downloads.is_installed(model_id),
                "download": DOWNLOADS.status(model_id),
                "server_state": (
                    server["state"] if server.get("model_id") == model_id else None
                ),
            }
        )
    return result


class HfCheckRequest(BaseModel):
    model_id: str


class HfDownloadRequest(BaseModel):
    model_id: str
    force: bool = False  # allow download even if modular_compatible is false


@app.post("/api/models/check")
def check_model(req: HfCheckRequest) -> dict:
    """Validate a Hugging Face id + Modular compatibility + env readiness."""
    return compat.check_hf_model(req.model_id)


@app.post("/api/models/download-hf")
def download_hf_model(req: HfDownloadRequest) -> dict:
    """Download any HF repo after a compatibility/env check."""
    report = compat.check_hf_model(req.model_id)
    if not report.get("ok"):
        raise HTTPException(400, report.get("error") or "invalid model id")
    if not report.get("can_download") and not req.force:
        raise HTTPException(
            409,
            {
                "message": "not ready to download — fix env / accept license first",
                "report": report,
            },
        )
    if not report.get("modular_compatible") and not req.force:
        raise HTTPException(
            409,
            {
                "message": (
                    "architecture may not be Modular-compatible; "
                    "re-submit with force=true to download anyway"
                ),
                "report": report,
            },
        )
    DOWNLOADS.total_size_bytes(req.model_id.strip())
    job = DOWNLOADS.start(req.model_id.strip())
    return {"download": job, "report": report}


@app.post("/api/models/{model_id:path}/download")
def download_model(model_id: str) -> dict:
    entry = _entry_or_404(model_id)
    DOWNLOADS.total_size_bytes(entry.id)  # warm the size cache for progress %
    return DOWNLOADS.start(entry.id)


@app.delete("/api/models/{model_id:path}")
def uninstall_model(model_id: str) -> dict:
    """Remove a downloaded model from the local HF cache to free space."""
    server = SERVERS.status()
    if server.get("state") in ("running", "starting") and server.get("model_id") == model_id:
        raise HTTPException(409, "stop the server before uninstalling this model")
    job = DOWNLOADS.status(model_id)
    if job and job.get("state") == "downloading":
        raise HTTPException(409, "download still in progress")
    # Allow uninstall for curated or custom ids
    result = downloads.uninstall(model_id)
    DOWNLOADS._jobs.pop(model_id, None)  # noqa: SLF001
    DOWNLOADS._total_cache.pop(model_id, None)  # noqa: SLF001
    return result


@app.get("/api/models/{model_id:path}/card")
def model_card(model_id: str) -> dict:
    entry = _entry_or_404(model_id)
    return {"markdown": modelinfo.get_model_card(entry.id)}


@app.post("/api/models/{model_id:path}/notebook")
def make_notebook(model_id: str) -> dict:
    entry = _entry_or_404(model_id)
    path = notebooks.write_notebook(entry)
    return {"path": str(path), "name": path.name}


@app.get("/api/notebooks")
def list_notebooks() -> list[dict]:
    return notebooks.list_notebooks()


@app.get("/api/notebooks/job")
def notebook_job() -> dict:
    """Must be registered before /api/notebooks/{name}."""
    return notebooks.NOTEBOOK_JOBS.status()


@app.get("/api/notebooks/{name}")
def get_notebook(name: str) -> dict:
    try:
        return notebooks.load_notebook(name)
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
        )
    elif not downloads.is_installed(entry.id):
        raise HTTPException(409, "model is not downloaded yet")
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


class BenchRequest(BaseModel):
    runs: int = Field(3, ge=1, le=10)
    max_tokens: int = Field(128, ge=16, le=1024)
    num_prompts: int = Field(12, ge=4, le=200)
    max_concurrency: int = Field(4, ge=1, le=64)
    use_max_benchmark: bool = True


@app.post("/api/bench")
def bench_start(req: BenchRequest) -> dict:
    status = SERVERS.status()
    if status["state"] != "running":
        raise HTTPException(
            409, f"model server is not running (state: {status['state']})"
        )
    return BENCH.start(
        status["model_id"],
        runs=req.runs,
        max_tokens=req.max_tokens,
        num_prompts=req.num_prompts,
        max_concurrency=req.max_concurrency,
        use_max_benchmark=req.use_max_benchmark,
    )


@app.get("/api/bench")
def bench_status() -> dict:
    return BENCH.status()


@app.get("/api/bench/history")
def bench_history() -> list[dict]:
    return BENCH.history()


@app.get("/api/activity")
def activity() -> dict:
    """Aggregate background jobs for the global activity bar."""
    started_at = time.perf_counter()
    jobs: list[dict] = []
    server = SERVERS.status()
    if server.get("state") == "starting":
        jobs.append(
            {
                "id": "serve",
                "kind": "serve",
                "label": f"Starting {server.get('model_id', 'model')}…",
                "detail": f"{server.get('uptime_s', 0)}s",
                "state": "running",
            }
        )
    for model_id, raw in list(DOWNLOADS._jobs.items()):  # noqa: SLF001
        if raw.get("state") != "downloading":
            continue
        dl = DOWNLOADS.status(model_id) or raw
        pct = dl.get("pct")
        entry = registry.get(model_id)
        label = entry.name if entry else model_id
        jobs.append(
            {
                "id": f"dl:{model_id}",
                "kind": "download",
                "label": f"Downloading {label}",
                "detail": f"{pct}%" if pct is not None else "working…",
                "state": "running",
            }
        )
    bench = BENCH.status()
    if bench.get("state") == "running":
        jobs.append(
            {
                "id": "bench",
                "kind": "bench",
                "label": f"Benchmarking {bench.get('model_id', '')}",
                "detail": f"{len(bench.get('runs', []))}/{bench.get('runs_total', '?')} runs",
                "state": "running",
            }
        )
    nb = notebooks.NOTEBOOK_JOBS.status()
    if nb.get("state") == "running":
        cur = nb.get("current_cell")
        jobs.append(
            {
                "id": "notebook",
                "kind": "notebook",
                "label": f"Notebook {nb.get('notebook', '')}",
                "detail": (
                    "running all cells…"
                    if nb.get("mode") == "all"
                    else f"cell {cur}"
                ),
                "state": "running",
            }
        )
    return {"jobs": jobs, "server": server, "bench": bench, "notebook": nb}


# Static UI last so /api/* wins.
app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
