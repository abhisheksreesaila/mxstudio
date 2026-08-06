"""SQLite job store under ~/.mxstudio/jobs.db (fh-saas JobState-friendly)."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from notebooks.mxstudio import state

DB_PATH = state.HOME / "jobs.db"
_lock = threading.Lock()

# Align with fh-saas JobState values where possible.
STATES = ("queued", "processing", "done", "failed")


def _connect() -> sqlite3.Connection:
    state.HOME.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    label TEXT NOT NULL,
                    state TEXT NOT NULL,
                    detail TEXT DEFAULT '',
                    progress REAL DEFAULT 0,
                    result_json TEXT,
                    error TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_updated ON jobs(updated_at DESC)"
            )
            conn.commit()
        finally:
            conn.close()


def fail_stale_jobs(max_age_s: float = 15.0) -> int:
    """Mark abandoned jobs failed while preserving workers with fresh heartbeats."""
    cutoff = time.time() - max_age_s
    with _lock:
        conn = _connect()
        try:
            cursor = conn.execute(
                """
                UPDATE jobs
                SET state = 'failed',
                    detail = 'interrupted',
                    error = 'job worker stopped; retry the action',
                    progress = 0,
                    updated_at = ?
                WHERE state IN ('queued', 'processing') AND updated_at < ?
                """,
                (time.time(), cutoff),
            )
            conn.commit()
            return cursor.rowcount
        finally:
            conn.close()


def new_id(prefix: str = "job") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def create(
    *,
    kind: str,
    label: str,
    job_id: str | None = None,
    detail: str = "",
) -> dict:
    init_db()
    jid = job_id or new_id(kind.replace("_", "-")[:16])
    now = time.time()
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                """
                INSERT INTO jobs
                  (job_id, kind, label, state, detail, progress, result_json, error, created_at, updated_at)
                VALUES (?, ?, ?, 'queued', ?, 0, NULL, NULL, ?, ?)
                """,
                (jid, kind, label, detail, now, now),
            )
            conn.commit()
        finally:
            conn.close()
    return get(jid) or {"job_id": jid, "kind": kind, "state": "queued"}


def update(
    job_id: str,
    *,
    state: str | None = None,
    detail: str | None = None,
    progress: float | None = None,
    result: dict | list | None = None,
    error: str | None = None,
) -> dict | None:
    init_db()
    fields: list[str] = ["updated_at = ?"]
    values: list = [time.time()]
    if state is not None:
        if state not in STATES:
            raise ValueError(f"bad state: {state}")
        fields.append("state = ?")
        values.append(state)
    if detail is not None:
        fields.append("detail = ?")
        values.append(detail)
    if progress is not None:
        fields.append("progress = ?")
        values.append(float(progress))
    if result is not None:
        fields.append("result_json = ?")
        values.append(json.dumps(result))
    if error is not None:
        fields.append("error = ?")
        values.append(error[:1000])
    values.append(job_id)
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                f"UPDATE jobs SET {', '.join(fields)} WHERE job_id = ?",
                values,
            )
            conn.commit()
        finally:
            conn.close()
    return get(job_id)


def get(job_id: str) -> dict | None:
    init_db()
    with _lock:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        finally:
            conn.close()
    return _row_to_dict(row) if row else None


def list_jobs(
    *,
    limit: int = 80,
    processing_only: bool = False,
) -> list[dict]:
    init_db()
    with _lock:
        conn = _connect()
        try:
            if processing_only:
                rows = conn.execute(
                    """
                    SELECT * FROM jobs
                    WHERE state IN ('queued', 'processing')
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM jobs
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        finally:
            conn.close()
    return [_row_to_dict(r) for r in rows]


def _row_to_dict(row: sqlite3.Row) -> dict:
    result = None
    if row["result_json"]:
        try:
            result = json.loads(row["result_json"])
        except json.JSONDecodeError:
            result = None
    return {
        "job_id": row["job_id"],
        "kind": row["kind"],
        "label": row["label"],
        "state": row["state"],
        "detail": row["detail"] or "",
        "progress": row["progress"] or 0,
        "result": result,
        "error": row["error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
