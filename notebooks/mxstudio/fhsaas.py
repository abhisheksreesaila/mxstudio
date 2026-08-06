"""fh-saas integration layer for mxstudio.

Backend utilities from fh-saas:

- ``utils_log`` / ``utils_workflow`` — bootstrap
- ``utils_ai_interface`` — JobState contract + polling endpoints over the
  SQLite-backed job queue (``mxstudio.jobs``) plus live serve status.

Long work must never run on the request thread — enqueue via ``jobs.start_kind``.
"""

from __future__ import annotations

import logging

from fh_saas.utils_ai_interface import AIInterfaceConfig, Interface, JobState
from fh_saas.utils_log import configure_logging, log_timing_event
from fh_saas.utils_workflow import Workflow
from pydantic import BaseModel, Field

logger = logging.getLogger("mxstudio.fhsaas")

_setup_done = False


class JobStartRequest(BaseModel):
    """JSON body accepted by the generic background-job endpoint."""

    kind: str
    model_id: str | None = None
    force: bool = False
    curated: bool = False
    runs: int = Field(3, ge=1, le=10)
    max_tokens: int = Field(128, ge=16, le=1024)
    num_prompts: int = Field(12, ge=4, le=200)
    max_concurrency: int = Field(4, ge=1, le=64)
    use_max_benchmark: bool = True
    label: str | None = None


def _configure_logging() -> None:
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
    try:
        from notebooks.mxstudio import jobs_db

        jobs_db.init_db()
        recovered = jobs_db.fail_stale_jobs()
        if recovered:
            logger.warning("marked %s interrupted background job(s) failed", recovered)
    except Exception:
        logger.exception("jobs.db init failed")
    _setup_done = True


def timing(label: str, started_at: float) -> None:
    log_timing_event(logger, label, started_at)


def collect_jobs() -> list[dict]:
    """SQLite queue jobs + live serve chip (serve is process-owned, not queued)."""
    from notebooks.mxstudio import jobs
    from notebooks.mxstudio.servers import MANAGER as SERVERS

    out = jobs.list_public(limit=100)

    # Serve is still a long-lived process — surface it alongside SQL jobs.
    server = SERVERS.status()
    if server.get("state") in ("starting", "running", "crashed"):
        raw = server["state"]
        fh = {
            "starting": JobState.processing.value,
            "running": JobState.done.value,  # "ready" — keep visible as done-ish
            "crashed": JobState.failed.value,
        }.get(raw, JobState.processing.value)
        # For activity we want starting as processing; running as a soft presence.
        if raw == "starting":
            out.insert(
                0,
                {
                    "job_id": "serve:current",
                    "kind": "serve",
                    "label": f"Serve {server.get('model_id', 'model')}",
                    "state": JobState.processing.value,
                    "mxstudio_state": raw,
                    "detail": f"{server.get('uptime_s', 0)}s",
                    "progress": 0,
                    "result": None,
                    "error": None,
                },
            )
        elif raw == "crashed":
            out.insert(
                0,
                {
                    "job_id": "serve:current",
                    "kind": "serve",
                    "label": f"Serve {server.get('model_id', 'model')}",
                    "state": fh,
                    "mxstudio_state": raw,
                    "detail": "crashed",
                    "progress": 0,
                    "result": None,
                    "error": "server crashed",
                },
            )

    # Deduplicate by job_id (prefer freshest order already)
    seen: set[str] = set()
    uniq: list[dict] = []
    for j in out:
        if not j or j["job_id"] in seen:
            continue
        seen.add(j["job_id"])
        uniq.append(j)
    return uniq


def build_interface() -> Interface:
    return Interface(
        fn=collect_jobs,
        inputs=[],
        outputs=[],
        name="mxstudio background jobs",
        config=AIInterfaceConfig.from_env(),
    )


def register_jobs_api(app) -> Interface:
    """Attach /api/jobs routes. Call BEFORE StaticFiles mount."""
    from fastapi import HTTPException

    from notebooks.mxstudio import jobs

    interface = build_interface()
    config = interface.config

    @app.get("/api/jobs/meta")
    def jobs_meta() -> dict:
        return {
            "interface": interface.describe(),
            "kinds": sorted(jobs.HANDLERS.keys()),
            "db": str(__import__("mxstudio.jobs_db", fromlist=["DB_PATH"]).DB_PATH),
            "polling_contract": {
                "status_endpoint_template": config.status_endpoint_template,
                "result_endpoint_template": config.result_endpoint_template,
                "poll_interval_ms": config.default_poll_interval_ms,
            },
        }

    @app.post("/api/jobs/start")
    def jobs_start(req: JobStartRequest) -> dict:
        """Enqueue long work — returns immediately with job_id."""
        payload = req.model_dump(exclude_none=True)
        kind = payload.pop("kind")
        label = payload.pop("label", None)
        try:
            return jobs.start_kind(kind, label=label, **payload)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except TypeError as exc:
            raise HTTPException(400, f"bad payload for {kind}: {exc}") from exc

    @app.get("/api/jobs")
    def jobs_list(processing_only: bool = False) -> dict:
        if processing_only:
            return {"jobs": jobs.list_public(processing_only=True)}
        return {"jobs": collect_jobs()}

    @app.get("/api/jobs/{job_id:path}/result")
    def job_result(job_id: str) -> dict:
        job = jobs.get(job_id)
        if job is None:
            # fall back to live collect (e.g. serve)
            for j in collect_jobs():
                if j["job_id"] == job_id:
                    job = j
                    break
        if job is None:
            raise HTTPException(404, f"unknown job: {job_id}")
        if job["state"] != JobState.done.value:
            raise HTTPException(409, f"job is not done (state: {job['state']})")
        return {"job_id": job_id, "result": job.get("result")}

    @app.get("/api/jobs/{job_id:path}")
    def job_status(job_id: str) -> dict:
        job = jobs.get(job_id)
        if job is None:
            for j in collect_jobs():
                if j["job_id"] == job_id:
                    job = j
                    break
        if job is None:
            raise HTTPException(404, f"unknown job: {job_id}")
        return {**job, **interface.build_polling_contract(job_id)}

    logger.info("fh-saas job API registered at /api/jobs (+ POST /api/jobs/start)")
    return interface
