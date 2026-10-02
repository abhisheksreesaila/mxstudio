"""Turn a model reference into a verdict ("can MAX run it?"), then pull and register it.

Ported in simplified form from notebooks/mxstudio (compat.py, downloads.py).
The MAX catalog and the Hugging Face client are injected so tests never touch the network.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from mst.store import Model, Store, mst_home

# Short names -> HF repo ids. Each architecture here is in MAX's catalog.
CURATED = {
    "qwen2.5-0.5b": "Qwen/Qwen2.5-0.5B-Instruct",
    "qwen2.5-1.5b": "Qwen/Qwen2.5-1.5B-Instruct",
    "qwen2.5-3b": "Qwen/Qwen2.5-3B-Instruct",
    "smollm2-1.7b": "HuggingFaceTB/SmolLM2-1.7B-Instruct",
    "llama3.2-1b": "meta-llama/Llama-3.2-1B-Instruct",
    "all-minilm-l6": "sentence-transformers/all-MiniLM-L6-v2",
}

CATALOG_TTL = 7 * 24 * 3600  # seconds; `max list` is slow and the list rarely changes

TOKENIZER_FILES = {
    "tokenizer.json", "tokenizer.model", "tokenizer_config.json", "special_tokens_map.json",
    "added_tokens.json", "vocab.json", "vocab.txt", "merges.txt", "spiece.model",
    "sentencepiece.bpe.model", "generation_config.json",
}


class RepoNotFound(Exception):
    pass


class RepoGated(Exception):
    pass


@dataclass(frozen=True)
class RepoInfo:
    sha: str
    architecture: str | None
    files: list[str]


@dataclass(frozen=True)
class Resolution:
    kind: str  # 'max-catalog' | 'hf-supported' | 'unsupported' | 'not-found' | 'gated'
    repo_id: str
    revision: str | None = None
    architecture: str | None = None
    encodings: list[str] = field(default_factory=list)
    message: str = ""
    files: list[str] = field(default_factory=list)

    @property
    def supported(self) -> bool:
        return self.kind in ("max-catalog", "hf-supported")


class PullError(Exception):
    """Raised when a ref cannot be pulled; `.resolution` says why."""

    def __init__(self, resolution: Resolution):
        super().__init__(resolution.message)
        self.resolution = resolution


# ---- MAX catalog ---------------------------------------------------------

def _run_max_list() -> str:
    proc = subprocess.run(["max", "list", "--json"], capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"`max list --json` failed: {proc.stderr.strip()[:200]}")
    return proc.stdout


def load_catalog(*, runner: Callable[[], str] = _run_max_list, refresh: bool = False) -> dict:
    """MAX's architectures -> {example_repo_ids, supported_encodings}, cached in MST_HOME."""
    cache = mst_home() / "max_catalog.json"
    if not refresh and cache.exists() and time.time() - cache.stat().st_mtime < CATALOG_TTL:
        return json.loads(cache.read_text())
    arches = json.loads(runner()).get("architectures", {})
    cache.write_text(json.dumps(arches))
    return arches


# ---- Hugging Face --------------------------------------------------------

class HubClient:
    """The real Hugging Face client. Needs HF_TOKEN in the environment for gated repos."""

    def __init__(self, token: str | None = None):
        from huggingface_hub import HfApi
        self.token = token or os.environ.get("HF_TOKEN") or None
        self.api = HfApi(token=self.token)

    def inspect(self, repo_id: str) -> RepoInfo:
        from huggingface_hub import hf_hub_download
        from huggingface_hub.errors import GatedRepoError, HfHubHTTPError, RepositoryNotFoundError
        try:
            info = self.api.model_info(repo_id)
            files = [s.rfilename for s in info.siblings or []]
            arch = None
            if "config.json" in files:
                path = hf_hub_download(repo_id, "config.json", revision=info.sha, token=self.token)
                archs = json.loads(Path(path).read_text()).get("architectures") or []
                arch = archs[0] if archs else None
            return RepoInfo(sha=info.sha, architecture=arch, files=files)
        except GatedRepoError as e:
            raise RepoGated(repo_id) from e
        except RepositoryNotFoundError as e:
            raise RepoNotFound(repo_id) from e
        except HfHubHTTPError as e:
            if getattr(e.response, "status_code", None) in (401, 403):
                raise RepoGated(repo_id) from e
            raise

    def download(self, repo_id: str, revision: str, patterns: list[str]) -> str:
        from huggingface_hub import snapshot_download
        return snapshot_download(repo_id, revision=revision, allow_patterns=patterns, token=self.token)


# ---- resolve -------------------------------------------------------------

def resolve(ref: str, *, catalog: dict | None = None, hf=None) -> Resolution:
    repo_id = CURATED.get(ref.lower(), ref)
    hf = hf or HubClient()
    try:
        info = hf.inspect(repo_id)
    except RepoGated:
        return Resolution("gated", repo_id, message=(
            f"{repo_id} is gated: accept its license on huggingface.co, then set HF_TOKEN and retry."))
    except RepoNotFound:
        return Resolution("not-found", repo_id, message=(
            f"{ref}: no such model on Hugging Face; check the name (use owner/repo)."))
    arch = info.architecture
    if catalog is None:
        catalog = load_catalog()
    entry = catalog.get(arch) if arch else None
    if entry is None:
        return Resolution("unsupported", repo_id, info.sha, arch, files=info.files, message=(
            f"{repo_id} uses {arch or 'an unknown architecture'}, which MAX does not support yet. "
            f"Next step: mst port {ref}"))
    kind = "max-catalog" if repo_id in entry.get("example_repo_ids", []) else "hf-supported"
    encodings = list(entry.get("supported_encodings", []))
    return Resolution(kind, repo_id, info.sha, arch, encodings, files=info.files,
                      message=f"{repo_id} ({arch}) is supported by MAX [{kind}]")


# ---- pull ----------------------------------------------------------------

def snapshot_patterns(files: list[str]) -> list[str]:
    """Safetensors weights (plus shard index) and config/tokenizer files; nothing else."""
    keep = [f for f in files
            if f == "config.json" or f.endswith(".safetensors") or f.endswith(".safetensors.index.json")
            or Path(f).name in TOKENIZER_FILES]
    if not any(f.endswith(".safetensors") for f in keep):
        raise ValueError("no safetensors weights in this repo")
    return sorted(keep)


def model_name(ref: str, repo_id: str) -> str:
    return ref.lower() if ref.lower() in CURATED else repo_id.split("/")[-1].lower()


def pull(ref: str, *, store: Store | None = None, catalog: dict | None = None, hf=None) -> Model:
    hf = hf or HubClient()
    res = resolve(ref, catalog=catalog, hf=hf)
    if not res.supported:
        raise PullError(res)
    path = hf.download(res.repo_id, res.revision, snapshot_patterns(res.files))
    store = store or Store.open()
    return store.add_model(name=model_name(ref, res.repo_id), repo_id=res.repo_id,
                           revision=res.revision, source=res.kind,
                           architecture=res.architecture, weights_path=str(path))
