"""Hardware detection: GPU (nvidia-smi), CPU, RAM, disk, WSL flag."""

from __future__ import annotations

import csv
import os
import platform
import shutil
import subprocess
import threading
import time
from io import StringIO
from pathlib import Path

_GPU_PROBE_LOCK = threading.Lock()
_GPU_PROBE_CACHE: tuple[float, str, dict] | None = None


def is_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False


def _nvidia_smi() -> str | None:
    found = shutil.which("nvidia-smi")
    if found:
        return found
    for candidate in ("/usr/lib/wsl/lib/nvidia-smi", "/usr/bin/nvidia-smi"):
        if Path(candidate).is_file():
            return candidate
    return None


def _gpu_probe() -> tuple[str, dict]:
    binary = _nvidia_smi()
    if not binary:
        return "", {
            "ok": False,
            "error": "nvidia-smi was not found in the studio process PATH",
        }
    cmd = [
        binary,
        "--query-gpu=name,memory.total,memory.used,utilization.gpu,temperature.gpu",
        "--format=csv,noheader,nounits",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=20 if is_wsl() else 10,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "", {"ok": False, "error": "nvidia-smi timed out", "binary": binary}
    except OSError as exc:
        return "", {"ok": False, "error": str(exc), "binary": binary}
    if proc.returncode != 0:
        return "", {
            "ok": False,
            "error": (proc.stderr or "nvidia-smi failed").strip()[-300:],
            "returncode": proc.returncode,
            "binary": binary,
        }
    return proc.stdout.strip(), {"ok": True, "binary": binary}


def _cached_gpu_probe(max_age_s: float = 5.0) -> tuple[str, dict]:
    """Serialize nvidia-smi and reuse a recent result across browser polls."""
    global _GPU_PROBE_CACHE
    with _GPU_PROBE_LOCK:
        now = time.monotonic()
        if _GPU_PROBE_CACHE and now - _GPU_PROBE_CACHE[0] < max_age_s:
            return _GPU_PROBE_CACHE[1], dict(_GPU_PROBE_CACHE[2])
        output, probe = _gpu_probe()
        _GPU_PROBE_CACHE = (now, output, dict(probe))
        return output, probe


def _number(value: str, *, integer: bool = True) -> int | float | None:
    cleaned = value.strip()
    if cleaned.lower() in {"", "n/a", "[n/a]", "na", "none", "-"}:
        return None
    try:
        number = float(cleaned)
        return int(number) if integer else number
    except ValueError:
        return None


def _parse_gpus(out: str) -> list[dict]:
    result = []
    for parts in csv.reader(StringIO(out)):
        parts = [p.strip() for p in parts]
        if len(parts) >= 5:
            total = _number(parts[1])
            used = _number(parts[2])
            if total is None or used is None:
                continue
            result.append(
                {
                    "name": parts[0],
                    "vram_total_mb": total,
                    "vram_used_mb": used,
                    "util_pct": _number(parts[3]),
                    "temp_c": _number(parts[4]),
                }
            )
    return result


def gpus() -> list[dict]:
    out, _ = _cached_gpu_probe()
    return _parse_gpus(out)


def cpu() -> dict:
    model = ""
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                model = line.split(":", 1)[1].strip()
                break
    except OSError:
        model = platform.processor()
    return {"model": model, "cores": os.cpu_count()}


def memory() -> dict:
    total_kb = avail_kb = 0
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                total_kb = int(line.split()[1])
            elif line.startswith("MemAvailable:"):
                avail_kb = int(line.split()[1])
    except OSError:
        pass
    return {
        "total_gb": round(total_kb / 1024 / 1024, 1),
        "available_gb": round(avail_kb / 1024 / 1024, 1),
    }


def disk() -> dict:
    try:
        usage = shutil.disk_usage(Path.home())
        return {
            "total_gb": round(usage.total / 1e9, 1),
            "free_gb": round(usage.free / 1e9, 1),
        }
    except OSError:
        return {"total_gb": None, "free_gb": None}


def summary() -> dict:
    gpu_output, gpu_probe = _cached_gpu_probe()
    return {
        "gpus": _parse_gpus(gpu_output),
        "gpu_probe": gpu_probe,
        "cpu": cpu(),
        "memory": memory(),
        "disk": disk(),
        "os": f"{platform.system()} {platform.release()}",
        "wsl": is_wsl(),
        "python": platform.python_version(),
    }
