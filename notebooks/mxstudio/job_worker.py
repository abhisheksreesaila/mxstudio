"""Detached worker for jobs that must survive mxstudio web UI restarts."""

from __future__ import annotations

import json
import logging
import sys
import threading

from notebooks.mxstudio import jobs_db
from notebooks.mxstudio import jobs

logger = logging.getLogger("mxstudio.job_worker")


def run(job_id: str, kind: str, payload: dict) -> int:
    handler = jobs.HANDLERS.get(kind)
    if handler is None:
        jobs_db.update(
            job_id,
            state="failed",
            detail="unknown job kind",
            error=f"unknown job kind: {kind}",
        )
        return 2

    jobs_db.update(job_id, state="processing", detail="starting…", progress=1)
    heartbeat_stop = threading.Event()

    def heartbeat() -> None:
        while not heartbeat_stop.wait(5):
            jobs_db.update(job_id)

    threading.Thread(
        target=heartbeat,
        daemon=True,
        name=f"heartbeat-{job_id[-8:]}",
    ).start()
    try:
        result = handler(
            job_id=job_id,
            progress=lambda detail, progress=None: jobs_db.update(
                job_id,
                detail=detail,
                progress=progress if progress is not None else None,
            ),
            **payload,
        )
        jobs_db.update(
            job_id,
            state="done",
            detail="done",
            progress=100,
            result=result if isinstance(result, (dict, list)) else {"value": result},
        )
        return 0
    except Exception as exc:
        logger.exception("detached job %s failed", job_id)
        jobs_db.update(
            job_id,
            state="failed",
            detail="failed",
            progress=0,
            error=str(exc)[:800],
        )
        return 1
    finally:
        heartbeat_stop.set()


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: python -m mxstudio.job_worker JOB_ID KIND PAYLOAD_JSON")
        return 2
    job_id, kind, raw_payload = sys.argv[1:]
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        jobs_db.update(
            job_id,
            state="failed",
            detail="invalid worker payload",
            error=str(exc),
        )
        return 2
    return run(job_id, kind, payload)


if __name__ == "__main__":
    raise SystemExit(main())
