"""Start Jupyter Lab on a generated notebook and say where it is.

One server (bound to 127.0.0.1, with a random token) serves the whole notebooks folder; a second
"open" reuses it while it is alive. The server is started detached, like `mxc learn` does, so it
outlives the daemon: stop it from Jupyter, or kill the process.
"""
from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from mxconsole.store import mxc_home


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


def _is_jupyter(pid: int) -> bool:
    try:
        return b"jupyter" in Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return False


class JupyterLauncher:
    """Callable: `launcher(notebook_path) -> url`. The process and port parts are injectable for tests."""

    def __init__(self, popen: Callable = subprocess.Popen, free_port: Callable[[], int] = _free_port,
                 port_open: Callable[[int], bool] = _port_open, wait_seconds: float = 20.0,
                 is_jupyter: Callable[[int], bool] = _is_jupyter):
        self.popen, self.free_port, self.port_open, self.wait_seconds = popen, free_port, port_open, wait_seconds
        self.is_jupyter = is_jupyter
        self.lock = threading.Lock()  # two concurrent opens must start one server, not two
        self.proc = None
        self.port = 0
        self.token = ""
        self.root: Path | None = None

    @staticmethod
    def record_path() -> Path:
        return mxc_home() / "run" / "jupyter.json"

    def __call__(self, notebook: Path) -> str:
        with self.lock:
            alive = self.proc is not None and self.proc.poll() is None and self.root == notebook.parent
            if not alive and not self._adopt(notebook.parent):
                self._start(notebook.parent)
        return f"http://127.0.0.1:{self.port}/lab/tree/{notebook.name}?token={self.token}"

    def _adopt(self, root: Path) -> bool:
        """Reuse the server a previous daemon recorded, if it is still that Jupyter and still answers."""
        try:
            rec = json.loads(self.record_path().read_text())
            pid, port, token = int(rec["pid"]), int(rec["port"]), str(rec["token"])
        except (OSError, ValueError, KeyError, TypeError):
            return False
        if rec.get("root") != str(root) or not self.is_jupyter(pid) or not self.port_open(port):
            return False
        self.proc, self.port, self.token, self.root = _Adopted(pid), port, token, root
        return True

    def _record(self, pid: int) -> None:
        path = self.record_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"pid": pid, "port": self.port, "token": self.token, "root": str(self.root)}, f)

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
        self._record(proc.pid)


class _Adopted:
    """A server started by an earlier daemon: not our child, so liveness is the pid's."""

    def __init__(self, pid: int):
        self.pid = pid

    def poll(self):
        try:
            os.kill(self.pid, 0)
        except OSError:
            return 1
        return None
