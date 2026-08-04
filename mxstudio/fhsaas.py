"""fh-saas integration layer for mxstudio.

fh-saas is the user's FastHTML-based SaaS/starter toolkit. mxstudio's UI is a
static HTML/JS frontend served by FastAPI, so this module adopts only the
backend utilities that map cleanly onto the existing app:

- ``utils_log``      -> one-time logging setup + env-gated timing metrics
- ``utils_workflow`` -> sequential startup steps
- ``utils_ai_interface`` -> the JobState contract + polling endpoints, exposed
  here as an additive ``/api/jobs`` bridge over mxstudio's existing background
  job managers (downloads, model server, benchmark, notebook runs).

See docs/FH_SAAS_INTEGRATION.md for what was adopted, what was deferred, and
how future fh-saas releases should flow in.
"""

from __future__ import annotations

import logging

from fh_saas.utils_ai_interface import AIInterfaceConfig, Interface, JobState
from fh_saas.utils_log import configure_logging, log_timing_event
from fh_saas.utils_workflow import Workflow

logger = logging.getLogger("mxstudio.fhsaas")

_setup_done = False


def _configure_logging() -> None:
    # Env-driven: FH_SAAS_LOG_LEVEL (default WARNING), FH_SAAS_LOG_FILE.
    configure_logging()


def _announce() -> None:
    try:
        from importlib.metadata import version

        fh_version = version("fh-saas")
    except Exception:
        fh_version = "unknown"
    logger.info("fh-saas %s active", fh_version)


def setup() -> None:
    """One-time fh-saas bootstrap; safe to call repeatedly."""
    global _setup_done
    if _setup_done:
        return
    Workflow([_configure_logging, _announce]).execute()
    _setup_done = True


def timing(label: str, started_at: float) -> None:
    """Emit a timing metric via fh-saas when FH_TIMING_ENABLED is on."""
    log_timing_event(logger, label, started_at)


# ---------- fh-saas AI-interface job bridge ----------

# mxstudio manager states -> fh-saas JobState values.
_STATE_MAP = {
    "downloading": JobState.processing,
    "starting": JobState.processing,
    "running": JobState.processing,
    "done": JobState.done,
    "error": JobState.failed,
    "crashed": JobState.failed,
}


def _job(job_id: str, kind: str, label: str, raw_state: str, detail: str = "",
         progress: int = 0, result: dict | None = None) -> dict | None:
    state = _STATE_MAP.get(raw_state)
    if state is None:
        return None
    return {
        "job_id": job_id,
        "kind": kind,
        "label": label,
        "state": state.value,
        "mxstudio_state": raw_state,
        "detail": detail,
        "progress": progress,
        "result": result,
    }


def collect_jobs() -> list[dict]:
    """Snapshot every mxstudio background job in fh-saas JobState terms."""
    from mxstudio import downloads, notebooks, registry
    from mxstudio.bench import MANAGER as BENCH
    from mxstudio.servers import MANAGER as SERVERS

    jobs: list[dict] = []

    for entry in registry.REGISTRY:
        dl = downloads.status(entry.id)
        if not dl or dl.get("state") not in ("downloading", "error", "done"):
            continue
        pct = dl.get("pct")
        jobs.append(_job(
            job_id=f"dl:{entry.id}",
            kind="download",
            label=f"Download {entry.name}",
            raw_state=dl["state"],
            detail=(
                f"{pct}%" if dl["state"] == "downloading" and pct is not None
                else dl.get("error", "") or ""
            ),
            progress=pct if isinstance(pct, (int, float)) else 0,
            result=(
                {"done_bytes": dl.get("done_bytes"), "total_bytes": dl.get("total_bytes")}
                if dl["state"] == "done" else None
            ),
        ))

    server = SERVERS.status()
    if server.get("state") in ("starting", "running", "crashed"):
        jobs.append(_job(
            job_id="serve:current",
            kind="serve",
            label=f"Serve {server.get('model_id', 'model')}",
            raw_state=server["state"],
            detail=f"{server.get('uptime_s', 0)}s",
            progress=100 if server["state"] == "running" else 0,
            result=(
                {
                    "model_id": server.get("model_id"),
                    "port": server.get("port"),
                    "uptime_s": server.get("uptime_s"),
                }
                if server["state"] == "running" else None
            ),
        ))

    bench = BENCH.status()
    if bench.get("state") in ("running", "done", "error"):
        runs_total = bench.get("runs_total") or 0
        runs_done = len(bench.get("runs") or [])
        jobs.append(_job(
            job_id="bench:current",
            kind="bench",
            label=f"Benchmark {bench.get('model_id', '')}",
            raw_state=bench["state"],
            detail=f"{runs_done}/{runs_total} runs",
            progress=round(runs_done / runs_total * 100) if runs_total else 0,
            result=(
                {"summary": bench.get("summary"), "runs": bench.get("runs")}
                if bench["state"] == "done" else None
            ),
        ))

    nb = notebooks.NOTEBOOK_JOBS.status()
    if nb.get("state") in ("running", "done", "error"):
        jobs.append(_job(
            job_id=f"nb:{nb.get('notebook', 'current')}",
            kind="notebook",
            label=f"Notebook {nb.get('notebook', '')}",
            raw_state=nb["state"],
            detail=(
                "running all cells" if nb.get("mode") == "all"
                else f"cell {nb.get('current_cell')}"
            ),
            result={} if nb["state"] == "done" else None,
        ))

    return [j for j in jobs if j is not None]


def build_interface() -> Interface:
    """The fh-saas Interface describing mxstudio's background-job contract."""
    return Interface(
        fn=collect_jobs,
        inputs=[],
        outputs=[],
        name="mxstudio background jobs",
        config=AIInterfaceConfig.from_env(),
    )


def register_jobs_api(app) -> Interface:
    """Attach fh-saas style /api/jobs routes to the FastAPI app.

    Additive only: the existing /api/* endpoints and the static UI are
    untouched. Call BEFORE the catch-all StaticFiles mount.
    """
    from fastapi import HTTPException

    interface = build_interface()
    config = interface.config

    @app.get("/api/jobs/meta")
    def jobs_meta() -> dict:
        return {
            "interface": interface.describe(),
            "polling_contract": {
                "status_endpoint_template": config.status_endpoint_template,
                "result_endpoint_template": config.result_endpoint_template,
                "poll_interval_ms": config.default_poll_interval_ms,
            },
        }

    @app.get("/api/jobs")
    def jobs_list() -> dict:
        return {"jobs": collect_jobs()}

    # Declare the more specific /result route before the path-param route.
    @app.get("/api/jobs/{job_id:path}/result")
    def job_result(job_id: str) -> dict:
        for job in collect_jobs():
            if job["job_id"] == job_id:
                if job["state"] != JobState.done.value:
                    raise HTTPException(409, f"job is not done (state: {job['state']})")
                return {"job_id": job_id, "result": job.get("result")}
        raise HTTPException(404, f"unknown job: {job_id}")

    @app.get("/api/jobs/{job_id:path}")
    def job_status(job_id: str) -> dict:
        for job in collect_jobs():
            if job["job_id"] == job_id:
                return {**job, **interface.build_polling_contract(job_id)}
        raise HTTPException(404, f"unknown job: {job_id}")

    logger.info("fh-saas job API registered at /api/jobs")
    return interface
