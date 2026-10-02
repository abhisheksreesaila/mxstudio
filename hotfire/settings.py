"""Ports and addresses shared by the daemon, the CLI and the exposed endpoint."""
from __future__ import annotations

import os


def env(name: str, default: str | None = None) -> str | None:
    """Read HOTFIRE_<name>, falling back to the legacy MST_<name> (kept for one release)."""
    v = os.environ.get(f"HOTFIRE_{name}")
    if v is None:
        v = os.environ.get(f"MST_{name}")
    return default if v is None else v

DAEMON_HOST = "127.0.0.1"
DAEMON_PORT = int(env("PORT", "11435"))  # Ollama uses 11434
LITELLM_PORT = int(env("LITELLM_PORT", "4000"))
EXPOSE_PORT = int(env("EXPOSE_PORT", "8443"))


def daemon_url() -> str:
    return f"http://{DAEMON_HOST}:{int(env('PORT', str(DAEMON_PORT)))}"
