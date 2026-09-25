"""Background Hugging Face downloads with byte-level progress."""

from __future__ import annotations

import fnmatch
import json
import threading
import time
from pathlib import Path
from collections.abc import Callable, Iterable

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


def cache_root() -> Path:
    return Path(HF_HUB_CACHE)


def installed_model_ids() -> list[str]:
    """Return complete model snapshots found in the local Hugging Face cache."""
    root = cache_root()
    if not root.is_dir():
        return []
    model_ids = []
    for path in root.glob("models--*"):
        encoded = path.name.removeprefix("models--")
        model_id = encoded.replace("--", "/")
        if model_id and is_installed(model_id):
            model_ids.append(model_id)
    return sorted(model_ids, key=str.casefold)


def local_info(model_id: str) -> dict:
    """Where this model lives on disk (HF hub cache layout)."""
    path = repo_dir(model_id)
    return {
        "model_id": model_id,
        "path": str(path),
        "exists": path.is_dir(),
        "installed": is_installed(model_id),
        "cache_root": str(cache_root()),
        "size_bytes": local_size_bytes(model_id) if path.is_dir() else 0,
    }


def open_containing_folder(model_id: str) -> dict:
    """Open the model cache folder in the OS file manager (Explorer / xdg-open)."""
    from notebooks.mxstudio.pathops import reveal_path

    info = local_info(model_id)
    path = Path(info["path"])
    target = path if path.is_dir() else cache_root()
    return {
        **info,
        **reveal_path(target, allowed_roots=(cache_root(),)),
    }

def is_installed(model_id: str) -> bool:
    snapshots = repo_dir(model_id) / "snapshots"
    if not snapshots.is_dir():
        return False
    for snap in snapshots.iterdir():
        has_weights = any(snap.glob("*.safetensors"))
        if has_weights and (snap / "config.json").exists():
            return True
    return False


def required_snapshot_files(file_names: Iterable[str]) -> list[str]:
    """Select a complete safetensors checkpoint and its config/tokenizer artifacts."""
    names = sorted({str(name) for name in file_names if name})
    if "config.json" not in names:
        raise ValueError("Hugging Face repository has no config.json")

    indexes = [
        name for name in names
        if name.endswith(".safetensors.index.json")
    ]
    if indexes:
        # The remote index's weight_map is content, not exposed by HfApi's file
        # listing. Include all safetensors so every referenced shard is present.
        required = {"config.json", *indexes}
        required.update(name for name in names if name.endswith(".safetensors"))
    else:
        weights = [name for name in names if name.endswith(".safetensors")]
        if not weights:
            raise ValueError("No safetensors weights found; this runtime path requires safetensors")
        required = {"config.json", *weights}

    tokenizer_names = {
        "tokenizer.json", "tokenizer.model", "tokenizer_config.json",
        "special_tokens_map.json", "added_tokens.json", "vocab.json",
        "vocab.txt", "merges.txt", "spiece.model", "sentencepiece.bpe.model",
        "generation_config.json",
    }
    tokenizers = [name for name in names if Path(name).name in tokenizer_names]
    if not any(Path(name).name in {"tokenizer.json", "tokenizer.model", "spiece.model",
                                   "sentencepiece.bpe.model", "vocab.json", "vocab.txt"}
               for name in tokenizers):
        raise ValueError("Hugging Face repository has no recognized tokenizer files")
    required.update(tokenizers)
    return sorted(required)


def validate_snapshot(
    snapshot_path: str | Path,
    expected_files: Iterable[str] | None = None,
    expected_sizes: dict[str, int] | None = None,
) -> dict:
    """Verify config, tokenizer, and every indexed/unsharded safetensors file."""
    snapshot = Path(snapshot_path)
    expected_file_set = set(expected_files) if expected_files is not None else None
    missing: list[str] = []
    size_mismatches: list[dict] = []
    try:
        config_path = snapshot / "config.json"
        if not config_path.is_file():
            missing.append("config.json")
        names = [p.relative_to(snapshot).as_posix() for p in snapshot.rglob("*") if p.is_file()]
        if expected_file_set is not None:
            missing.extend(
                name for name in expected_file_set if not (snapshot / name).is_file()
            )
        for name, expected_size in (expected_sizes or {}).items():
            path = snapshot / name
            if path.is_file() and path.stat().st_size != expected_size:
                size_mismatches.append(
                    {"name": name, "expected": expected_size, "actual": path.stat().st_size}
                )
        indexes = [name for name in names if name.endswith(".safetensors.index.json")]
        required_weights: set[str] = set()
        if indexes:
            for index in indexes:
                try:
                    index_data = json.loads((snapshot / index).read_text(encoding="utf-8"))
                    required_weights.update((index_data.get("weight_map") or {}).values())
                except (OSError, json.JSONDecodeError, AttributeError):
                    missing.append(index)
        else:
            required_weights.update(name for name in names if name.endswith(".safetensors"))
        if not required_weights:
            missing.append("*.safetensors")
        missing.extend(
            name for name in required_weights if not (snapshot / name).is_file()
        )
        tokenizer_names = {
            "tokenizer.json", "tokenizer.model", "spiece.model",
            "sentencepiece.bpe.model", "vocab.json", "vocab.txt",
        }
        if not any(Path(name).name in tokenizer_names for name in names):
            missing.append("tokenizer files")
        manifest_names = sorted(expected_file_set if expected_file_set is not None else set(names))
        manifest = [
            {"name": name, "size": (snapshot / name).stat().st_size}
            for name in manifest_names
            if (snapshot / name).is_file()
        ]
    except OSError:
        manifest = []
        missing.append("snapshot directory")
    missing = sorted(set(missing))
    return {
        "ok": not missing and not size_mismatches,
        "snapshot_path": str(snapshot.resolve()),
        "manifest": manifest,
        "missing_files": missing,
        "size_mismatches": size_mismatches,
    }


def snapshot_for_revision(model_id: str, revision: str) -> Path | None:
    snapshot = repo_dir(model_id) / "snapshots" / revision
    return snapshot if validate_snapshot(snapshot)["ok"] else None


def pull_revision(
    model_id: str,
    revision: str,
    file_names: Iterable[str],
    *,
    token: str | None = None,
    progress: Callable[[dict], None] | None = None,
    expected_sizes: dict[str, int] | None = None,
) -> dict:
    """Fetch a SHA-pinned model subset and reject incomplete snapshots."""
    selected = required_snapshot_files(file_names)
    if progress:
        progress({"stage": "downloading", "revision": revision, "files": len(selected)})
    snapshot = snapshot_download(
        repo_id=model_id,
        revision=revision,
        token=token,
        allow_patterns=selected,
    )
    result = validate_snapshot(snapshot, selected, expected_sizes)
    if not result["ok"]:
        problems = result["missing_files"] + [
            f"{item['name']} has {item['actual']} bytes; expected {item['expected']}"
            for item in result["size_mismatches"]
        ]
        raise RuntimeError(
            "Downloaded snapshot is incomplete: " + ", ".join(problems)
        )
    result["revision"] = revision
    if progress:
        progress({"stage": "downloaded", "revision": revision, "files": len(result["manifest"])})
    return result


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


def local_model_metadata(model_id: str) -> dict:
    """Read useful catalog fields from an installed snapshot's config.json."""
    snapshots = repo_dir(model_id) / "snapshots"
    config: dict = {}
    if snapshots.is_dir():
        for snapshot in sorted(snapshots.iterdir(), reverse=True):
            config_path = snapshot / "config.json"
            if not config_path.is_file():
                continue
            try:
                config = json.loads(config_path.read_text())
            except (OSError, json.JSONDecodeError):
                pass
            break

    architectures = config.get("architectures") or []
    architecture = architectures[0] if architectures else config.get("model_type", "")
    context = next(
        (
            int(config[key])
            for key in ("max_position_embeddings", "max_sequence_length", "seq_length")
            if isinstance(config.get(key), (int, float))
        ),
        0,
    )
    size_bytes = local_size_bytes(model_id)
    return {
        "approx_gb": round(size_bytes / 1_000_000_000, 1),
        "context": context,
        "architecture": architecture,
        "family": str(config.get("model_type", "")).replace("_", " ").title(),
    }


def uninstall(model_id: str) -> dict:
    """Delete the local HF cache directory for this model to free disk."""
    path = repo_dir(model_id)
    freed = local_size_bytes(model_id)
    if not path.exists():
        return {"ok": True, "model_id": model_id, "freed_bytes": 0, "missing": True}
    import shutil

    try:
        shutil.rmtree(path)
    except OSError as exc:
        return {
            "ok": False,
            "model_id": model_id,
            "freed_bytes": 0,
            "path": str(path),
            "missing": False,
            "error": str(exc)[:500],
        }
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
