"""First-run setup preferences stored separately from secrets and runtime state."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from notebooks.mxstudio import state

SETUP_VERSION = 1
SETUP_FILE = state.HOME / "setup.json"
_lock = threading.Lock()


def load() -> dict:
    try:
        data = json.loads(SETUP_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, OSError, ValueError):
        return {}


def acknowledge(environment_kind: str, environment_path: str) -> dict:
    """Remember the environment the user approved without storing live process state."""
    data = {
        "setup_version": SETUP_VERSION,
        "environment_confirmed": True,
        "environment_kind": environment_kind,
        "environment_path": str(Path(environment_path).expanduser().resolve()),
        "confirmed_at": datetime.now(timezone.utc).isoformat(),
    }
    with _lock:
        state.HOME.mkdir(parents=True, exist_ok=True)
        temporary = SETUP_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temporary.replace(SETUP_FILE)
    return data


def is_confirmed(environment_path: str) -> bool:
    data = load()
    return bool(
        data.get("environment_confirmed")
        and data.get("setup_version") == SETUP_VERSION
        and data.get("environment_path")
        == str(Path(environment_path).expanduser().resolve())
    )