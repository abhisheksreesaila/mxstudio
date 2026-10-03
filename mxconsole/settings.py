"""Ports and addresses shared by the daemon, the CLI and the exposed endpoint."""
from __future__ import annotations

import os


def env(name: str, default: str | None = None) -> str | None:
    """Read MXC_<name>, then HOTFIRE_<name>, then MST_<name> (the last two kept for compatibility)."""
    for prefix in ("MXC", "HOTFIRE", "MST"):
        v = os.environ.get(f"{prefix}_{name}")
        if v is not None:
            return v
    return default

DAEMON_HOST = "127.0.0.1"
DAEMON_PORT = int(env("PORT", "11435"))  # Ollama uses 11434
LITELLM_PORT = int(env("LITELLM_PORT", "4000"))
EXPOSE_PORT = int(env("EXPOSE_PORT", "8443"))


def daemon_url() -> str:
    return f"http://{DAEMON_HOST}:{int(env('PORT', str(DAEMON_PORT)))}"
