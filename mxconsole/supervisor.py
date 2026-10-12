"""The hypervisor: one `max serve` child per loaded model, started on demand, unloaded when idle."""
from __future__ import annotations

import asyncio
import contextlib
import ctypes
import fcntl
import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Callable

import httpx

from . import system
from .settings import env
from .store import Store, mxc_home

HOST = "127.0.0.1"
LOG_TAIL_BYTES = 2000
START_ATTEMPTS = 3  # fresh ports tried when ours was taken between picking it and binding it


class SupervisorError(Exception):
    pass


class UnknownModel(SupervisorError):
    def __init__(self, name: str):
        super().__init__(f"unknown model {name!r}: register it with `mxc pull`")
        self.name = name


class StartFailed(SupervisorError):
    pass


class DaemonAlreadyRunning(SupervisorError):
    pass


class _PortClash(Exception):
    pass


@dataclass
class _Child:
    proc: subprocess.Popen
    port: int
    last_used: float
    ready: bool = False
    crashed: bool = False
    in_flight: int = 0
    started_at: float = 0.0
    gpu: int | None = None


DEFAULT_GPU_MEMORY_FRACTION = "0.35"
MAX_GPU_MEMORY_FRACTION = 0.9
KV_HEADROOM_BYTES = 4 * 1024 ** 3  # beyond the weights: activations and a usable KV cache


def memory_fraction(weights_bytes: int | None, total_mb: int | None, default: str = DEFAULT_GPU_MEMORY_FRACTION) -> str:
    """The share of one GPU a model gets: the shared default for small models, or enough for its weights
    plus headroom when they are bigger than that (capped; past the cap MAX itself says it doesn't fit)."""
    if not weights_bytes or not total_mb:
        return default
    need = (weights_bytes * 1.1 + KV_HEADROOM_BYTES) / (total_mb * 1024 ** 2)
    if need <= float(default):
        return default
    return str(round(min(MAX_GPU_MEMORY_FRACTION, need), 2))


def weights_bytes(path: str | None) -> int | None:
    """Total size of a local snapshot's weight files (symlinks into the HF blobs are followed)."""
    if not path or not Path(path).is_dir():
        return None
    total = 0
    for f in Path(path).iterdir():
        if f.suffix in (".safetensors", ".bin", ".gguf"):
            try:
                total += f.stat().st_size
            except OSError:
                pass
    return total or None


def launch_flags() -> list[str]:
    """Flags every `max serve` mxconsole starts needs on this hardware (the port's parity server too)."""
    # Device graph capture crashes MAX at warm-up on this hardware (CUDA_ERROR_INVALID_DEVICE in
    # warmup_graph_capture, native Linux, RTX 3090) while serving works fine without it.
    # MXC_GRAPH_CAPTURE=1 lets MAX decide again where capture works.
    return [] if env("GRAPH_CAPTURE") == "1" else ["--no-device-graph-capture"]
MIN_FREE_GPU_MB = 4096


def _nvidia_gpus() -> list[dict]:
    from .metrics import gpus
    return gpus()


class Supervisor:
    def __init__(self, store: Store, *, max_command: tuple[str, ...] = ("max",),
                 health_timeout: float = 600, clock: Callable[[], float] = time.monotonic,
                 gpus: Callable[[], list[dict]] | None = None):
        self.store = store
        self.gpus = gpus if gpus is not None else _nvidia_gpus
        self.max_command = tuple(max_command)
        self.health_timeout = health_timeout
        self.clock = clock
        self._children: dict[str, _Child] = {}
        self._starts: dict[str, asyncio.Task] = {}
        self._lock_file = None

    # -- public API -------------------------------------------------------------------------

    async def ensure_running(self, name: str) -> str:
        model = self.store.get_model(name)
        if model is None:
            raise UnknownModel(name)
        child = self._children.get(name)
        if child is not None and child.ready and not _dead(child):
            child.last_used = self.clock()
            return self._url(child)
        task = self._starts.get(name)
        if task is None:
            task = asyncio.create_task(self._start(model))
            self._starts[name] = task
            task.add_done_callback(lambda t, n=name: self._starts.pop(n, None) if self._starts.get(n) is t else None)
            task.add_done_callback(lambda t: t.cancelled() or t.exception())  # mark retrieved
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():  # stop() cancelled the start, not this caller
                raise StartFailed(f"{name!r} was stopped while starting") from None
            raise

    def touch(self, name: str) -> None:
        child = self._children.get(name)
        if child is not None:
            child.last_used = self.clock()

    @contextlib.asynccontextmanager
    async def request(self, name: str) -> AsyncIterator[None]:
        """Mark a request in flight: the model is touched on entry and exit and never reaped meanwhile."""
        child = self._children.get(name)
        if child is None:
            yield
            return
        child.in_flight += 1
        child.last_used = self.clock()
        try:
            yield
        finally:
            child.in_flight -= 1
            child.last_used = self.clock()

    async def reap_idle(self) -> list[str]:
        """Stop every non-pinned, ready, idle model with nothing in flight and past its keep_alive."""
        now = self.clock()
        stopped = []
        for name, child in list(self._children.items()):
            model = self.store.get_model(name)
            if model is None or not child.ready or _dead(child) or child.in_flight:
                continue
            if not model.pinned and now - child.last_used > model.keep_alive:
                await self.stop(name)
                stopped.append(name)
        return stopped

    async def stop(self, name: str) -> None:
        task = self._starts.get(name)
        if task is not None:
            task.cancel()  # its handler kills the child
            await asyncio.gather(task, return_exceptions=True)
        child = self._children.pop(name, None)
        if child is not None:
            await asyncio.to_thread(self._terminate, name, child)

    async def stop_all(self) -> None:
        for name in set(self._children) | set(self._starts):
            await self.stop(name)

    def acquire_lock(self) -> None:
        """Take MXC_HOME/run/daemon.lock for the life of this process, or raise DaemonAlreadyRunning.

        Call before sweep_orphans: only the daemon holding the lock may kill leftover children."""
        if self._lock_file is not None:
            return
        f = open(self._run_dir() / "daemon.lock", "a+")
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            f.close()
            raise DaemonAlreadyRunning("another mxconsole daemon is already running for this MXC_HOME") from None
        f.seek(0)
        f.truncate()
        pid = os.getpid()  # "pid starttime", so `mxc ps` can tell this daemon from a reused pid without the lock
        f.write(f"{pid} {system.start_time(pid)}")
        f.flush()
        self._lock_file = f

    def sweep_orphans(self) -> list[int]:
        """Kill children recorded in MXC_HOME/run that a dead daemon left behind. Call at startup."""
        mine = {c.proc.pid for c in self._children.values()}
        killed = []
        for f in self._run_dir().glob("*.pid"):
            try:
                pid, pgid = (int(x) for x in f.read_text().split())
            except (OSError, ValueError):
                f.unlink(missing_ok=True)
                continue
            if pid in mine:
                continue
            if _is_max_serve(pid) and _pgid(pid) == pgid:  # never signal a reused pid
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(pgid, signal.SIGKILL)
                killed.append(pid)
            f.unlink(missing_ok=True)
        return killed

    def status(self) -> list[dict]:
        now = self.clock()
        names = {m.name for m in self.store.models()} | set(self._children)
        rows = []
        for name in sorted(names):
            model = self.store.get_model(name)
            child = self._children.get(name)
            row = {"name": name, "state": "cold", "port": None, "pid": None,
                   "idle_seconds": None, "starting_seconds": None,
                   "pinned": bool(model and model.pinned), "in_flight": 0}
            if child is not None:
                if _dead(child):
                    child.crashed = True
                row["state"] = "crashed" if child.crashed else ("ready" if child.ready else "starting")
                row["port"], row["pid"], row["in_flight"] = child.port, child.proc.pid, child.in_flight
                if child.ready and not child.crashed:
                    row["idle_seconds"] = now - child.last_used
                elif row["state"] == "starting":
                    row["starting_seconds"] = now - child.started_at
            rows.append(row)
        return rows

    # -- starting ---------------------------------------------------------------------------

    async def _start(self, model) -> str:
        name = model.name
        stale = self._children.pop(name, None)
        if stale is not None:  # crashed behind our back
            await asyncio.to_thread(self._terminate, name, stale)
        last: Exception | None = None
        for _ in range(START_ATTEMPTS):
            child = self._spawn(model)
            self._children[name] = child
            try:
                await self._wait_ready(model, child)
            except _PortClash as e:
                last = e
                self._children.pop(name, None)
                await asyncio.to_thread(self._terminate, name, child)
                continue
            except BaseException:  # failure, timeout or stop(): never leave the process behind
                self._children.pop(name, None)
                await asyncio.to_thread(self._terminate, name, child)
                raise
            child.ready = True
            child.last_used = self.clock()
            return self._url(child)
        raise StartFailed(f"could not find a free port for {name!r}: {last}")

    async def _wait_ready(self, model, child: _Child) -> None:
        name = model.name
        offset = self._log_path(name).stat().st_size
        deadline = time.monotonic() + self.health_timeout  # real time: this is a real wait
        base = f"http://{HOST}:{child.port}"
        async with httpx.AsyncClient(timeout=2.0) as http:
            while True:
                healthy = False
                try:
                    healthy = (await http.get(base + "/health")).status_code == 200
                except httpx.TransportError:
                    pass
                if healthy:
                    # Confirm it is *our* child answering, not something that grabbed the port.
                    try:
                        ids = [m.get("id") for m in (await http.get(base + "/v1/models")).json()["data"]]
                    except (httpx.TransportError, ValueError, KeyError, TypeError):
                        ids = []
                    if name in ids and not _dead(child):
                        return
                    if name not in ids:
                        raise _PortClash(f"something else answered on port {child.port}")
                if _dead(child):
                    tail = self._log_tail(name, offset)
                    if "in use" in tail.lower():
                        raise _PortClash(tail)
                    raise StartFailed(
                        f"max serve for {name!r} exited with code {_exit_code(child.proc.pid)}:\n{tail}")
                if time.monotonic() > deadline:
                    raise StartFailed(
                        f"max serve for {name!r} was not healthy after {self.health_timeout:g}s:\n"
                        f"{self._log_tail(name, offset)}")
                await asyncio.sleep(0.1)

    # -- processes --------------------------------------------------------------------------

    @staticmethod
    def _url(child: _Child) -> str:
        return f"http://{HOST}:{child.port}/v1"

    def _log_path(self, name: str) -> Path:
        logs = mxc_home() / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        return logs / f"{name}.log"

    def _run_dir(self) -> Path:
        run = mxc_home() / "run"
        run.mkdir(parents=True, exist_ok=True)
        return run

    def _pidfile(self, name: str, pid: int) -> Path:
        return self._run_dir() / f"{name}.{pid}.pid"

    def _pick_gpu(self) -> int | None:
        # MAX's default takes ~90% of free GPU memory for one model (a 0.5B model took 20 GB live), which
        # leaves no room for a second, so each model gets a capped share. Models started together all read
        # the same free memory before any has claimed it, so the GPU with the fewest of our models wins
        # first, and free memory breaks the tie.
        try:
            gpus = self.gpus()
        except Exception:
            return None
        if not gpus:
            return None
        free = lambda g: g["total_mb"] - g["used_mb"]
        # A GPU another program has nearly filled would OOM whatever our count says.
        roomy = [g for g in gpus if free(g) >= MIN_FREE_GPU_MB] or gpus
        ours = [c.gpu for c in self._children.values()]
        best = min(roomy, key=lambda g: (ours.count(g["index"]), -free(g)))
        return best["index"]

    def _gpu_total_mb(self, gpu: int | None) -> int | None:
        if gpu is None:
            return None
        try:
            return next((g["total_mb"] for g in self.gpus() if g["index"] == gpu), None)
        except Exception:
            return None

    def _command(self, model, port: int, gpu: int | None = None) -> list[str]:
        # A pulled model loads from its pinned local snapshot, so serving needs no network.
        cmd = [*self.max_command, "serve", "--port", str(port), "--served-model-name", model.name]
        cmd += launch_flags()
        if gpu is not None:
            cmd += ["--devices", f"gpu:{gpu}"]
        cmd += ["--device-memory-utilization",
                env("GPU_MEMORY_FRACTION") or memory_fraction(weights_bytes(model.weights_path), self._gpu_total_mb(gpu))]
        if model.custom_arch_path:  # a ported architecture MAX doesn't ship
            cmd += ["--custom-architectures", model.custom_arch_path]
        if model.weights_path and Path(model.weights_path).is_dir():
            return cmd + ["--model-path", model.weights_path]
        cmd += ["--model-path", model.repo_id]
        if model.revision:
            cmd += ["--huggingface-model-revision", model.revision]
        return cmd

    def _spawn(self, model) -> _Child:
        port = _free_port()
        gpu = self._pick_gpu()
        env = {**os.environ, "MAX_SERVE_HOST": HOST}
        log = open(self._log_path(model.name), "ab")
        try:
            proc = subprocess.Popen(
                self._command(model, port, gpu), stdout=log, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, start_new_session=True, env=env,
                preexec_fn=_die_with_parent if sys.platform.startswith("linux") else None,
            )
        except OSError as e:
            raise StartFailed(f"could not run {' '.join(self.max_command)}: {e}") from e
        finally:
            log.close()
        self._pidfile(model.name, proc.pid).write_text(f"{proc.pid} {proc.pid}")
        return _Child(proc=proc, port=port, last_used=self.clock(), started_at=self.clock(), gpu=gpu)

    def _log_tail(self, name: str, offset: int = 0) -> str:
        with open(self._log_path(name), "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(offset, f.tell() - LOG_TAIL_BYTES))
            return f.read().decode(errors="replace").strip()

    def _terminate(self, name: str, child: _Child, grace: float = 10.0) -> None:
        """SIGTERM the group, wait for the leader (without reaping it), SIGKILL the whole group, reap."""
        proc = child.proc
        if _exit_code(proc.pid) is None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGTERM)
            deadline = time.monotonic() + grace
            while time.monotonic() < deadline and _exit_code(proc.pid) is None:
                time.sleep(0.05)
        # The pgid is still held by the unreaped leader, so this cannot hit a reused pid. It also
        # catches grandchildren, including those of a leader that crashed.
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        self._pidfile(name, proc.pid).unlink(missing_ok=True)


def _dead(child: _Child) -> bool:
    return _exit_code(child.proc.pid) is not None


def _exit_code(pid: int) -> int | None:
    """Exit status once the process has exited, leaving it unreaped so its pgid stays reserved."""
    try:
        info = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    except ChildProcessError:
        return -1  # already reaped
    return None if info is None else info.si_status


def _die_with_parent() -> None:  # runs in the child between fork and exec
    try:
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
    except Exception:
        pass


def _is_max_serve(pid: int) -> bool:
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError:
        return False
    return b"serve" in cmdline


def _pgid(pid: int) -> int | None:
    try:
        return os.getpgid(pid)
    except ProcessLookupError:
        return None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]
