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
