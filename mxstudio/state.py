"""Tiny JSON state store under ~/.mxstudio (server pid, bench history)."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

HOME = Path(os.environ.get("MXSTUDIO_HOME", str(Path.home() / ".mxstudio")))
LOGS = HOME / "logs"
STATE_FILE = HOME / "state.json"

_lock = threading.Lock()


def _ensure_dirs() -> None:
    LOGS.mkdir(parents=True, exist_ok=True)


def load() -> dict:
    _ensure_dirs()
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save(data: dict) -> None:
    _ensure_dirs()
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(STATE_FILE)


def update(**kwargs) -> dict:
    """Atomically merge keys into the state file."""
    with _lock:
        data = load()
        data.update(kwargs)
        save(data)
        return data
