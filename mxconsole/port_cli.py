"""`mxc port <ref> --auto`: start an automatic port in the daemon and watch it.

  --json   one JSON object per change on stdout (event: progress), then a final one (event: done |
           failed | cancelled), each carrying the whole job (stages, log_tail), for agents.
  Ctrl-C   cancels the job (the agent and its commands are killed) and exits 130.
Exit codes: 0 ported and serving, 1 failed, 130 cancelled.
"""
from __future__ import annotations

import json
import sys
import time

import httpx

from mxconsole.port_auto import DEFAULT_ATTEMPTS, FINISHED
from mxconsole.settings import daemon_url

POLL = 1.0
ADMIN = {"X-Mst-Admin": "1"}
MARK = {"done": "ok", "running": "..", "failed": "FAILED", "cancelled": "cancelled", "skipped": "skipped", "pending": ""}


class PortFailed(Exception):
    pass


class DaemonSource:
    def _call(self, method, path, **kw):
        try:
            return httpx.request(method, f"{daemon_url()}/admin/ports{path}", headers=ADMIN, timeout=10.0, **kw)
        except httpx.HTTPError as e:
            raise PortFailed(f"could not reach the mxconsole daemon at {daemon_url()}: {e}")

    def start(self, ref, name, max_attempts, unsandboxed=False):
        body = {"ref": ref, "max_attempts": max_attempts, **({"name": name} if name else {}), **({"unsandboxed": True} if unsandboxed else {})}
        r = self._call("POST", "", json=body)
        data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code == 409 and "port" in data:
            return data["port"]
        if r.status_code != 202:
            raise PortFailed((data.get("error") or {}).get("message") or f"the daemon answered HTTP {r.status_code}")
        return data

    def get(self, port_id):
        r = self._call("GET", f"/{port_id}")
        if r.status_code == 200:
            return r.json()
        if r.status_code == 404:
            return None
        raise PortFailed(f"the daemon answered HTTP {r.status_code}")

    def cancel(self, port_id):
        self._call("POST", f"/{port_id}/cancel")


def stage_label(s: dict) -> str:
    return s["name"] + (f" (attempt {s['attempt']})" if s.get("attempt") else "")


def stage_line(s: dict) -> str:
    detail = f"  {s['detail']}" if s.get("detail") else ""
    return f"{stage_label(s):<22} {MARK.get(s['status'], s['status'])}{detail}"


class Display:
    def __init__(self, as_json: bool, out=None, err=None):
        self.as_json = as_json
        self.out, self.err = out or sys.stdout, err or sys.stderr
        self.seen: dict[tuple, str] = {}
        self.last_json = None

    def progress(self, job: dict) -> None:
        if self.as_json:
            key = json.dumps([job["status"], job["stage"], [(s["status"], s["detail"]) for s in job["stages"]], job["log_tail"]])
            if key != self.last_json:
                self.last_json = key
                print(json.dumps({"event": "progress", **job}), file=self.out, flush=True)
            return
        for s in job["stages"]:
            if s["status"] in ("pending", "skipped"):
                continue
            key = (s["name"], s.get("attempt"))
            line = stage_line(s)
            if self.seen.get(key) != line and (s["status"] != "running" or key not in self.seen):
                self.seen[key] = line
                print(line, file=self.err, flush=True)

    def finish(self, job: dict) -> None:
        if self.as_json:
            print(json.dumps({"event": job["status"], **job}), file=self.out, flush=True)


def run(ref: str, name: str | None, as_json: bool, max_attempts: int = DEFAULT_ATTEMPTS, *, unsandboxed: bool = False, source=None,
        display: Display | None = None, sleep=time.sleep) -> tuple[int, str]:
    """Start the port and watch it. Returns (exit code, the line to print; empty with --json)."""
    from mxconsole import serve
    display = display or Display(as_json)
    try:
        if source is None:
            if serve.ensure_daemon() and not as_json:
                print("started the mxconsole daemon", file=sys.stderr)
            source = DaemonSource()
        job = source.start(ref, name, max_attempts, unsandboxed)
    except (PortFailed, serve.DaemonError) as e:
        return _failed(as_json, str(e))
    port_id = job["id"]
    if not as_json:
        print(f"port {port_id}: {job['ref']} (log {job['log']})", file=sys.stderr, flush=True)
    try:
        while True:
            job = source.get(port_id) or job
            display.progress(job)
            if job["status"] in FINISHED:
                break
            sleep(POLL)
    except KeyboardInterrupt:
        try:
            source.cancel(port_id)
            for _ in range(10):
                job = source.get(port_id) or job
                if job["status"] in FINISHED:
                    break
                sleep(0.5)
        except (PortFailed, KeyboardInterrupt):
            pass
        job = {**job, "status": "cancelled", "message": "Cancelled."} if job["status"] not in FINISHED else job
        display.finish(job)
        return 130, "" if as_json else "cancelled"
    except PortFailed:
        msg = "the daemon stopped during this port; run the same command again"
        return _failed(as_json, msg)
    display.progress(job)
    display.finish(job)
    if job["status"] == "done":
        return 0, "" if as_json else f"{job['message']}\nTry it: mxc ps   (log {job['log']})"
    if job["status"] == "cancelled":
        return 130, "" if as_json else "cancelled"
    tail = "\n".join(job["log_tail"].splitlines()[-8:])
    return 1, "" if as_json else f"error: {job['message']}" + (f"\n--- last of the log ---\n{tail}" if tail else "")


def _failed(as_json: bool, message: str) -> tuple[int, str]:
    if as_json:
        print(json.dumps({"event": "failed", "status": "failed", "message": message}), flush=True)
        return 1, ""
    return 1, f"error: {message}"
