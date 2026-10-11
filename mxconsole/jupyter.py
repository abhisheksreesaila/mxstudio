"""Start Jupyter Lab on a generated notebook and say where it is.

One server (bound to 127.0.0.1, with a random token) serves the whole notebooks folder; a second
"open" reuses it while it is alive. The server is started detached, like `mxc learn` does, so it
outlives the daemon: stop it from Jupyter, or kill the process.
"""
from __future__ import annotations

import os
import secrets
import socket
import subprocess
import time
from pathlib import Path
from typing import Callable


class JupyterMissing(Exception):
    pass


class JupyterFailed(Exception):
    pass


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.2)
        return s.connect_ex(("127.0.0.1", port)) == 0


class JupyterLauncher:
    """Callable: `launcher(notebook_path) -> url`. The process and port parts are injectable for tests."""

    def __init__(self, popen: Callable = subprocess.Popen, free_port: Callable[[], int] = _free_port,
                 port_open: Callable[[int], bool] = _port_open, wait_seconds: float = 20.0):
        self.popen, self.free_port, self.port_open, self.wait_seconds = popen, free_port, port_open, wait_seconds
        self.proc = None
        self.port = 0
        self.token = ""
        self.root: Path | None = None

    def __call__(self, notebook: Path) -> str:
        alive = self.proc is not None and self.proc.poll() is None and self.root == notebook.parent
        if not alive:
            self._start(notebook.parent)
        return f"http://127.0.0.1:{self.port}/lab/tree/{notebook.name}?token={self.token}"

    def _start(self, root: Path) -> None:
        port, token = self.free_port(), secrets.token_urlsafe(24)
        cmd = ["jupyter", "lab", "--no-browser", "--ServerApp.ip=127.0.0.1", f"--ServerApp.port={port}",
               "--ServerApp.port_retries=0", f"--ServerApp.root_dir={root}"]
        try:
            proc = self.popen(cmd, start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, env={**os.environ, "JUPYTER_TOKEN": token})
        except FileNotFoundError:
            raise JupyterMissing("jupyter was not found. Install it with `pixi add jupyterlab` "
                                 "or open the notebook yourself: pixi run jupyter lab") from None
        deadline = time.monotonic() + self.wait_seconds
        while not self.port_open(port):
            if proc.poll() is not None or time.monotonic() > deadline:
                if proc.poll() is None:
                    proc.terminate()
                raise JupyterFailed("Jupyter Lab did not start; run `jupyter lab` in a terminal to see why")
            time.sleep(0.1)
        self.proc, self.port, self.token, self.root = proc, port, token, root
