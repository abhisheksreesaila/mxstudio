"""Remove a registered model: unregister it, drop its compiled-graph cache, optionally its HF snapshot.

Order matters. The daemon unregisters first (`unregister`), so a request that arrives mid-removal is
answered 404 instead of respawning a child for a model that is going away; then it stops the child,
then `cleanup` touches the disk. `remove` runs all of it for `mxc rm` with the daemon down.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from mxconsole.store import Model, Store, mxc_home


class UnknownModel(Exception):
    pass


def graph_cache_root() -> Path:
    return mxc_home() / "graph-cache"


def graph_cache_dir(name: str) -> Path:
    """The one compiled-graph cache that is clearly this model's: <MXC_HOME>/graph-cache/<name>.

    MAX itself keeps compiled graphs in its own shared cache (~/.modular/.max_cache, or under
    MODULAR_DERIVED_PATH), keyed by a hash of the graph, not by model name. Nothing there can be
    tied to one model, so removal never touches it.
    """
    return graph_cache_root() / name


def hf_cache_root() -> Path:
    from huggingface_hub import constants
    return Path(os.environ.get("HF_HUB_CACHE") or constants.HF_HUB_CACHE)


def hf_repo_dir(weights_path: str | None) -> Path | None:
    """models--<org>--<name> above a snapshot path in a Hugging Face cache, or None for any other path."""
    if not weights_path:
        return None
    path = Path(weights_path)
    for parent in path.parents:
        if parent.name.startswith("models--") and path.parts[len(parent.parts):][:1] == ("snapshots",):
            return parent
    return None


MAX_SIZE_FILES = 2000  # a weights folder holds a handful of files; a bigger tree is not one, and the poll must stay fast
_sizes: dict[tuple[str, int], int | None] = {}


def weights_size(weights_path: str | None) -> int | None:
    """Bytes of a model's weights on disk (a folder adds up its files, links followed), or None when there is nothing to count
    (no files, a missing folder, or more than MAX_SIZE_FILES files: not a weights folder).
    The console polls this for every model every few seconds, so a result is kept while the folder's mtime stays the same:
    a pull that is still adding files changes it, and the number grows with it."""
    if not weights_path:
        return None
    path = Path(weights_path)
    try:
        key = (weights_path, path.stat().st_mtime_ns)
    except OSError:
        return None
    if key in _sizes:
        return _sizes[key]
    try:
        if path.is_file():
            total = path.stat().st_size
        else:
            total, seen = 0, 0
            for f in path.rglob("*"):
                if not f.is_file():  # folders, and links that point nowhere
                    continue
                seen += 1
                if seen > MAX_SIZE_FILES:
                    total = 0
                    break
                total += f.stat().st_size
    except OSError:
        return None
    for old in [k for k in _sizes if k[0] == weights_path]:
        del _sizes[old]
    _sizes[key] = total or None
    return total or None


def _inside(path: Path, root: Path) -> bool:
    """True when `path` resolves strictly below `root` (symlinks and `..` followed)."""
    try:
        return path.resolve().is_relative_to(root.resolve()) and path.resolve() != root.resolve()
    except (OSError, RuntimeError):
        return False


def unregister(store: Store, name: str) -> Model:
    model = store.get_model(name)
    if model is None:
        raise UnknownModel(name)
    store.remove_model(name)
    return model


def cleanup(store: Store, model: Model, *, purge: bool = False) -> dict:
    """Disk work after the model is unregistered and stopped."""
    name = model.name
    result = {"name": name, "removed": True, "graph_cache_deleted": False,
              "purged": False, "purged_path": None, "purge_note": None}
    cache = graph_cache_dir(name)
    if cache.is_dir() and not cache.is_symlink() and _inside(cache, graph_cache_root()):
        shutil.rmtree(cache)
        result["graph_cache_deleted"] = True
    if purge:
        _purge(store, model, result)
    return result


def _purge(store: Store, model: Model, result: dict) -> None:
    repo = hf_repo_dir(model.weights_path)
    if repo is None or repo.parent.resolve() != hf_cache_root().resolve() or repo.is_symlink():
        result["purge_note"] = "its weights are not in the Hugging Face cache, so nothing was deleted"
        return
    sharing = [m.name for m in store.models() if hf_repo_dir(m.weights_path) == repo]
    if sharing:
        result["purge_note"] = f"kept the weights: {', '.join(sharing)} still uses them"
    elif not repo.is_dir():
        result["purge_note"] = "the weights were already gone"
    else:
        shutil.rmtree(repo, ignore_errors=True)
        if repo.exists():
            result["purge_note"] = f"could not delete all of {repo} (permissions?)"
        else:
            result.update(purged=True, purged_path=str(repo))


def remove(store: Store, name: str, *, purge: bool = False) -> dict:
    return cleanup(store, unregister(store, name), purge=purge)
