"""The one SQLite file that remembers registered models and per-app keys."""
from __future__ import annotations

import math
import fcntl
import os
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from mxconsole.kinds import LabelError, check_kind, check_label, derive_kind
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
    kind TEXT NOT NULL DEFAULT 'chat',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS labels (
    name TEXT PRIMARY KEY,
    position INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS model_labels (
    model TEXT NOT NULL,
    label TEXT NOT NULL,
    PRIMARY KEY (model, label)
);
CREATE INDEX IF NOT EXISTS model_labels_label ON model_labels (label);
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
    kind: str = "chat"  # chat | coder | vision | embeddings | image
    labels: tuple[str, ...] = ()  # your labels, sorted by name


@dataclass(frozen=True)
class Label:
    name: str
    position: int
    count: int


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
        if "kind" not in cols:  # once, offline: the name and architecture are all we have for old models
            with self._tx():  # all or nothing: a crash must not leave the column without its backfill
                self.db.execute("ALTER TABLE models ADD COLUMN kind TEXT NOT NULL DEFAULT 'chat'")
                for r in self.db.execute("SELECT name, architecture FROM models").fetchall():
                    self.db.execute("UPDATE models SET kind = ? WHERE name = ?",
                                    (derive_kind(name=r["name"], architecture=r["architecture"]), r["name"]))

    @contextmanager
    def _tx(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        self.db.execute("COMMIT")

    @classmethod
    def open(cls) -> "Store":
        return cls(mxc_home() / "mst.db")

    def add_model(self, *, name: str, repo_id: str, revision: str, source: str,
                  architecture: str, weights_path: str | None = None,
                  custom_arch_path: str | None = None, kind: str | None = None) -> Model:
        """Registers (or re-registers) a model. `kind` is derived from the name and architecture when not
        given; a re-pull never changes a kind that is already set."""
        kind = check_kind(kind) if kind else derive_kind(name=name, architecture=architecture)
        with self._tx():
            if self.db.execute("SELECT 1 FROM models WHERE name = ?", (name,)).fetchone() is None:
                self.db.execute("DELETE FROM model_labels WHERE model = ?", (name,))  # strays from an older daemon
            self.db.execute(
                """INSERT INTO models (name, repo_id, revision, source, architecture, weights_path,
                                       custom_arch_path, kind, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET repo_id=excluded.repo_id, revision=excluded.revision,
                     source=excluded.source, architecture=excluded.architecture,
                     weights_path=excluded.weights_path,
                     custom_arch_path=excluded.custom_arch_path""",
                (name, repo_id, revision, source, architecture, weights_path, custom_arch_path, kind, time.time()),
            )
        return self.get_model(name)

    def get_model(self, name: str) -> Model | None:
        row = self.db.execute("SELECT * FROM models WHERE name = ?", (name,)).fetchone()
        return _model(row, self._links(name).get(name, ())) if row else None

    def models(self) -> list[Model]:
        links = self._links()
        return [_model(r, links.get(r["name"], ())) for r in self.db.execute("SELECT * FROM models ORDER BY name")]

    def _links(self, model: str | None = None) -> dict[str, list[str]]:
        rows = self.db.execute("SELECT model, label FROM model_labels" + (" WHERE model = ?" if model else "")
                               + " ORDER BY label", (model,) if model else ()).fetchall()
        out: dict[str, list[str]] = {}
        for r in rows:
            out.setdefault(r["model"], []).append(r["label"])
        return out

    def set_lifecycle(self, name: str, *, pinned: bool | None = None, keep_alive: int | None = None) -> None:
        if pinned is not None:
            self.db.execute("UPDATE models SET pinned = ? WHERE name = ?", (int(pinned), name))
        if keep_alive is not None:
            self.db.execute("UPDATE models SET keep_alive = ? WHERE name = ?", (keep_alive, name))

    def remove_model(self, name: str) -> bool:
        with self._tx():
            self.db.execute("DELETE FROM model_labels WHERE model = ?", (name,))
            return self.db.execute("DELETE FROM models WHERE name = ?", (name,)).rowcount > 0

    # ---- kind and your labels --------------------------------------------

    def set_kind(self, name: str, kind: str) -> None:
        check_kind(kind)
        if not self.db.execute("UPDATE models SET kind = ? WHERE name = ?", (kind, name)).rowcount:
            raise _no_model(name)

    def labels(self) -> list[Label]:
        return [Label(r["name"], r["position"], r["n"]) for r in self.db.execute(
            """SELECT l.name, l.position, COUNT(x.name) AS n FROM labels l
               LEFT JOIN model_labels m ON m.label = l.name
               LEFT JOIN models x ON x.name = m.model GROUP BY l.name ORDER BY l.position""")]

    def _label_exists(self, name: str) -> bool:
        return self.db.execute("SELECT 1 FROM labels WHERE name = ?", (name,)).fetchone() is not None

    def _need_label(self, name: str) -> None:
        if not self._label_exists(name):
            raise LabelError(f"no label named {name}. See: mxc labels", "label_not_found")

    def _make_label(self, name: str) -> None:
        self.db.execute("INSERT INTO labels (name, position) VALUES (?, "
                        "(SELECT COALESCE(MAX(position) + 1, 0) FROM labels))", (name,))

    def create_label(self, name: str) -> None:
        check_label(name)
        with self._tx():
            if self._label_exists(name):
                raise LabelError(f"a label named {name} already exists", "label_exists")
            self._make_label(name)

    def rename_label(self, old: str, new: str) -> None:
        check_label(new)
        with self._tx():
            self._need_label(old)
            if new != old and self._label_exists(new):
                raise LabelError(f"a label named {new} already exists", "label_exists")
            self.db.execute("UPDATE labels SET name = ? WHERE name = ?", (new, old))
            self.db.execute("UPDATE model_labels SET label = ? WHERE label = ?", (new, old))

    def reorder_labels(self, names: list[str]) -> None:
        with self._tx():
            if sorted(names) != sorted(l.name for l in self.labels()):
                raise LabelError("send every label exactly once to reorder them", "invalid_order")
            for i, name in enumerate(names):
                self.db.execute("UPDATE labels SET position = ? WHERE name = ?", (i, name))

    def delete_label(self, name: str) -> None:
        with self._tx():
            self._need_label(name)
            self.db.execute("DELETE FROM model_labels WHERE label = ?", (name,))
            self.db.execute("DELETE FROM labels WHERE name = ?", (name,))

    def add_labels(self, models: list[str], labels: list[str]) -> None:
        """Put every label (created when new) on every model. All or nothing."""
        for label in labels:
            check_label(label)
        with self._tx():
            self._need_models(models)
            for label in labels:
                if not self._label_exists(label):
                    self._make_label(label)
                self.db.executemany("INSERT OR IGNORE INTO model_labels (model, label) VALUES (?, ?)",
                                    [(m, label) for m in models])

    def remove_labels(self, models: list[str], labels: list[str]) -> None:
        with self._tx():
            self._need_models(models)
            for label in labels:
                self._need_label(label)
                self.db.executemany("DELETE FROM model_labels WHERE model = ? AND label = ?",
                                    [(m, label) for m in models])

    def _need_models(self, models: list[str]) -> None:
        for m in models:
            if self.db.execute("SELECT 1 FROM models WHERE name = ?", (m,)).fetchone() is None:
                raise _no_model(m)


def _no_model(name: str) -> LabelError:
    return LabelError(f"no model named {name}. See: mxc ls", "model_not_found")


def _model(row: sqlite3.Row, labels: tuple[str, ...] = ()) -> Model:
    return Model(
        name=row["name"], repo_id=row["repo_id"], revision=row["revision"], source=row["source"],
        architecture=row["architecture"], pinned=bool(row["pinned"]), keep_alive=row["keep_alive"],
        weights_path=row["weights_path"], custom_arch_path=row["custom_arch_path"],
        kind=row["kind"], labels=tuple(labels),
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
