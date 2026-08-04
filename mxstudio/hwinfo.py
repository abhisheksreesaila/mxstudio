"""Hardware detection: GPU (nvidia-smi), CPU, RAM, disk, WSL flag."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=5
        ).stdout.strip()
    except Exception:
        return ""


def gpus() -> list[dict]:
    out = _run(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total,memory.used,utilization.gpu,temperature.gpu",
            "--format=csv,noheader,nounits",
        ]
    )
    result = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 5:
            try:
                result.append(
                    {
                        "name": parts[0],
                        "vram_total_mb": int(float(parts[1])),
                        "vram_used_mb": int(float(parts[2])),
                        "util_pct": int(float(parts[3])),
                        "temp_c": int(float(parts[4])),
                    }
                )
            except ValueError:
                continue
    return result


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
    usage = shutil.disk_usage(Path.home())
    return {
        "total_gb": round(usage.total / 1e9, 1),
        "free_gb": round(usage.free / 1e9, 1),
    }


def is_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False


def summary() -> dict:
    return {
        "gpus": gpus(),
        "cpu": cpu(),
        "memory": memory(),
        "disk": disk(),
        "os": f"{platform.system()} {platform.release()}",
        "wsl": is_wsl(),
        "python": platform.python_version(),
    }
