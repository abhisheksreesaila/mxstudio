"""Formatting and data helpers for the read commands (`mst status`, `metrics`, `requests`, `--json`)."""
from __future__ import annotations

import json
import time

from mst.settings import daemon_url
from mst.store import Model, format_duration

PAGES = ("overview", "models", "activity", "metrics", "keys", "network")


def console_link(page: str | None = None) -> str:
    return f"{daemon_url()}/console" + (f"#{page}" if page else "")


def lifecycle(m: Model) -> str:
    return "pinned (never unloads)" if m.pinned else f"keep-alive {format_duration(m.keep_alive)}"


def dump(data) -> str:
    return json.dumps(data, indent=2)


def when(t: float | None) -> str:
    return "never" if t is None else time.strftime("%Y-%m-%d %H:%M", time.localtime(t))


def models_data(models: list[Model], states: dict[str, str]) -> list[dict]:
    """Registered models; `state` is the daemon live state, or null when the daemon is not running."""
    return [{"name": m.name, "repo_id": m.repo_id, "revision": m.revision, "source": m.source,
             "architecture": m.architecture, "pinned": m.pinned, "keep_alive": m.keep_alive,
             "state": states.get(m.name) if states else None} for m in models]


def keys_data(rows) -> list[dict]:
    return [{"app": k.app, "prefix": k.prefix, "created_at": k.created_at, "last_used_at": k.last_used_at,
             "revoked_at": k.revoked_at, "status": "active" if k.revoked_at is None else "revoked"} for k in rows]
