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
import threading
from contextlib import contextmanager
from pathlib import Path

import httpx

from notebooks.mxstudio import state
from notebooks.mxstudio.registry import ModelEntry

MODEL_PORT = 8100
MODEL_HOST = "127.0.0.1"
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
        stat = Path(f"/proc/{pid}/stat")
        if stat.exists() and stat.read_text().rsplit(")", 1)[1].split()[0] == "Z":
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                pass
            return False
        return True
    except (ProcessLookupError, PermissionError, FileNotFoundError):
        return False


def _process_identity(pid: int) -> str | None:
    try:
        if platform.system() == "Linux":
            start = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
            boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            return f"{boot}:{start}"
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart="],
            capture_output=True, text=True, timeout=2, check=True,
        )
        return result.stdout.strip() or None
    except (OSError, IndexError, subprocess.SubprocessError):
        return None


def _owned(server: dict) -> bool:
    pid = server.get("pid")
    identity = server.get("process_identity")
    try:
        return bool(pid and identity and identity == _process_identity(pid) and os.getpgid(pid) == pid)
    except ProcessLookupError:
        return False


class ServerManager:
    def __init__(self) -> None:
        self._health_cache: tuple[float, bool] | None = None
        self._lifecycle_lock = threading.RLock()
        self._transition_owner = threading.local()

    @contextmanager
    def reservation(self, *, shared: bool = False):
        """Cross-process exclusion: inference holds shared, transitions exclusive.

        Nonblocking acquisition is intentional: never block an API thread or
        replace a model underneath another process's active inference request.
        The descriptor, not thread identity, owns a shared reservation.
        """
        import fcntl

        state.HOME.mkdir(parents=True, exist_ok=True)
        fd = os.open(state.HOME / "server.lifecycle.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(fd, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("model is busy; retry after active requests or preparation finish") from None
            yield
        finally:
            os.close(fd)

    @contextmanager
    def inference_reservation(self, model_id: str):
        with self.reservation(shared=True):
            current = self.status()
            if current.get("state") != "running" or current.get("model_id") != model_id:
                raise RuntimeError("model changed before admission; retry with the current model")
            yield current

    @contextmanager
    def transition(self):
        """Reentrant on the owning worker so prepare can call start safely."""
        with self._lifecycle_lock:
            if getattr(self._transition_owner, "active", False):
                yield
                return
            with self.reservation():
                self._transition_owner.active = True
                try:
                    yield
                finally:
                    self._transition_owner.active = False

    def start(self, entry: ModelEntry) -> dict:
        with self.transition():
            return self._start_unlocked(entry)

    def _start_unlocked(self, entry: ModelEntry) -> dict:
        current = state.load().get("server")
        if current and _alive(current.get("pid")):
            if not _owned(current):
                raise RuntimeError("cannot verify existing MAX process ownership; inspect it before retrying")
            if current.get("model_id") == entry.id and current.get("host") == MODEL_HOST:
                return self.status()
            self._stop_unlocked()

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
        env["MAX_SERVE_HOST"] = MODEL_HOST
        if is_macos:
            env.pop("CUDA_VISIBLE_DEVICES", None)
        else:
            env["CUDA_VISIBLE_DEVICES"] = entry.cuda_devices
        # Ensure `max` from the pixi env is found when launched outside pixi run.
        pixi_bin = str(PROJECT_ROOT / ".pixi" / "envs" / "default" / "bin")
        env["PATH"] = pixi_bin + os.pathsep + env.get("PATH", "")
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                cwd=str(PROJECT_ROOT),
                env=env,
            )
        finally:
            log_file.close()
        state.update(
            server={
                "pid": proc.pid,
                "process_identity": _process_identity(proc.pid),
                "port": MODEL_PORT,
                "host": MODEL_HOST,
                "model_id": entry.id,
                "log": str(log_path),
                "started_at": time.time(),
            }
        )
        self._health_cache = None
        return self.status()

    def stop(self) -> None:
        with self.transition():
            self._stop_unlocked()

    def _stop_unlocked(self) -> None:
        server = state.load().get("server")
        if server and _alive(server.get("pid")):
            if not _owned(server):
                raise RuntimeError("cannot verify existing MAX process ownership; refusing to stop it")
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
                deadline = time.monotonic() + 5
                while _alive(pid) and time.monotonic() < deadline:
                    time.sleep(0.1)
                if _alive(pid):
                    raise RuntimeError("MAX did not exit; refusing to start a replacement")
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
            "host": server.get("host"),
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
        if not _owned(server):
            return {"state": "unmanaged", **base}
        if self._healthy(server["port"]):
            return {"state": "running", **base}
        return {"state": "starting", **base}

    def model_matches(self, model_id: str) -> bool:
        try:
            response = httpx.get(
                f"http://{MODEL_HOST}:{MODEL_PORT}/v1/models", timeout=2.0, trust_env=False,
            )
            response.raise_for_status()
            return any(item.get("id") == model_id for item in response.json().get("data", []))
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            return False

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
