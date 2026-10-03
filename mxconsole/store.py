"""The one SQLite file that remembers registered models and per-app keys."""
from __future__ import annotations

import math
import fcntl
import os
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from mxconsole.settings import env

DEFAULT_KEEP_ALIVE = 300  # seconds, like Ollama's keep_alive

SCHEMA = """
CREATE TABLE IF NOT EXISTS models (
    name TEXT PRIMARY KEY,
    repo_id TEXT NOT NULL,
    revision TEXT NOT NULL,
    source TEXT NOT NULL,
    architecture TEXT NOT NULL,
    pinned INTEGER NOT NULL DEFAULT 0,
    keep_alive INTEGER NOT NULL DEFAULT 300,
    weights_path TEXT,
    custom_arch_path TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    app TEXT,
    model TEXT,
    route TEXT NOT NULL,
    status INTEGER NOT NULL,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    ttft_ms REAL,
    duration_ms REAL NOT NULL,
    estimated INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS requests_ts ON requests (ts);
CREATE TABLE IF NOT EXISTS keys (
    app TEXT PRIMARY KEY,
    prefix TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL,
    last_used_at REAL,
    revoked_at REAL
);
"""


def _old_daemon_running(old: Path) -> bool:
    """A pre-rename daemon still holds <old>/run/daemon.lock: moving its folder would split its state."""
    lock = old / "run" / "daemon.lock"
    if not lock.exists():
        return False
    try:
        with open(lock, "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(f, fcntl.LOCK_UN)
    except OSError:
        return True
    return False


def _migrate_legacy_home(new: Path) -> Path:
    """Move ~/.hotfire (or else ~/.mst) to ~/.mxc once, when only an old folder exists. Returns the folder to use."""
    if new.exists():
        return new
    old = next((p for p in (Path.home() / ".hotfire", Path.home() / ".mst") if p.is_dir()), None)
    if old is None:
        return new
    if _old_daemon_running(old):
        print(f"mxc: an older daemon is still using {old}; using it until that daemon stops "
              f"(then run any mxc command to move it to {new})", file=sys.stderr)
        return old
    try:
        old.rename(new)
    except FileNotFoundError:  # another mxc command moved it a moment ago
        return new
    except OSError as e:
        if new.exists():  # another mxc command moved the other old folder there first: use it
            return new
        # a mount point or another device: rename can't do it
        print(f"mxc: could not move {old} to {new} ({e.strerror}); move it by hand. "
              f"Using {old} for now.", file=sys.stderr)
        return old
    print(f"mxc: moved {old} to {new}", file=sys.stderr)
    return new


def mxc_home() -> Path:
    configured = env("HOME")
    if configured:
        home = Path(configured).expanduser()
    else:
        home = _migrate_legacy_home(Path.home() / ".mxc")
    home.mkdir(parents=True, exist_ok=True)
    home.chmod(0o700)
    return home


@dataclass(frozen=True)
class Model:
    name: str
    repo_id: str
    revision: str
    source: str
    architecture: str
    pinned: bool
    keep_alive: int
    weights_path: str | None
    custom_arch_path: str | None = None


class Store:
    """One SQLite file, one connection per thread.

    The daemon uses a Store from the event loop and from the threadpool (sync routes, pull and bench
    workers) at the same time. A single sqlite3 connection shared across threads fails with
    InterfaceError, so each thread gets its own; WAL lets readers and one writer work together, and
    busy_timeout makes a writer wait for another instead of failing.
    """

    BUSY_TIMEOUT_MS = 10_000

    def __init__(self, path: Path):
        self.path = path
        path.touch(mode=0o600, exist_ok=True)
        path.chmod(0o600)
        self._local = threading.local()
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self._migrate()

    @property
    def db(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, isolation_level=None, timeout=self.BUSY_TIMEOUT_MS / 1000)
            conn.row_factory = sqlite3.Row
            conn.execute(f"PRAGMA busy_timeout = {self.BUSY_TIMEOUT_MS}")
            self._local.conn = conn
        return conn

    def _migrate(self) -> None:
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(models)")}
        if "custom_arch_path" not in cols:
            self.db.execute("ALTER TABLE models ADD COLUMN custom_arch_path TEXT")

    @classmethod
    def open(cls) -> "Store":
        return cls(mxc_home() / "mst.db")

    def add_model(self, *, name: str, repo_id: str, revision: str, source: str,
                  architecture: str, weights_path: str | None = None,
                  custom_arch_path: str | None = None) -> Model:
        self.db.execute(
            """INSERT INTO models (name, repo_id, revision, source, architecture, weights_path,
                                   custom_arch_path, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET repo_id=excluded.repo_id, revision=excluded.revision,
                 source=excluded.source, architecture=excluded.architecture,
                 weights_path=excluded.weights_path,
                 custom_arch_path=excluded.custom_arch_path""",
            (name, repo_id, revision, source, architecture, weights_path, custom_arch_path, time.time()),
        )
        return self.get_model(name)

    def get_model(self, name: str) -> Model | None:
        row = self.db.execute("SELECT * FROM models WHERE name = ?", (name,)).fetchone()
        return _model(row) if row else None

    def models(self) -> list[Model]:
        return [_model(r) for r in self.db.execute("SELECT * FROM models ORDER BY name")]

    def set_lifecycle(self, name: str, *, pinned: bool | None = None, keep_alive: int | None = None) -> None:
        if pinned is not None:
            self.db.execute("UPDATE models SET pinned = ? WHERE name = ?", (int(pinned), name))
        if keep_alive is not None:
            self.db.execute("UPDATE models SET keep_alive = ? WHERE name = ?", (keep_alive, name))

    def remove_model(self, name: str) -> bool:
        return self.db.execute("DELETE FROM models WHERE name = ?", (name,)).rowcount > 0


def _model(row: sqlite3.Row) -> Model:
    return Model(
        name=row["name"], repo_id=row["repo_id"], revision=row["revision"], source=row["source"],
        architecture=row["architecture"], pinned=bool(row["pinned"]), keep_alive=row["keep_alive"],
        weights_path=row["weights_path"], custom_arch_path=row["custom_arch_path"],
    )


def format_duration(seconds: int) -> str:
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def parse_duration(text: str) -> int:
    """'90s', '10m', '1h' or a bare number of seconds."""
    text = text.strip().lower()
    units = {"s": 1, "m": 60, "h": 3600}
    number, unit = (text[:-1], units[text[-1]]) if text and text[-1] in units else (text, 1)
    value = float(number) * unit
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{text!r} is not a duration")
    return int(value)
