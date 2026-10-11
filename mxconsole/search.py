"""`mxc search` / GET /admin/search: find a model and say whether it fits on this machine.

Matches curated names, the MAX catalog (resolve.load_catalog, cached on disk) and, when the query
looks like a Hugging Face repo id (owner/name), that repo. Every outside source is injected so
tests never touch the network, a GPU or /proc.

Memory estimate (a heuristic, not a measurement):
    size_gb      = parameters x 2 bytes (bfloat16 weights, what MAX serves by default) / 1e9
    est_vram_gb  = size_gb x 1.2 (activations, buffers, fragmentation) + 1.0 GB KV-cache headroom
Fit is judged against the free memory of the GPU with the most room right now (what other programs
and already-loaded models have taken is already subtracted). With no NVIDIA GPU it uses available
system RAM instead and the answer's `memory.basis` says "ram".
    ok     est <= 80% of free
    tight  est <= free
    no     est > free
    port   MAX does not support the architecture yet (needs `mxc port`), whatever the size
    null   the size is unknown, so no verdict
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from mxconsole import resolve

WEIGHT_OVERHEAD = 1.2
KV_HEADROOM_GB = 1.0
BYTES_PER_PARAM = 2
OK_FRACTION = 0.8
MAX_RESULTS = 20
MAX_SUGGESTIONS = 3
MAX_QUERY = 200
_REPO_PART = r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?"
_REPO_ID = re.compile(f"{_REPO_PART}/{_REPO_PART}")


def looks_like_repo_id(q: str) -> bool:
    """Hugging Face's own rules: owner/name, at most 96 characters, no leading or trailing '.' or '-', no '..' or '--'."""
    return len(q) <= 96 and ".." not in q and "--" not in q and _REPO_ID.fullmatch(q) is not None
_PARAMS = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)([bBmM])(?![A-Za-z0-9])")


def params_from_name(text: str) -> int | None:
    """'Qwen2.5-1.5B-Instruct' -> 1_500_000_000 (the largest figure named: 'OLMoE-1B-7B' -> 7B)."""
    values = [float(n) * (1e9 if u in "bB" else 1e6) for n, u in _PARAMS.findall(text)]
    return int(max(values)) if values else None


def size_gb(params: int | None) -> float | None:
    return None if params is None else params * BYTES_PER_PARAM / 1e9


def estimate_vram_gb(size: float | None) -> float | None:
    return None if size is None else size * WEIGHT_OVERHEAD + KV_HEADROOM_GB


def fit_for(est: float | None, free_gb: float, supported: bool) -> str | None:
    if not supported:
        return "port"
    if est is None:
        return None
    return "ok" if est <= free_gb * OK_FRACTION else "tight" if est <= free_gb else "no"


@dataclass
class Sources:
    """What search reads from outside. Defaults are the real thing; tests pass fakes."""
    catalog: Callable[[], dict] = resolve.load_catalog
    hub: Callable[[], object] = resolve.HubClient  # built only if the query is a repo id
    gpus: Callable[[], list[dict]] | None = None
    ram: Callable[[], tuple[float, float]] | None = None  # (available_gb, total_gb)


def system_ram_gb() -> tuple[float, float]:
    info = {}
    with open("/proc/meminfo") as f:
        for line in f:
            key, _, rest = line.partition(":")
            info[key] = int(rest.split()[0]) / 1024 / 1024  # kB -> GB
    return info.get("MemAvailable", 0.0), info.get("MemTotal", 0.0)


def memory(src: Sources) -> dict:
    """Free memory a model could use right now: the roomiest GPU, else system RAM."""
    from mxconsole import metrics
    gpus = (src.gpus or metrics.gpus)()
    if gpus:
        best = max(gpus, key=lambda g: g["total_mb"] - g["used_mb"])
        return {"basis": "gpu", "name": best["name"], "free_gb": round((best["total_mb"] - best["used_mb"]) / 1024, 1),
                "total_gb": round(best["total_mb"] / 1024, 1)}
    try:
        free, total = (src.ram or system_ram_gb)()
    except OSError:
        free = total = 0.0
    return {"basis": "ram", "name": "system RAM (no NVIDIA GPU found)", "free_gb": round(free, 1), "total_gb": round(total, 1)}


def _row(*, id_: str, repo_id: str, source: str, architecture: str | None, params: int | None, supported: bool,
         free_gb: float) -> dict:
    size = size_gb(params)
    est = estimate_vram_gb(size)
    return {"id": id_, "repo_id": repo_id, "source": source, "architecture": architecture, "params": params,
            "size_gb": None if size is None else round(size, 2),
            "est_vram_gb": None if est is None else round(est, 1),
            "supported": supported, "fit": fit_for(est, free_gb, supported), "suggestions": []}


def _pool(catalog: dict, free_gb: float) -> list[dict]:
    """Every curated and catalog model whose size is known, for matching and for suggestions."""
    by_repo: dict[str, str] = {}
    for arch, entry in catalog.items():
        for repo in entry.get("example_repo_ids", []):
            by_repo.setdefault(repo.lower(), arch)
    rows, seen = [], set()
    for name, repo in resolve.CURATED.items():
        arch = by_repo.get(repo.lower())
        rows.append(_row(id_=name, repo_id=repo, source="max", architecture=arch,
                         params=params_from_name(repo), supported=True, free_gb=free_gb))  # curated = in the catalog
        seen.add(repo.lower())
    for arch, entry in sorted(catalog.items()):
        for repo in entry.get("example_repo_ids", []):
            if repo.lower() not in seen:
                seen.add(repo.lower())
                rows.append(_row(id_=repo, repo_id=repo, source="max", architecture=arch,
                                 params=params_from_name(repo), supported=True, free_gb=free_gb))
    return rows


def _matches(row: dict, tokens: list[str]) -> bool:
    hay = f"{row['id']} {row['repo_id']} {row['architecture'] or ''}".lower()
    return all(t in hay for t in tokens)


def _suggest(row: dict, pool: list[dict], free_gb: float) -> list[dict]:
    """Up to three smaller supported models that fit comfortably, same architecture first, biggest first."""
    fits = [p for p in pool if p["fit"] == "ok" and p["repo_id"].lower() != row["repo_id"].lower()
            and p["est_vram_gb"] is not None and (row["est_vram_gb"] is None or p["est_vram_gb"] < row["est_vram_gb"])]
    fits.sort(key=lambda p: (p["architecture"] != row["architecture"], -p["est_vram_gb"]))
    return [{k: p[k] for k in ("id", "repo_id", "architecture", "params", "size_gb", "est_vram_gb")}
            for p in fits[:MAX_SUGGESTIONS]]


def search(q: str, src: Sources | None = None) -> dict:
    src = src or Sources()
    q = q.strip()
    mem = memory(src)
    free = mem["free_gb"]
    catalog = src.catalog()
    pool = _pool(catalog, free)
    tokens = q.lower().split()
    results = [r for r in pool if _matches(r, tokens)] if tokens else [r for r in pool if r["id"] in resolve.CURATED]
    results.sort(key=lambda r: (r["id"].lower() != q.lower(), r["id"] not in resolve.CURATED))  # exact name, then curated
    results = results[:MAX_RESULTS]
    warnings: list[str] = []
    if looks_like_repo_id(q) and not any(r["repo_id"].lower() == q.lower() for r in results):
        row = _hf_row(q, src, catalog, free, warnings)
        if row:
            results.insert(0, row)
    for r in results:
        if r["fit"] == "no":
            r["suggestions"] = _suggest(r, pool, free)
    return {"query": q, "memory": mem, "results": results, "warnings": warnings}


def _hf_row(repo_id: str, src: Sources, catalog: dict, free_gb: float, warnings: list[str]) -> dict | None:
    hub = src.hub()
    try:
        info = hub.inspect(repo_id)
    except (resolve.RepoNotFound, resolve.RepoGated):
        return None  # nothing to offer: pulling it would fail the same way
    except resolve.ResolveError as e:
        warnings.append(str(e))
        return None
    except ValueError:  # the client's own repo-id validation said no
        return None
    params = None
    counter = getattr(hub, "param_count", None)
    if counter:
        try:
            params = counter(repo_id)
        except Exception:  # a size is a nicety; the verdict does not need the network twice
            params = None
    params = params or params_from_name(repo_id)
    supported = (info.architecture in catalog) and any(f.endswith(".safetensors") for f in info.files)
    return _row(id_=repo_id, repo_id=repo_id, source="hf", architecture=info.architecture, params=params,
                supported=supported, free_gb=free_gb)
