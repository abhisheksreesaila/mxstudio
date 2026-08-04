"""Background Hugging Face downloads with byte-level progress."""

from __future__ import annotations

import fnmatch
import threading
import time
from pathlib import Path

from huggingface_hub import HfApi, snapshot_download
from huggingface_hub.constants import HF_HUB_CACHE

# Skip alternative weight formats; we only need safetensors + tokenizer/config.
IGNORE_PATTERNS = [
    "*.bin",
    "*.pth",
    "*.pt",
    "*.gguf",
    "*.onnx*",
    "*.msgpack",
    "*.h5",
    "original/*",
]


def repo_dir(model_id: str) -> Path:
    return Path(HF_HUB_CACHE) / ("models--" + model_id.replace("/", "--"))


def is_installed(model_id: str) -> bool:
    snapshots = repo_dir(model_id) / "snapshots"
    if not snapshots.is_dir():
        return False
    for snap in snapshots.iterdir():
        has_weights = any(snap.glob("*.safetensors"))
        if has_weights and (snap / "config.json").exists():
            return True
    return False


def local_size_bytes(model_id: str) -> int:
    blobs = repo_dir(model_id) / "blobs"
    if not blobs.is_dir():
        return 0
    total = 0
    for f in blobs.iterdir():
        try:
            total += f.stat().st_size
        except OSError:
            continue
    return total


def uninstall(model_id: str) -> dict:
    """Delete the local HF cache directory for this model to free disk."""
    path = repo_dir(model_id)
    freed = local_size_bytes(model_id)
    if not path.exists():
        return {"ok": True, "model_id": model_id, "freed_bytes": 0, "missing": True}
    import shutil

    shutil.rmtree(path)
    return {
        "ok": True,
        "model_id": model_id,
        "freed_bytes": freed,
        "path": str(path),
        "missing": False,
    }


class DownloadManager:
    def __init__(self) -> None:
        self._jobs: dict[str, dict] = {}
        self._total_cache: dict[str, int | None] = {}
        self._lock = threading.Lock()

    def total_size_bytes(self, model_id: str) -> int | None:
        if model_id in self._total_cache:
            return self._total_cache[model_id]
        total: int | None
        try:
            info = HfApi().model_info(model_id, files_metadata=True)
            total = 0
            for sibling in info.siblings or []:
                if sibling.size is None:
                    continue
                if any(
                    fnmatch.fnmatch(sibling.rfilename, pat)
                    for pat in IGNORE_PATTERNS
                ):
                    continue
                total += sibling.size
        except Exception:
            total = None
        self._total_cache[model_id] = total
        return total

    def start(self, model_id: str) -> dict:
        with self._lock:
            job = self._jobs.get(model_id)
            if job and job["state"] == "downloading":
                return job
            job = {"state": "downloading", "error": None, "started_at": time.time()}
            self._jobs[model_id] = job

        def _worker() -> None:
            try:
                snapshot_download(model_id, ignore_patterns=IGNORE_PATTERNS)
                job["state"] = "done"
            except Exception as exc:  # surfaced to the UI/CLI
                job["state"] = "error"
                job["error"] = str(exc)[:500]

        threading.Thread(target=_worker, daemon=True, name=f"dl-{model_id}").start()
        return job

    def status(self, model_id: str) -> dict | None:
        job = self._jobs.get(model_id)
        if job is None:
            return None
        done = local_size_bytes(model_id)
        total = self._total_cache.get(model_id)
        pct = None
        if total:
            pct = min(100, round(done / total * 100, 1))
        return {**job, "done_bytes": done, "total_bytes": total, "pct": pct}


MANAGER = DownloadManager()
