"""`mxc pull <ref>` with a live progress bar.

The pull always runs in the daemon (started here if it is not up, like `mxc bench`), so the console
shows it too and only one process ever writes the download. `--dry-run` stays local: it downloads nothing.

  --json   one JSON object per progress event on stdout, then a final one (event: done | failed |
           unsupported | cancelled), for agents.
  Ctrl-C   cancels the pull (partial files stay, a retry resumes) and exits 130; a second Ctrl-C
           stops waiting and exits 130 at once.
Exit codes: 0 pulled, 1 refused, failed or interrupted (the daemon stopped), 130 cancelled.
"""
from __future__ import annotations

import json
import sys
import time

import httpx

from mxconsole import resolve
from mxconsole.pulls import FINISHED
from mxconsole.settings import daemon_url
from mxconsole.store import Store

INTERRUPTED = "the pull was interrupted (the daemon stopped); run the same command again to resume"
CANCELLED = "cancelled. Partial files are kept: run the same command to resume."
POLL = 0.25         # seconds between looks at the job
BAR = 24            # width of the progress bar in characters
ADMIN = {"X-Mst-Admin": "1"}


class PullFailed(Exception):
    """Could not even start the pull (a bad ref, an unreachable daemon): one line for the person."""


class DaemonSource:
    """Asks the running daemon to pull, and watches its job."""

    def _call(self, method, path, **kw):
        try:
            return httpx.request(method, f"{daemon_url()}/admin/pulls{path}", headers=ADMIN, timeout=10.0, **kw)
        except httpx.HTTPError as e:
            raise PullFailed(f"could not reach the MX Console daemon at {daemon_url()}: {e}")

    def start(self, ref, name):
        r = self._call("POST", "", json={"ref": ref, **({"name": name} if name else {})})
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code == 409 and "pull" in body:
            return body["pull"]  # already running: watch that one
        if r.status_code != 202:
            raise PullFailed((body.get("error") or {}).get("message") or f"the daemon answered HTTP {r.status_code}")
        return body

    def get(self, pull_id):
        r = self._call("GET", f"/{pull_id}")
        if r.status_code == 200:
            return r.json()
        if r.status_code == 404:
            return None  # not known (yet): the caller keeps the last state it saw
        raise PullFailed(f"the daemon answered HTTP {r.status_code}")

    def cancel(self, pull_id):
        r = self._call("POST", f"/{pull_id}/cancel")
        if r.status_code >= 400 and r.status_code != 404:
            raise PullFailed(f"the daemon answered HTTP {r.status_code}")


def human_bytes(n) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def render(job: dict) -> str:
    """One line: step, bar, percent, bytes, speed, eta."""
    step = next((s for s in job["steps"] if s["state"] in ("now", "failed", "cancelled")), job["steps"][-1])
    pct = job.get("percent")
    if job["step"] != "download" and job["status"] == "running" or pct is None:
        return f"{job['ref']}  {step['label'].lower()}…  {step['detail']}".rstrip()
    filled = int(BAR * pct / 100)
    bar = "█" * filled + "░" * (BAR - filled)
    line = f"{job['ref']}  {step['label'].lower()}  {bar} {pct:5.1f}%  {human_bytes(job['done_bytes'])} of {human_bytes(job['total_bytes'])}"
    if job.get("speed"):
        line += f"  {human_bytes(job['speed'])}/s"
    if job.get("eta_seconds") is not None:
        line += f"  eta {job['eta_seconds']}s"
    return line


class Display:
    def __init__(self, as_json: bool, out=None, err=None):
        self.as_json = as_json
        self.out, self.err = out or sys.stdout, err or sys.stderr
        self.tty = hasattr(self.err, "isatty") and self.err.isatty()
        self.last = None
        self.last_decile = -1
        self.drawn = False

    def progress(self, job: dict) -> None:
        key = (job["step"], job["status"], job["done_bytes"])
        if key == self.last:
            return
        step_changed = self.last is None or key[0] != self.last[0]
        self.last = key
        if self.as_json:
            print(json.dumps({"event": "progress", **job}), file=self.out, flush=True)
        elif self.tty:
            print("\r\x1b[2K" + render(job), end="", file=self.err, flush=True)
            self.drawn = True
        else:  # a log or a pipe: a line per step and every 10 percent, not a thousand carriage returns
            decile = int((job["percent"] or 0) // 10)
            if step_changed or decile > self.last_decile:
                self.last_decile = max(self.last_decile, decile)
                print(render(job), file=self.err, flush=True)

    def finish(self, job: dict) -> None:
        if self.drawn:
            print("\r\x1b[2K", end="", file=self.err, flush=True)
            self.drawn = False
        if self.as_json:
            print(json.dumps({"event": job["status"], **job}), file=self.out, flush=True)


def run(ref: str, name: str | None, as_json: bool, *, source=None, display: Display | None = None, sleep=time.sleep) -> tuple[int, str]:
    """Pull and watch. Returns (exit code, the line to print for the person; empty with --json)."""
    from mxconsole import serve
    display = display or Display(as_json)
    try:
        if source is None:
            if serve.ensure_daemon() and not as_json:
                print("started the MX Console daemon", file=sys.stderr)
            source = DaemonSource()
        job = source.start(ref, name)
    except (PullFailed, serve.DaemonError) as e:
        return _failed(as_json, str(e))
    except KeyboardInterrupt:  # during the start POST: the pull may or may not have begun; say how to find out
        return _cancelled(display, as_json, {"ref": ref, "status": "cancelled", "step": "resolve", "done_bytes": 0, "steps": [],
                                              "percent": None, "message": "Cancelled."})
    pull_id = job["id"]
    try:
        while True:
            job = source.get(pull_id) or job
            display.progress(job)
            if job["status"] in FINISHED:
                break
            sleep(POLL)
    except KeyboardInterrupt:
        return _cancel(source, pull_id, job, display, as_json, sleep)
    except PullFailed:
        display.finish({**job, "status": "failed", "message": INTERRUPTED})
        return _failed(as_json, INTERRUPTED, event=False)
    display.finish(job)
    if job["status"] == "done":
        if as_json:
            return 0, ""
        model = Store.open().get_model(job["model"])
        return 0, f"pulled {model.name} ({model.repo_id}@{model.revision[:8]}, {model.source})" if model else job["message"]
    if job["status"] == "cancelled":
        return 130, "" if as_json else CANCELLED
    if job.get("error_kind") == "interrupted":
        return 1, "" if as_json else INTERRUPTED
    return 1, "" if as_json else f"error: {job['message']}"


def _cancel(source, pull_id, job, display, as_json, sleep) -> tuple[int, str]:
    """Ctrl-C while watching: ask the daemon to stop, wait briefly so the last state is true."""
    try:
        source.cancel(pull_id)
        for _ in range(int(5 / POLL)):
            job = source.get(pull_id) or job
            if job["status"] in FINISHED:
                break
            sleep(POLL)
    except PullFailed:  # the daemon went away while we were cancelling
        display.finish({**job, "status": "failed", "message": INTERRUPTED})
        return _failed(as_json, INTERRUPTED, event=False)
    except KeyboardInterrupt:  # a second Ctrl-C: stop waiting
        pass
    if job["status"] not in FINISHED:
        job = {**job, "status": "cancelled", "message": "Cancelled."}
    return _cancelled(display, as_json, job)


def _cancelled(display, as_json, job) -> tuple[int, str]:
    display.finish(job)
    return 130, "" if as_json else CANCELLED


def _failed(as_json: bool, message: str, event: bool = True) -> tuple[int, str]:
    """Exit 1 with one line. `event=False` when display.finish already printed the final --json event."""
    if as_json:
        if event:
            print(json.dumps({"event": "failed", "status": "failed", "message": message}), flush=True)
        return 1, ""
    return 1, message if message == INTERRUPTED else f"error: {message}"
