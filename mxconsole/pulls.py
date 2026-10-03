"""Pull jobs: resolve a model ref, download it with live progress, and register it.

One `PullManager` runs jobs in background threads. The daemon owns one (the console and
`mxc pull` talk to it over /admin/pulls); `mxc pull` makes its own when no daemon is running.
Every job is a row in the `pulls` table, so recent pulls survive a restart.

The Hugging Face client and the MAX catalog are injected (see resolve.py), so tests never
touch the network.
"""
from __future__ import annotations

import collections
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Callable

from mxconsole import resolve
from mxconsole.store import Store

log = logging.getLogger("mxconsole.pulls")

STEPS = [("resolve", "Resolve"), ("architecture", "Architecture"), ("download", "Download"),
         ("verify", "Verify"), ("register", "Register")]
STEP_IDS = [s for s, _ in STEPS]
FINISHED = ("done", "failed", "cancelled", "unsupported")
SPEED_WINDOW = 4.0   # seconds of history the speed is averaged over
SAVE_EVERY = 0.5     # seconds between database writes while bytes are moving
KEEP_ROWS = 50       # finished pulls kept for "recent pulls"

REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}(/[A-Za-z0-9][A-Za-z0-9._-]{0,95})?$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS pulls (
    id INTEGER PRIMARY KEY,
    ref TEXT NOT NULL,
    name TEXT,
    repo_id TEXT,
    status TEXT NOT NULL,
    step TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    hint TEXT,
    error_kind TEXT,
    started_at REAL NOT NULL,
    finished_at REAL,
    total_bytes INTEGER NOT NULL DEFAULT 0,
    done_bytes INTEGER NOT NULL DEFAULT 0,
    speed REAL NOT NULL DEFAULT 0,
    files TEXT NOT NULL DEFAULT '[]',
    details TEXT NOT NULL DEFAULT '{}',
    model TEXT
);
"""


class PullBusy(Exception):
    """A pull of this ref is already running; `.job` is its view."""

    def __init__(self, job: dict):
        super().__init__(f"{job['ref']} is already being pulled")
        self.job = job


class BadPull(ValueError):
    """The ref or name is not something we will pass on."""


def repo_key(ref: str) -> str:
    return resolve.CURATED.get(ref.lower(), ref).lower()


class _Job:
    """The live state of one pull. Only the worker thread writes; `view()` is safe from any thread."""

    def __init__(self, id: int, ref: str, name: str | None):
        self.id, self.ref, self.name = id, ref, name
        self.repo_id: str | None = None
        self.status, self.step = "running", "resolve"
        self.message, self.hint, self.error_kind = "", None, None
        self.started_at, self.finished_at = time.time(), None
        self.files: dict[str, dict] = {}
        self.details: dict[str, str] = {}
        self.model: str | None = None
        self.cancel = threading.Event()
        self.lock = threading.Lock()
        self._samples: collections.deque = collections.deque()
        self._speed = 0.0
        self.thread: threading.Thread | None = None
        self.last_saved = 0.0

    # -- progress ---------------------------------------------------------------------------
    def bytes_done(self) -> int:
        return sum(f["done"] for f in self.files.values())

    def bytes_total(self) -> int:
        return sum(f["size"] or 0 for f in self.files.values())

    def on_progress(self, filename: str, done: int, total: int | None) -> None:
        with self.lock:
            f = self.files.setdefault(filename, {"name": filename, "size": None, "done": 0})
            if total:
                f["size"] = total
            f["done"] = max(f["done"], done) if done else f["done"]
            now = time.monotonic()
            self._samples.append((now, self.bytes_done()))
            while len(self._samples) > 2 and now - self._samples[0][0] > SPEED_WINDOW:
                self._samples.popleft()
            t0, b0 = self._samples[0]
            self._speed = (self.bytes_done() - b0) / (now - t0) if now - t0 > 0.05 else self._speed

    def view(self) -> dict:
        with self.lock:
            running = self.status == "running"
            total, done = self.bytes_total(), self.bytes_done()
            speed = self._speed if running and self.step == "download" else 0.0
            # idle for a moment (a stalled link): the average decays rather than freezing on the last burst
            if speed and self._samples and time.monotonic() - self._samples[-1][0] > SPEED_WINDOW:
                speed = 0.0
            return {
                "id": self.id, "ref": self.ref, "name": self.name, "repo_id": self.repo_id,
                "status": self.status, "step": self.step, "message": self.message, "hint": self.hint,
                "error_kind": self.error_kind, "started_at": self.started_at, "finished_at": self.finished_at,
                "total_bytes": total, "done_bytes": done,
                "percent": (100.0 if self.status == "done" else round(done * 100 / total, 1)) if total else
                           (100.0 if self.status == "done" else None),
                "speed": round(speed), "eta_seconds": round((total - done) / speed) if speed and total > done else None,
                "files": [dict(f, state=self._file_state(f)) for f in self.files.values()],
                "steps": self._steps(), "model": self.model,
            }

    def _file_state(self, f: dict) -> str:
        if f["size"] and f["done"] >= f["size"]:
            return "done"
        return "now" if f["done"] else "next"

    def _steps(self) -> list[dict]:
        out = []
        at = STEP_IDS.index(self.step)
        for i, (sid, label) in enumerate(STEPS):
            if self.status == "done" or i < at:
                state = "done"
            elif i == at:
                state = {"running": "now", "cancelled": "cancelled"}.get(self.status, "failed")
            else:
                state = "next"
            out.append({"id": sid, "label": label, "state": state, "detail": self.details.get(sid, "")})
        return out


class PullManager:
    """Starts, tracks, cancels and remembers pulls.

    hub_factory() gives a Hugging Face client (see resolve.HubClient); catalog_loader() the MAX catalog.
    Both are looked up per job, so tests can swap them with monkeypatch.
    """

    def __init__(self, store: Store, *, hub_factory: Callable | None = None,
                 catalog_loader: Callable | None = None, on_registered: Callable | None = None):
        self.store = store
        self.hub_factory = hub_factory or (lambda: resolve.HubClient())
        self.catalog_loader = catalog_loader or (lambda: resolve.load_catalog())
        self.on_registered = on_registered
        self.db_lock = threading.Lock()
        self.live: dict[int, _Job] = {}
        self.live_lock = threading.Lock()
        with self.db_lock:
            store.db.executescript(SCHEMA)
            # a daemon that stopped mid-pull leaves "running" rows nobody is working on
            store.db.execute("UPDATE pulls SET status='failed', finished_at=?, error_kind='interrupted', "
                             "message='interrupted: the daemon stopped during this pull. Pull it again to resume.' "
                             "WHERE status='running'", (time.time(),))

    # -- public ---------------------------------------------------------------------------------
    def start(self, ref: str, name: str | None = None) -> dict:
        ref = (ref or "").strip()
        if not REF_RE.match(ref):
            raise BadPull("Send a short name (qwen2.5-0.5b) or a Hugging Face id like owner/repo.")
        if name is not None and not NAME_RE.match(name):
            raise BadPull("`name` may use letters, digits, '.', '_' and '-' (64 characters at most).")
        with self.live_lock:
            for j in self.live.values():
                if repo_key(j.ref) == repo_key(ref) and j.status == "running":
                    raise PullBusy(j.view())
            with self.db_lock:
                cur = self.store.db.execute("INSERT INTO pulls (ref, name, status, step, started_at) "
                                            "VALUES (?, ?, 'running', 'resolve', ?)", (ref, name, time.time()))
            job = _Job(cur.lastrowid, ref, name)
            self.live[job.id] = job
        job.thread = threading.Thread(target=self._run, args=(job,), name=f"pull-{job.id}", daemon=True)
        job.thread.start()
        return job.view()

    def get(self, pull_id: int) -> dict | None:
        job = self.live.get(pull_id)
        if job:
            return job.view()
        with self.db_lock:
            row = self.store.db.execute("SELECT * FROM pulls WHERE id = ?", (pull_id,)).fetchone()
        return _row_view(row) if row else None

    def list(self, limit: int = 20) -> list[dict]:
        with self.db_lock:
            rows = self.store.db.execute("SELECT * FROM pulls ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [self.live[r["id"]].view() if r["id"] in self.live else _row_view(r) for r in rows]

    def cancel(self, pull_id: int) -> dict | None:
        job = self.live.get(pull_id)
        if job is None:
            return self.get(pull_id)
        job.cancel.set()
        return job.view()

    def wait(self, pull_id: int, timeout: float = 30.0) -> dict | None:
        job = self.live.get(pull_id)
        if job and job.thread:
            job.thread.join(timeout)
        return self.get(pull_id)

    def cancel_all(self, timeout: float = 5.0) -> None:
        jobs = list(self.live.values())
        for j in jobs:
            j.cancel.set()
        for j in jobs:
            if j.thread:
                j.thread.join(timeout)

    # -- the pipeline ----------------------------------------------------------------------------
    def _set_step(self, job: _Job, step: str, detail: str | None = None) -> None:
        with job.lock:
            job.step = step
            if detail is not None:
                job.details[step] = detail
        self._save(job)

    def _detail(self, job: _Job, step: str, detail: str) -> None:
        with job.lock:
            job.details[step] = detail

    def _run(self, job: _Job) -> None:
        try:
            self._pipeline(job)
        except resolve.PullCancelled:
            self._finish(job, "cancelled", message="Cancelled. Partial files are kept, so pulling again resumes.")
        except resolve.PullError as e:
            kind = e.resolution.kind
            if kind == "unsupported":
                self._finish(job, "unsupported", message=e.resolution.message, error_kind=kind,
                             hint=f"mxc port {job.ref}")
            else:
                self._finish(job, "failed", message=e.resolution.message, error_kind=kind)
        except resolve.ResolveError as e:
            self._finish(job, "failed", message=str(e), error_kind="error")
        except Exception as e:  # a bug or a surprise must end the job, not hang it as "running"
            log.exception("pull %s crashed", job.id)
            self._finish(job, "failed", message=f"unexpected error: {type(e).__name__}: {str(e)[:160]}",
                         error_kind="error")

    def _pipeline(self, job: _Job) -> None:
        hub = self.hub_factory()
        # 1 + 2: resolve the ref, then check MAX can run the architecture
        self._set_step(job, "resolve", f"looking up {job.ref} on Hugging Face…")
        catalog = self.catalog_loader()
        job_check(job)
        res = resolve.resolve(job.ref, catalog=catalog, hf=hub)
        job.repo_id = res.repo_id
        alias = f"{job.ref} → {res.repo_id}" if job.ref.lower() != res.repo_id.lower() else res.repo_id
        if res.kind in ("not-found", "gated"):
            self._detail(job, "resolve", res.message)
            raise resolve.PullError(res)
        self._detail(job, "resolve", alias + (f" @ {res.revision[:8]}" if res.revision else ""))
        self._set_step(job, "architecture")
        if not res.supported:
            self._detail(job, "architecture", res.message)
            raise resolve.PullError(res)
        self._detail(job, "architecture", f"{res.architecture} is {'in the MAX catalog' if res.kind == 'max-catalog' else 'supported by MAX'}")
        name = job.name or resolve.model_name(job.ref, res.repo_id)
        job.name = name
        resolve.check_name_free(self.store, name, job.ref, res)
        job_check(job)

        # 3: download
        self._set_step(job, "download", "starting…")
        path = resolve.download_snapshot(res, hub, progress=lambda f, d, t: self._progress(job, f, d, t),
                                         cancel=job.cancel)
        job_check(job)

        # 4: verify what landed matches what the hub said
        self._set_step(job, "verify", "checking file sizes…")
        bad = _verify(path, job)
        if bad:
            raise resolve.ResolveError(f"{bad} did not download completely; pull again to resume.")
        self._detail(job, "verify", f"{len(job.files)} file{'s' if len(job.files) != 1 else ''} match their sizes" if job.files else "nothing to compare")

        # 5: register
        self._set_step(job, "register", f"adding {name} to your models…")
        model = self.store.add_model(name=name, repo_id=res.repo_id, revision=res.revision, source=res.kind,
                                     architecture=res.architecture, weights_path=str(path))
        self._detail(job, "register", f"served as {model.name} · keep-alive {model.keep_alive // 60} min")
        job.model = model.name
        if self.on_registered:
            try:
                self.on_registered(model)
            except Exception:
                log.exception("on_registered failed")
        self._finish(job, "done", message=f"Pulled {model.name} ({model.repo_id}@{model.revision[:8]}, {model.source}).")

    def _progress(self, job: _Job, filename: str, done: int, total: int | None) -> None:
        job.on_progress(filename, done, total)
        if job.cancel.is_set():
            raise resolve.PullCancelled()
        files, size, got = len(job.files), job.bytes_total(), job.bytes_done()
        finished = sum(1 for f in job.files.values() if f["size"] and f["done"] >= f["size"])
        self._detail(job, "download", f"{min(finished + 1, files)} of {files} files · {_mb(got)} of {_mb(size)}")
        if time.monotonic() - job.last_saved >= SAVE_EVERY:
            self._save(job)

    def _finish(self, job: _Job, status: str, *, message: str, error_kind: str | None = None, hint: str | None = None) -> None:
        with job.lock:
            job.status, job.message, job.hint, job.error_kind = status, message, hint, error_kind
            job.finished_at = time.time()
        self._save(job)
        with self.live_lock:
            self.live.pop(job.id, None)
        with self.db_lock:
            self.store.db.execute("DELETE FROM pulls WHERE id <= (SELECT MAX(id) FROM pulls) - ? AND status != 'running'",
                                  (KEEP_ROWS,))

    def _save(self, job: _Job) -> None:
        v = job.view()
        job.last_saved = time.monotonic()
        try:
            with self.db_lock:
                self.store.db.execute(
                    """UPDATE pulls SET name=?, repo_id=?, status=?, step=?, message=?, hint=?, error_kind=?, finished_at=?,
                       total_bytes=?, done_bytes=?, speed=?, files=?, details=?, model=? WHERE id=?""",
                    (v["name"], v["repo_id"], v["status"], v["step"], v["message"], v["hint"], v["error_kind"],
                     v["finished_at"], v["total_bytes"], v["done_bytes"], v["speed"],
                     json.dumps([{k: f[k] for k in ("name", "size", "done")} for f in v["files"]]),
                     json.dumps(job.details), v["model"], job.id))
        except Exception:  # a locked database must not fail the download
            log.exception("could not save pull %s", job.id)


def job_check(job: _Job) -> None:
    if job.cancel.is_set():
        raise resolve.PullCancelled()


def _verify(snapshot: str, job: _Job) -> str | None:
    """The name of the first file that is missing or short, among files whose size the hub told us."""
    for f in job.files.values():
        if not f["size"]:
            continue
        p = Path(snapshot) / f["name"]
        if not p.is_file() or p.stat().st_size != f["size"]:
            return f["name"]
    return None


def _mb(n: int) -> str:
    if n >= 1 << 30:
        return f"{n / (1 << 30):.2f} GB"
    if n >= 1 << 20:
        return f"{n / (1 << 20):.1f} MB"
    return f"{n / 1024:.0f} KB" if n else "0 KB"


def _row_view(row) -> dict:
    """A finished (or interrupted) pull rebuilt from its database row, in the same shape as a live one."""
    job = _Job(row["id"], row["ref"], row["name"])
    job.repo_id, job.status, job.step = row["repo_id"], row["status"], row["step"]
    job.message, job.hint, job.error_kind = row["message"], row["hint"], row["error_kind"]
    job.started_at, job.finished_at, job.model = row["started_at"], row["finished_at"], row["model"]
    job.details = json.loads(row["details"] or "{}")
    for f in json.loads(row["files"] or "[]"):
        job.files[f["name"]] = f
    return job.view()
