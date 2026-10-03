"""What is running and where: the crew (daemon, Caddy, LiteLLM, each `max serve`), the listeners, the logs.

One data source for `mxc ps` and the console's System pages. Everything is read from pid files and /proc
(a `proc_root` argument lets tests hand in a fake one), so it works with the daemon down. GPU memory is
not readable from /proc, so it is never reported."""
from __future__ import annotations

import fcntl
import os
import time
from datetime import date
from pathlib import Path

from mxconsole import expose
from mxconsole.settings import DAEMON_HOST, DAEMON_PORT, EXPOSE_PORT, LITELLM_PORT, env
from mxconsole.store import mxc_home

CLK_TCK = os.sysconf("SC_CLK_TCK")
DEFAULT_LOG_BYTES = 16000
MAX_LOG_BYTES = 65536
CADDY_ADMIN_PORT = 2099  # fixed in the Caddyfile expose.py renders


def daemon_port() -> int:
    return int(env("PORT", str(DAEMON_PORT)))


class Counters:
    """Things only the daemon sees, counted per local day, in memory (they restart with the daemon)."""

    def __init__(self, today=date.today):
        self._today, self._day, self._n = today, None, {}
        self.since = time.time()

    def bump(self, name: str) -> None:
        self._roll()
        self._n[name] = self._n.get(name, 0) + 1

    def get(self, name: str) -> int:
        self._roll()
        return self._n.get(name, 0)

    def _roll(self) -> None:
        day = self._today()
        if day != self._day:
            self._day, self._n = day, {}


# -- /proc ---------------------------------------------------------------------------------------
def proc_stats(pid: int, proc_root: str | Path = "/proc") -> dict | None:
    """RSS and uptime of a live (non-zombie) process, or None when it is gone."""
    root = Path(proc_root)
    try:
        stat = (root / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
        if stat[0] == "Z":
            return None
        start_ticks = int(stat[19])
    except (OSError, IndexError, ValueError):
        return None
    rss = uptime = None
    try:
        for line in (root / str(pid) / "status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                rss = round(int(line.split()[1]) / 1024, 1)  # kB to MB
    except (OSError, ValueError, IndexError):
        pass
    try:
        uptime = max(0.0, float((root / "uptime").read_text().split()[0]) - start_ticks / CLK_TCK)
    except (OSError, ValueError, IndexError):
        pass
    return {"rss_mb": rss, "uptime_seconds": None if uptime is None else round(uptime)}


def _cmdline(pid: int, proc_root: str | Path) -> list[str]:
    try:
        return (Path(proc_root) / str(pid) / "cmdline").read_bytes().decode(errors="replace").split("\0")
    except OSError:
        return []


def _port_from(cmd: list[str]) -> int | None:
    for i, part in enumerate(cmd[:-1]):
        if part == "--port" and cmd[i + 1].isdigit():
            return int(cmd[i + 1])
    return None


# -- pid files -----------------------------------------------------------------------------------
def daemon_pid() -> int | None:
    """The pid the daemon wrote into run/daemon.lock, only while a daemon really holds that lock."""
    path = mxc_home() / "run" / "daemon.lock"
    try:
        pid = int(path.read_text().strip())
        with open(path, "a+") as probe:
            try:
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return pid  # held: the daemon is alive
            fcntl.flock(probe, fcntl.LOCK_UN)
    except (OSError, ValueError):
        pass
    return None


def _model_children(proc_root) -> list[dict]:
    """`max serve` children recorded in run/<name>.<pid>.pid that are still alive."""
    out = []
    for f in sorted((mxc_home() / "run").glob("*.pid")):
        name, _, pid_text = f.name[:-4].rpartition(".")
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        cmd = _cmdline(pid, proc_root)
        if "serve" in cmd and proc_stats(pid, proc_root) is not None:
            out.append({"name": name, "pid": pid, "port": _port_from(cmd), "state": "running"})
    return out


# -- the crew ------------------------------------------------------------------------------------
def collect(rows: list[dict] | None = None, *, proc_root: str | Path = "/proc", daemon: int | None = None,
            expose_pids: dict[str, int | None] | None = None) -> list[dict]:
    """Every background process MX Console owns. `rows` is the supervisor's status (inside the daemon);
    without it the models come from pid files and /proc, so `mxc ps` works with the daemon down."""
    daemon = daemon if daemon is not None else daemon_pid()
    pids = expose_pids if expose_pids is not None else {n: expose._running_pid(n) for n in expose.PROCESSES}
    ports = {"daemon": daemon_port(), "caddy": EXPOSE_PORT, "litellm": LITELLM_PORT}
    crew = []
    for kind, label, pid in (("daemon", "MX Console daemon", daemon), ("caddy", "Caddy", pids.get("caddy")),
                             ("litellm", "LiteLLM", pids.get("litellm"))):
        stats = proc_stats(pid, proc_root) if pid else None
        crew.append({"id": kind, "kind": kind, "name": label, "state": "running" if stats else "stopped",
                     "pid": pid if stats else None, "port": ports[kind],
                     "rss_mb": stats and stats["rss_mb"], "uptime_seconds": stats and stats["uptime_seconds"]})
    if rows is None:
        children = _model_children(proc_root)
    else:
        children = [{"name": r["name"], "pid": r["pid"], "port": r["port"], "state": r["state"]}
                    for r in rows if r.get("pid")]
    for c in children:
        stats = proc_stats(c["pid"], proc_root)
        crew.append({"id": f"model:{c['name']}", "kind": "model", "name": c["name"], "state": c["state"],
                     "pid": c["pid"], "port": c["port"], "rss_mb": stats and stats["rss_mb"],
                     "uptime_seconds": stats and stats["uptime_seconds"]})
    return crew


def listeners(crew: list[dict]) -> list[dict]:
    """Every listening port, with what talks to it. Stopped parts are listed as 'off'."""
    by = {c["id"]: c for c in crew}

    def state(i: str) -> str:
        return "listening" if by[i]["state"] == "running" else "off"

    rows = [
        {"name": "MX Console daemon", "kind": "daemon", "host": DAEMON_HOST, "port": by["daemon"]["port"],
         "exposed": False, "state": state("daemon"),
         "talks_to": "the mxc CLI, this console, your apps on this machine, LiteLLM and Caddy's key check"},
        {"name": "LiteLLM", "kind": "litellm", "host": "127.0.0.1", "port": by["litellm"]["port"], "exposed": False,
         "state": state("litellm"), "talks_to": "Caddy only (its UI opens from this machine)"},
        {"name": "Caddy", "kind": "caddy", "host": "0.0.0.0", "port": by["caddy"]["port"], "exposed": True,
         "state": state("caddy"), "talks_to": "LAN clients over HTTPS, each with an app key"},
        {"name": "Caddy admin", "kind": "caddy", "host": "127.0.0.1", "port": CADDY_ADMIN_PORT, "exposed": False,
         "state": state("caddy"), "talks_to": "Caddy itself (its config API)"},
    ]
    for c in crew:
        if c["kind"] == "model":
            up = c["state"] in ("ready", "running")
            rows.append({"name": f"max serve · {c['name']}", "kind": "model", "host": "127.0.0.1", "port": c["port"],
                         "exposed": False, "state": "listening" if up else c["state"], "talks_to": "the daemon only"})
    return rows


# -- logs ----------------------------------------------------------------------------------------
def log_path(source: str) -> Path:
    """daemon.log lives with the models' logs; Caddy's and LiteLLM's under expose/."""
    if source == "daemon":
        return mxc_home() / "logs" / "daemon.log"
    return expose.expose_dir() / f"{source}.log"


def read_tail(path: Path, limit: int = DEFAULT_LOG_BYTES) -> str:
    """The end of a log, at most `limit` bytes (never more than MAX_LOG_BYTES); '' when there is no file."""
    limit = max(1, min(MAX_LOG_BYTES, limit))
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - limit))
            return f.read().decode(errors="replace")
    except FileNotFoundError:
        return ""


def log_sources(model_names: list[str]) -> list[dict]:
    """Logs the viewer can pick: the daemon's, Caddy's and LiteLLM's when the files exist, every model's."""
    labels = {"daemon": "Daemon", "caddy": "Caddy", "litellm": "LiteLLM"}
    out = [{"id": s, "label": labels[s]} for s in labels if log_path(s).exists()]
    return out + [{"id": f"model:{n}", "label": n} for n in model_names]


# -- text for `mxc ps` ---------------------------------------------------------------------------
def _uptime(seconds) -> str:
    if seconds is None:
        return "-"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m" if h else f"{m}m {s}s" if m else f"{s}s"


def ps_text(crew: list[dict]) -> str:
    lines = [f"{'NAME':<30} {'STATE':<9} {'PID':>7} {'PORT':>6} {'MEMORY':>9} {'UPTIME':>8}"]
    for c in crew:
        name = f"max serve · {c['name']}" if c["kind"] == "model" else c["name"]
        mem = "-" if c["rss_mb"] is None else f"{c['rss_mb']:.0f} MB"
        lines.append(f"{name:<30} {c['state']:<9} {c['pid'] or '-':>7} {c['port'] or '-':>6} {mem:>9} "
                     f"{_uptime(c['uptime_seconds']):>8}")
    return "\n".join(lines)
