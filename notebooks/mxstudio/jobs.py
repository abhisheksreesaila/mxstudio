"""Background job queue — never run long work on the request thread.

Persists to SQLite (jobs_db) so the UI can poll /api/jobs and the activity bar
stays responsive. fh-saas JobState: queued/processing → processing, done, failed.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from notebooks.mxstudio import jobs_db
from notebooks.mxstudio import compat, downloads

logger = logging.getLogger("mxstudio.jobs")

_workers_lock = threading.Lock()
_active_threads: dict[str, threading.Thread] = {}


def _fh_state(raw: str) -> str:
    if raw in ("queued", "processing"):
        return "processing"
    if raw == "done":
        return "done"
    if raw == "failed":
        return "failed"
    return raw


def public_job(row: dict) -> dict:
    """Shape used by /api/jobs and the activity bar."""
    return {
        "job_id": row["job_id"],
        "kind": row["kind"],
        "label": row["label"],
        "state": _fh_state(row["state"]),
        "mxstudio_state": row["state"],
        "detail": row.get("detail") or "",
        "progress": row.get("progress") or 0,
        "result": row.get("result"),
        "error": row.get("error"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def enqueue(
    kind: str,
    label: str,
    fn: Callable[..., Any],
    *args: Any,
    job_id: str | None = None,
    detail: str = "queued…",
    **kwargs: Any,
) -> dict:
    """Create a DB row and run ``fn`` on a daemon thread. Returns public job dict."""
    jobs_db.init_db()
    row = jobs_db.create(kind=kind, label=label, job_id=job_id, detail=detail)
    jid = row["job_id"]

    def _worker() -> None:
        jobs_db.update(jid, state="processing", detail="starting…", progress=1)
        try:
            result = fn(
                *args,
                job_id=jid,
                progress=lambda d, p=None: jobs_db.update(
                    jid,
                    detail=d,
                    progress=p if p is not None else None,
                ),
                **kwargs,
            )
            jobs_db.update(
                jid,
                state="done",
                detail="done",
                progress=100,
                result=result if isinstance(result, (dict, list)) else {"value": result},
            )
        except Exception as exc:
            logger.exception("job %s failed", jid)
            jobs_db.update(
                jid,
                state="failed",
                detail="failed",
                error=str(exc)[:800],
                progress=0,
            )
        finally:
            with _workers_lock:
                _active_threads.pop(jid, None)

    t = threading.Thread(target=_worker, daemon=True, name=f"job-{kind}-{jid[-8:]}")
    with _workers_lock:
        _active_threads[jid] = t
    t.start()
    return public_job(jobs_db.get(jid) or row)


def enqueue_process(kind: str, label: str, payload: dict) -> dict:
    """Run a durable job outside the web process so UI restarts do not stop it."""
    from notebooks.mxstudio import state

    jobs_db.init_db()
    row = jobs_db.create(kind=kind, label=label, detail="starting worker…")
    jid = row["job_id"]
    project_root = Path(__file__).resolve().parents[2]
    state.LOGS.mkdir(parents=True, exist_ok=True)
    log_path = state.LOGS / f"job_{jid}.log"
    log_file = open(log_path, "ab")
    env = os.environ.copy()
    pixi_bin = str(project_root / ".pixi" / "envs" / "default" / "bin")
    env["PATH"] = pixi_bin + os.pathsep + env.get("PATH", "")
    try:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "mxstudio.job_worker",
                jid,
                kind,
                json.dumps(payload),
            ],
            cwd=str(project_root),
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except Exception as exc:
        jobs_db.update(
            jid,
            state="failed",
            detail="worker failed to start",
            error=str(exc),
        )
        raise
    finally:
        log_file.close()
    jobs_db.update(
        jid,
        detail=f"worker {proc.pid} starting…",
        progress=1,
    )
    return public_job(jobs_db.get(jid) or row)


def get(job_id: str) -> dict | None:
    row = jobs_db.get(job_id)
    return public_job(row) if row else None


def list_public(*, limit: int = 80, processing_only: bool = False) -> list[dict]:
    return [
        public_job(r)
        for r in jobs_db.list_jobs(limit=limit, processing_only=processing_only)
    ]


def run_hf_check(model_id: str, *, job_id: str, progress: Callable) -> dict:
    from notebooks.mxstudio import compat

    progress("Looking up Hugging Face metadata…", 10)
    report = compat.check_hf_model(model_id)
    progress("Check complete", 100)
    return {"report": report}


def run_arch_analyze(model_id: str, *, job_id: str, progress: Callable) -> dict:
    from notebooks.mxstudio import architectures

    progress("Inspecting architecture vs MAX…", 15)
    analysis = architectures.analyze(model_id)
    progress("Gap analysis ready", 100)
    return {"analysis": analysis}


def run_arch_scaffold(model_id: str, *, job_id: str, progress: Callable) -> dict:
    from notebooks.mxstudio import architectures

    progress("Writing ports/ package…", 20)
    result = architectures.scaffold(model_id)
    if not result.get("ok") and result.get("error"):
        raise RuntimeError(result["error"])
    progress(result.get("message") or "scaffold done", 100)
    return result


def run_arch_smoke(model_id: str, *, job_id: str, progress: Callable) -> dict:
    from notebooks.mxstudio import architectures

    progress("Syntax + TODO scan…", 25)
    result = architectures.smoke_check(model_id)
    progress(result.get("smoke", {}).get("message") or "smoke done", 100)
    return result


def run_arch_stage(
    model_id: str, stage: str, *, job_id: str, progress: Callable
) -> dict:
    from notebooks.mxstudio import architectures

    result = architectures.run_skill_stage(model_id, stage, progress=progress)
    if not result.get("ok"):
        raise RuntimeError(result.get("stderr") or result.get("error") or f"{stage} failed")
    return result


def run_download(
    model_id: str,
    *,
    force: bool = False,
    curated: bool = False,
    job_id: str,
    progress: Callable,
) -> dict:
    """HF gate + download in one background job (UI never waits on check)."""
    from notebooks.mxstudio import registry
    from notebooks.mxstudio.downloads import MANAGER as DOWNLOADS

    progress("Compatibility & hardware check…", 5)
    report = compat.check_hf_model(model_id)
    if not report.get("ok"):
        raise RuntimeError(report.get("error") or "invalid model id")

    entry = registry.get(model_id) if curated else None
    if not report.get("can_download") and not force:
        raise RuntimeError("not ready to download — fix env / accept license first")
    if (
        not report.get("modular_compatible")
        and not (entry and entry.custom_arch)
        and not force
    ):
        raise RuntimeError(
            "architecture may not be Modular-compatible — re-run with force"
        )
    hw_level = (report.get("hardware") or {}).get("level")
    if hw_level in ("slow", "insufficient") and not force:
        raise RuntimeError(
            "hardware fit looks poor — re-run with force to download at your own risk"
        )

    progress("Resolving package size…", 15)
    try:
        DOWNLOADS.total_size_bytes(model_id)
    except Exception:
        pass

    progress("Downloading weights…", 20)
    DOWNLOADS.start(model_id)

    while True:
        time.sleep(0.6)
        st = DOWNLOADS.status(model_id) or {}
        state = st.get("state")
        pct = st.get("pct")
        if pct is not None:
            progress(f"Downloading… {pct}%", 20 + min(75, float(pct) * 0.75))
        else:
            progress("Downloading…", None)
        if state == "done":
            progress("Download complete", 100)
            info = downloads.local_info(model_id)
            return {"download": st, "report": report, "local": info}
        if state == "error":
            raise RuntimeError(st.get("error") or "download failed")


def run_model_card(model_id: str, *, job_id: str, progress: Callable) -> dict:
    from notebooks.mxstudio import modelinfo

    progress("Fetching Hugging Face model card…", 20)
    md = modelinfo.get_model_card(model_id)
    progress("Card ready", 100)
    return {"markdown": md or "(model card not available)"}


def run_notebook_generate(
    model_id: str, *, job_id: str, progress: Callable
) -> dict:
    """Generate the notebook and cached slide deck in one persistent job."""
    from notebooks.mxstudio import notebooks

    progress("Building notebook + animated story deck…", 20)
    result = notebooks.generate_notebook(model_id)
    progress("Notebook and animated Reveal deck ready", 100)
    return result


def run_bench(
    model_id: str,
    *,
    runs: int = 3,
    max_tokens: int = 128,
    num_prompts: int = 12,
    max_concurrency: int = 4,
    use_max_benchmark: bool = True,
    job_id: str,
    progress: Callable,
) -> dict:
    from notebooks.mxstudio.bench import MANAGER as BENCH

    progress("Starting max benchmark…", 5)
    BENCH.start(
        model_id,
        runs=runs,
        max_tokens=max_tokens,
        num_prompts=num_prompts,
        max_concurrency=max_concurrency,
        use_max_benchmark=use_max_benchmark,
    )
    while True:
        time.sleep(0.8)
        st = BENCH.status()
        state = st.get("state")
        runs_done = len(st.get("runs") or [])
        total = st.get("runs_total") or runs
        backend = st.get("backend", "")
        if backend == "max_benchmark":
            progress(
                f"MAX benchmark running · {st.get('num_prompts', num_prompts)} prompts",
                15,
            )
        else:
            pct = round(runs_done / total * 90) if total else 10
            progress(
                f"Stream timing… {runs_done}/{total}",
                min(95, 5 + pct),
            )
        if state == "done":
            progress("Benchmark complete", 100)
            return dict(st)
        if state == "error":
            raise RuntimeError(st.get("error") or "benchmark failed")


def run_nucleus_prepare(model_id: str, timeout_s: float = 600.0, *, job_id: str, progress: Callable) -> dict:
    import asyncio
    from notebooks.mxstudio.nucleus import MANAGER

    progress("Starting model; waiting for MAX readiness")
    result = asyncio.run(MANAGER.prepare(model_id, timeout_s))
    progress("Model ready", 100)
    return result


def run_deployment(run_id: str, *, job_id: str, progress: Callable) -> dict:
    from notebooks.mxstudio import deployments

    return deployments.execute(run_id, job_id=job_id, progress=progress)


def run_endpoint_verify(run_id: str, ca_file: str | None = None, *, job_id: str, progress: Callable) -> dict:
    from notebooks.mxstudio import deployments

    progress("Verifying authenticated endpoint generation", 5)
    return deployments.verify_endpoint(run_id, ca_file=ca_file)


def run_endpoint_stop(run_id: str, *, job_id: str, progress: Callable) -> dict:
    from notebooks.mxstudio import deployments

    progress("Stopping owned endpoint resources", 5)
    return deployments.stop_endpoint(run_id)


HANDLERS: dict[str, Callable[..., Any]] = {
    "deployment": run_deployment,
    "endpoint_verify": run_endpoint_verify,
    "endpoint_stop": run_endpoint_stop,
    "nucleus_prepare": run_nucleus_prepare,
    "hf_check": run_hf_check,
    "arch_analyze": run_arch_analyze,
    "arch_scaffold": run_arch_scaffold,
    "arch_smoke": run_arch_smoke,
    "arch_stage": run_arch_stage,
    "download": run_download,
    "model_card": run_model_card,
    "notebook_generate": run_notebook_generate,
    "bench": run_bench,
}

_KIND_KEYS: dict[str, set[str]] = {
    "deployment": {"run_id"},
    "endpoint_verify": {"run_id", "ca_file"},
    "endpoint_stop": {"run_id"},
    "nucleus_prepare": {"model_id", "timeout_s"},
    "hf_check": {"model_id"},
    "arch_analyze": {"model_id"},
    "arch_scaffold": {"model_id"},
    "arch_smoke": {"model_id"},
    "arch_stage": {"model_id", "stage"},
    "download": {"model_id", "force", "curated"},
    "model_card": {"model_id"},
    "notebook_generate": {"model_id"},
    "bench": {
        "model_id",
        "runs",
        "max_tokens",
        "num_prompts",
        "max_concurrency",
        "use_max_benchmark",
    },
}


def start_kind(kind: str, *, label: str | None = None, **payload: Any) -> dict:
    """Dispatch a known kind from /api/jobs/start."""
    if kind not in HANDLERS or kind in {"deployment", "endpoint_verify", "endpoint_stop"}:
        raise ValueError(f"unknown job kind: {kind}")
    handler = HANDLERS[kind]
    allowed = _KIND_KEYS.get(kind, set(payload))
    clean = {k: v for k, v in payload.items() if k in allowed}
    model_id = str(clean.get("model_id") or "")
    labels = {
        "hf_check": f"Check {model_id}",
        "arch_analyze": f"Analyze {model_id}",
        "arch_scaffold": f"Scaffold {model_id}",
        "arch_smoke": f"Smoke {model_id}",
        "arch_stage": f"Architecture stage {model_id}",
        "download": f"Download {model_id}",
        "model_card": f"Model card {model_id}",
        "notebook_generate": f"Notebook {model_id}",
        "bench": f"Benchmark {model_id}",
    }

    def _run(*, job_id: str, progress: Callable) -> Any:
        return handler(job_id=job_id, progress=progress, **clean)

    job_label = label or labels.get(kind, kind)
    if kind in {
        "download",
        "arch_analyze",
        "arch_scaffold",
        "arch_smoke",
        "arch_stage",
    }:
        return enqueue_process(kind, job_label, clean)
    return enqueue(kind, job_label, _run)
