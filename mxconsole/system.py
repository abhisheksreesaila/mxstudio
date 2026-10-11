"""What is running and where: the crew (daemon, Caddy, LiteLLM, each `max serve`), the listeners, the logs.

One data source for `mxc ps` and the console's System pages. Everything is read from pid files and /proc
(a `proc_root` argument lets tests hand in a fake one), so it works with the daemon down. GPU memory is
not readable from /proc: the daemon's /admin/system adds it from nvidia-smi (gpu_summary, add_vram)."""
from __future__ import annotations

import os
import shlex
import threading
import time
import urllib.request
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
        self._lock = threading.Lock()  # bump() runs on request threads as well as the event loop
        self.since = time.time()

    def bump(self, name: str) -> None:
        with self._lock:
            self._roll()
            self._n[name] = self._n.get(name, 0) + 1

    def get(self, name: str) -> int:
        with self._lock:
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
def start_time(pid: int, proc_root: str | Path = "/proc") -> str | None:
    """Kernel start time of a live (non-zombie) process; tells a process from a reused pid."""
    try:
        fields = (Path(proc_root) / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
        return None if fields[0] == "Z" else fields[19]
    except (OSError, IndexError):
        return None


def _is_daemon_cmd(cmd: list[str]) -> bool:
    return any(cmd[i:i + 3] == ["-m", "mxconsole", "daemon"] for i in range(len(cmd))) or (
        len(cmd) >= 2 and os.path.basename(cmd[0]) == "mxc" and cmd[1] == "daemon") or (
        len(cmd) >= 3 and os.path.basename(cmd[1]) == "mxc" and cmd[2] == "daemon")


def daemon_pid(proc_root: str | Path = "/proc") -> int | None:
    """The daemon's pid from run/daemon.lock ("pid starttime"), only while that process is really it.
    Never takes the lock: a daemon that is starting up must not fail because `mxc ps` looked."""
    try:
        parts = (mxc_home() / "run" / "daemon.lock").read_text().split()
        pid = int(parts[0])
    except (OSError, ValueError, IndexError):
        return None
    if len(parts) > 1:
        return pid if start_time(pid, proc_root) == parts[1] else None
    # Written by a daemon older than start times: the command line has to say it is a daemon.
    return pid if proc_stats(pid, proc_root) and _is_daemon_cmd(_cmdline(pid, proc_root)) else None


def daemon_answers() -> bool:
    """Does something answer /health on the daemon's port? Used when the lock file has no pid."""
    try:
        with urllib.request.urlopen(f"http://{DAEMON_HOST}:{daemon_port()}/health", timeout=0.5) as r:
            return r.status == 200
    except (OSError, ValueError):
        return False


def _is_max_serve(cmd: list[str]) -> bool:
    """The configured max command followed by `serve`, as the supervisor spawns it (also when an
    interpreter comes first, as in `python /env/bin/max serve`)."""
    want = shlex.split(env("MAX_CMD", "max")) + ["serve"]
    for i in range(len(cmd) - len(want) + 1):
        head = cmd[i:i + len(want)]
        if os.path.basename(head[0]) == os.path.basename(want[0]) and head[1:] == want[1:]:
            return True
    return False


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
        if _is_max_serve(cmd) and proc_stats(pid, proc_root) is not None:
            out.append({"name": name, "pid": pid, "port": _port_from(cmd), "state": "running"})
    return out


# -- GPU memory ----------------------------------------------------------------------------------
def parent_pid(pid: int, proc_root: str | Path = "/proc") -> int | None:
    try:
        return int((Path(proc_root) / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()[1])
    except (OSError, IndexError, ValueError):
        return None


def gpu_summary(gpus: list[dict]) -> tuple[dict | None, list[dict]]:
    """(one summary over all GPUs or None, per-GPU rows) in GB, from metrics.gpus()."""
    rows = [{"index": g["index"], "name": g["name"], "vram_total_gb": round(g["total_mb"] / 1024, 1),
             "vram_used_gb": round(g["used_mb"] / 1024, 1)} for g in gpus]
    if not rows:
        return None, []
    distinct = list(dict.fromkeys(r["name"] for r in rows))
    name = ", ".join(distinct) if len(distinct) > 1 else distinct[0] + (f" x{len(rows)}" if len(rows) > 1 else "")
    return ({"name": name, "count": len(rows), "vram_total_gb": round(sum(r["vram_total_gb"] for r in rows), 1),
             "vram_used_gb": round(sum(r["vram_used_gb"] for r in rows), 1)}, rows)


def add_vram(crew: list[dict], procs: dict[int, int], ppid=parent_pid) -> None:
    """Set `vram_gb` on each model in the crew: the GPU memory of its process and of the processes below it
    (MAX may compute in a child). None when the driver does not list the model, so the console shows a dash."""
    for c in crew:
        if c["kind"] != "model":
            continue
        total, found = 0, False
        for pid, mb in procs.items():
            node, hops = pid, 0
            while node and hops < 16:  # walk up the parents; a model's workers sit a few levels below it
                if node == c["pid"]:
                    total, found = total + mb, True
                    break
                node, hops = ppid(node), hops + 1
        c["vram_gb"] = round(total / 1024, 2) if found else None


# -- the crew ------------------------------------------------------------------------------------
def collect(rows: list[dict] | None = None, *, proc_root: str | Path = "/proc", daemon: int | None = None,
            expose_pids: dict[str, int | None] | None = None, health=None) -> list[dict]:
    """Every background process mxconsole owns. `rows` is the supervisor's status (inside the daemon);
    without it the models come from pid files and /proc, so `mxc ps` works with the daemon down.
    `health()` says whether a daemon with no readable pid (started before pids were recorded) answers."""
    daemon = daemon if daemon is not None else daemon_pid(proc_root)
    pids = expose_pids if expose_pids is not None else {n: expose._running_pid(n) for n in expose.PROCESSES}
    ports = {"daemon": daemon_port(), "caddy": EXPOSE_PORT, "litellm": LITELLM_PORT}
    crew = []
    for kind, label, pid in (("daemon", "mxconsole daemon", daemon), ("caddy", "Caddy", pids.get("caddy")),
                             ("litellm", "LiteLLM", pids.get("litellm"))):
        stats = proc_stats(pid, proc_root) if pid else None
        unknown = kind == "daemon" and not stats and health is not None and health()
        crew.append({"id": kind, "kind": kind, "name": label, "state": "running" if stats or unknown else "stopped",
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
        {"name": "mxconsole daemon", "kind": "daemon", "host": DAEMON_HOST, "port": by["daemon"]["port"],
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
