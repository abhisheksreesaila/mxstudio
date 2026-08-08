"""Manage the `max serve` subprocess: start, stop, health, logs.

One model server at a time (a 24GB GPU can't fit several anyway). The server
runs in its own process group so it can never block or take down the studio
backend, and its pid is persisted so the studio survives its own restarts.
"""

from __future__ import annotations

import os
import platform
import signal
import subprocess
import time
from pathlib import Path

import httpx

from notebooks.mxstudio import state
from notebooks.mxstudio.registry import ModelEntry

MODEL_PORT = 8100
PROJECT_ROOT = Path(__file__).resolve().parents[2]  # the mxstudio pixi project
REPO_ROOT = PROJECT_ROOT.parent  # unifiedram


def custom_arch_path(entry: ModelEntry) -> Path | None:
    if not entry.custom_arch:
        return None
    return (PROJECT_ROOT / entry.custom_arch).resolve()


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


class ServerManager:
    def __init__(self) -> None:
        self._health_cache: tuple[float, bool] | None = None

    def start(self, entry: ModelEntry) -> dict:
        current = state.load().get("server")
        if current and _alive(current.get("pid")):
            if current.get("model_id") == entry.id:
                return self.status()
            self.stop()

        log_path = state.LOGS / f"serve_{entry.id.replace('/', '__')}.log"
        cmd = [
            "max",
            "serve",
            "--model",
            entry.id,
            "--port",
            str(MODEL_PORT),
            # CUDA graph capture fails on WSL (CUDA_ERROR_INVALID_DEVICE).
            "--no-device-graph-capture",
        ]
        devices = [d.strip() for d in entry.cuda_devices.split(",") if d.strip()]
        is_macos = platform.system() == "Darwin"
        if is_macos:
            cmd += ["--devices", "cpu"]
        elif len(devices) > 1:
            cmd += ["--devices", "gpu:" + ",".join(devices), "--ep-size", str(len(devices))]
        arch_path = custom_arch_path(entry)
        if arch_path:
            cmd += [
                "--custom-architectures",
                str(arch_path),
                "--max-batch-size",
                "1",
                "--max-batch-input-tokens",
                "256",
            ]

        state.LOGS.mkdir(parents=True, exist_ok=True)
        log_file = open(log_path, "ab")
        env = os.environ.copy()
        if is_macos:
            env.pop("CUDA_VISIBLE_DEVICES", None)
        else:
            env["CUDA_VISIBLE_DEVICES"] = entry.cuda_devices
        # Ensure `max` from the pixi env is found when launched outside pixi run.
        pixi_bin = str(PROJECT_ROOT / ".pixi" / "envs" / "default" / "bin")
        env["PATH"] = pixi_bin + os.pathsep + env.get("PATH", "")
        proc = subprocess.Popen(
            cmd,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            cwd=str(PROJECT_ROOT),
            env=env,
        )
        state.update(
            server={
                "pid": proc.pid,
                "port": MODEL_PORT,
                "model_id": entry.id,
                "log": str(log_path),
                "started_at": time.time(),
            }
        )
        self._health_cache = None
        return self.status()

    def stop(self) -> None:
        server = state.load().get("server")
        if server and _alive(server.get("pid")):
            pid = server["pid"]
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            deadline = time.time() + 15
            while _alive(pid) and time.time() < deadline:
                time.sleep(0.3)
            if _alive(pid):
                try:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
        data = state.load()
        data.pop("server", None)
        state.save(data)
        self._health_cache = None

    def _healthy(self, port: int) -> bool:
        now = time.time()
        if self._health_cache and now - self._health_cache[0] < 2.0:
            return self._health_cache[1]
        try:
            ok = (
                httpx.get(f"http://127.0.0.1:{port}/health", timeout=2.0).status_code
                == 200
            )
        except Exception:
            ok = False
        self._health_cache = (now, ok)
        return ok

    def status(self) -> dict:
        server = state.load().get("server")
        if not server:
            return {"state": "stopped"}
        base = {
            "model_id": server.get("model_id"),
            "port": server.get("port"),
            "log": server.get("log"),
            "uptime_s": round(time.time() - server.get("started_at", time.time())),
        }
        if not _alive(server.get("pid")):
            log_tail = self.logs(40)
            reason = "process exited"
            if "Server ready" in log_tail and "Traceback" not in log_tail:
                reason = (
                    "process disappeared after becoming ready — often a WSL restart "
                    "or host OOM kill, not a MAX compile failure"
                )
            elif "Traceback" in log_tail or "CUDA" in log_tail:
                reason = "max serve exited with an error — see log_tail"
            return {
                "state": "crashed",
                **base,
                "crash_reason": reason,
                "log_tail": log_tail,
            }
        if self._healthy(server["port"]):
            return {"state": "running", **base}
        return {"state": "starting", **base}

    def logs(self, lines: int = 100) -> str:
        server = state.load().get("server")
        if not server or not server.get("log"):
            return ""
        try:
            content = Path(server["log"]).read_text(errors="replace")
        except OSError:
            return ""
        return "\n".join(content.splitlines()[-lines:])


MANAGER = ServerManager()
