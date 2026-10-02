"""Ports and addresses shared by the daemon, the CLI and the exposed endpoint."""
from __future__ import annotations

import os

DAEMON_HOST = "127.0.0.1"
DAEMON_PORT = int(os.environ.get("MST_PORT", "11435"))  # Ollama uses 11434
LITELLM_PORT = int(os.environ.get("MST_LITELLM_PORT", "4000"))
EXPOSE_PORT = int(os.environ.get("MST_EXPOSE_PORT", "8443"))


def daemon_url() -> str:
    return f"http://{DAEMON_HOST}:{int(os.environ.get('MST_PORT', DAEMON_PORT))}"
