"""The one SQLite file that remembers registered models and per-app keys."""
from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

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
CREATE TABLE IF NOT EXISTS keys (
    app TEXT PRIMARY KEY,
    prefix TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL,
    last_used_at REAL,
    revoked_at REAL
);
"""


def mst_home() -> Path:
    home = Path(os.environ.get("MST_HOME", Path.home() / ".mst")).expanduser()
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
    def __init__(self, path: Path):
        self.path = path
        path.touch(mode=0o600, exist_ok=True)
        path.chmod(0o600)
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(models)")}
        if "custom_arch_path" not in cols:
            self.db.execute("ALTER TABLE models ADD COLUMN custom_arch_path TEXT")

    @classmethod
    def open(cls) -> "Store":
        return cls(mst_home() / "mst.db")

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
    if text and text[-1] in units:
        return int(float(text[:-1]) * units[text[-1]])
    return int(text)
