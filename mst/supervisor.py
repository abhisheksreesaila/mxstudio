"""The hypervisor: one `max serve` child per loaded model, started on demand, unloaded when idle."""
from __future__ import annotations

import asyncio
import os
import signal
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

from .store import Store, mst_home

HOST = "127.0.0.1"
LOG_TAIL_BYTES = 2000


class SupervisorError(Exception):
    pass


class UnknownModel(SupervisorError):
    def __init__(self, name: str):
        super().__init__(f"unknown model {name!r}: register it with `mst pull`")
        self.name = name


class StartFailed(SupervisorError):
    pass


@dataclass
class _Child:
    proc: subprocess.Popen
    port: int
    last_used: float
    ready: bool = False
    crashed: bool = False


class Supervisor:
    def __init__(self, store: Store, *, max_command: tuple[str, ...] = ("max",),
                 health_timeout: float = 600, clock: Callable[[], float] = time.monotonic):
        self.store = store
        self.max_command = tuple(max_command)
        self.health_timeout = health_timeout
        self.clock = clock
        self._children = {}
        self._locks = {}

    # -- public API -------------------------------------------------------------------------

    async def ensure_running(self, name: str) -> str:
        model = self.store.get_model(name)
        if model is None:
            raise UnknownModel(name)
        async with self._locks.setdefault(name, asyncio.Lock()):
            child = self._children.get(name)
            if child is not None and child.proc.poll() is None and child.ready:
                child.last_used = self.clock()
                return self._url(child)
            if child is not None:  # exited behind our back, or a stale entry
                self._reap_process(child)
                self._children.pop(name, None)
            child = self._spawn(model)
            self._children[name] = child
            try:
                await self._wait_healthy(name, child)
            except BaseException:
                self._children.pop(name, None)
                await asyncio.to_thread(self._terminate, child)
                raise
            child.ready = True
            child.last_used = self.clock()
            return self._url(child)

    def touch(self, name: str) -> None:
        child = self._children.get(name)
        if child is not None:
            child.last_used = self.clock()

    async def reap_idle(self) -> list[str]:
        """Stop every non-pinned, running model idle longer than its keep_alive."""
        now = self.clock()
        stopped = []
        for name, child in list(self._children.items()):
            model = self.store.get_model(name)
            if model is None or not child.ready or child.proc.poll() is not None:
                continue
            if not model.pinned and now - child.last_used > model.keep_alive:
                await self.stop(name)
                stopped.append(name)
        return stopped

    async def stop(self, name: str) -> None:
        child = self._children.pop(name, None)
        if child is not None:
            await asyncio.to_thread(self._terminate, child)

    async def stop_all(self) -> None:
        for name in list(self._children):
            await self.stop(name)

    def status(self) -> list[dict]:
        now = self.clock()
        names = {m.name for m in self.store.models()} | set(self._children)
        rows = []
        for name in sorted(names):
            model = self.store.get_model(name)
            child = self._children.get(name)
            row = {"name": name, "state": "cold", "port": None, "pid": None,
                   "idle_seconds": None, "pinned": bool(model and model.pinned)}
            if child is not None:
                if child.proc.poll() is not None:
                    child.crashed = True
                row["state"] = "crashed" if child.crashed else ("ready" if child.ready else "starting")
                row["port"], row["pid"] = child.port, child.proc.pid
                if child.ready and not child.crashed:
                    row["idle_seconds"] = now - child.last_used
            rows.append(row)
        return rows

    # -- internals --------------------------------------------------------------------------

    @staticmethod
    def _url(child: _Child) -> str:
        return f"http://{HOST}:{child.port}/v1"

    def _log_path(self, name: str) -> Path:
        logs = mst_home() / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        return logs / f"{name}.log"

    def _command(self, model, port: int) -> list[str]:
        # A pulled model loads from its pinned local snapshot, so serving needs no network.
        cmd = [*self.max_command, "serve", "--port", str(port), "--served-model-name", model.name]
        if model.weights_path and Path(model.weights_path).is_dir():
            return cmd + ["--model-path", model.weights_path]
        cmd += ["--model-path", model.repo_id]
        if model.revision:
            cmd += ["--huggingface-model-revision", model.revision]
        return cmd

    def _spawn(self, model) -> _Child:
        port = _free_port()
        env = {**os.environ, "MAX_SERVE_HOST": HOST}
        log = open(self._log_path(model.name), "ab")
        try:
            proc = subprocess.Popen(
                self._command(model, port), stdout=log, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, start_new_session=True, env=env,
            )
        except OSError as e:
            raise StartFailed(f"could not run {' '.join(self.max_command)}: {e}") from e
        finally:
            log.close()
        return _Child(proc=proc, port=port, last_used=self.clock())

    async def _wait_healthy(self, name: str, child: _Child) -> None:
        deadline = time.monotonic() + self.health_timeout  # real time: this is a real wait
        url = f"http://{HOST}:{child.port}/health"
        async with httpx.AsyncClient(timeout=2.0) as http:
            while True:
                if child.proc.poll() is not None:
                    raise StartFailed(
                        f"max serve for {name!r} exited with code {child.proc.returncode}:\n"
                        f"{self._log_tail(name)}")
                try:
                    if (await http.get(url)).status_code == 200:
                        return
                except httpx.TransportError:
                    pass
                if time.monotonic() > deadline:
                    raise StartFailed(
                        f"max serve for {name!r} was not healthy after {self.health_timeout:g}s:\n"
                        f"{self._log_tail(name)}")
                await asyncio.sleep(0.1)

    def _log_tail(self, name: str) -> str:
        path = self._log_path(name)
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - LOG_TAIL_BYTES))
            return f.read().decode(errors="replace").strip()

    @staticmethod
    def _reap_process(child: _Child) -> None:
        if child.proc.poll() is None:
            Supervisor._terminate(child)
        else:
            child.proc.wait()

    @staticmethod
    def _terminate(child: _Child, grace: float = 10.0) -> None:
        proc = child.proc
        if proc.poll() is None:
            _signal_group(proc, signal.SIGTERM)
            try:
                proc.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                _signal_group(proc, signal.SIGKILL)
                proc.wait()
        else:
            _signal_group(proc, signal.SIGKILL)  # sweep stragglers in the group


def _signal_group(proc: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(proc.pid, sig)  # child leads its own session/group
    except (ProcessLookupError, PermissionError):
        pass


def _free_port() -> int:
    with socket.socket() as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]
