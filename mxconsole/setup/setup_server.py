"""The first-run setup page's server. Standard library only: it runs under `pixi exec python` before omxconsole
itself is downloaded. `install.sh` appends one line per step change to the setup log:

    <unix time> <step id> <running|done|failed|skipped> <message...>

and this serves `setup.html` plus `/state`, the log as JSON. Loopback only. It exits on its own a minute after the
setup has finished or failed, once the page has had time to see it.

Run: python setup_server.py <setup.log> <port>
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STEPS = [
    ("pixi", "Install pixi", "the package manager that keeps everything in one folder"),
    ("source", "Download omxconsole", "about 7 MB"),
    ("env", "Install MAX and Python", "the big one: a few GB, most of the wait"),
    ("app", "Install the window and tray", "Qt, about 1.5 GB"),
    ("login", "Start at login", "so the tray is there next time"),
    ("start", "Start omxconsole", "the server and the tray icon"),
]
LINGER_SECONDS = float(os.environ.get("MXC_SETUP_LINGER", "60"))
MAX_LIFETIME = 3 * 3600  # never outlive a setup that hung
PAGE = Path(__file__).with_name("setup.html")


def read_state(log_text: str, now: float) -> dict:
    """The setup log as the page sees it: each step's state, message and seconds spent, plus the overall state."""
    steps = {sid: {"id": sid, "label": label, "hint": hint, "state": "next", "message": "", "seconds": None}
             for sid, label, hint in STEPS}
    started: dict[str, float] = {}
    first = last = None
    extra: dict = {}
    for line in log_text.splitlines():
        parts = line.split(" ", 3)
        if len(parts) < 3:
            continue
        try:
            ts = float(parts[0])
        except ValueError:
            continue
        sid, state, message = parts[1], parts[2], parts[3] if len(parts) > 3 else ""
        first = ts if first is None else first
        last = ts
        if sid == "console":  # the URL to open when everything is up
            extra["console"] = message
            continue
        if sid not in steps or state not in ("running", "done", "failed", "skipped"):
            continue
        step = steps[sid]
        if state == "running":
            started[sid] = ts
        step["state"], step["message"] = state, message
        if state != "running" and sid in started:
            step["seconds"] = round(ts - started[sid])
        elif state == "running":
            step["seconds"] = round(now - ts)
    ordered = [steps[sid] for sid, _, _ in STEPS]
    if any(s["state"] == "failed" for s in ordered):
        overall = "failed"
    elif all(s["state"] in ("done", "skipped") for s in ordered):
        overall = "done"
    else:
        overall = "running"
    end = now if overall == "running" else last  # a finished setup took what it took
    return {"state": overall, "steps": ordered, "elapsed": round(end - first) if first is not None else 0, **extra}


def make_handler(log: Path, page: Path = PAGE):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes, kind: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                                                        "script-src 'self' 'unsafe-inline'")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path in ("/", "/setup"):
                self._send(200, page.read_bytes(), "text/html; charset=utf-8")
            elif self.path == "/state":
                try:
                    text = log.read_text(errors="replace")
                except OSError:
                    text = ""
                state = read_state(text, time.time())
                self._send(200, json.dumps(state).encode(), "application/json")
            else:
                self._send(404, b"not found", "text/plain")

        def log_message(self, *args) -> None:  # quiet
            pass

    return Handler


def finished(log: Path) -> bool:
    try:
        return read_state(log.read_text(errors="replace"), time.time())["state"] in ("done", "failed")
    except OSError:
        return False


def main(log: str, port: int) -> int:
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(Path(log)))
    # install.sh stops a lingering server from an earlier run with this (after checking it is one of ours).
    Path(log).with_name("setup-server.pid").write_text(str(os.getpid()))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    deadline = time.monotonic() + MAX_LIFETIME
    while not finished(Path(log)) and time.monotonic() < deadline:
        time.sleep(2)
    time.sleep(LINGER_SECONDS)  # long enough for the page to see the end and move on
    server.shutdown()
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: setup_server.py <setup.log> <port>")
    sys.exit(main(sys.argv[1], int(sys.argv[2])))
