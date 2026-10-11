"""Remove a registered model: unregister it, drop its compiled-graph cache, optionally its HF snapshot.

Stopping a running model is the caller's job (the daemon awaits `supervisor.stop` first); this
module only touches the store and the disk, so `mxc rm` can also run with the daemon down.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from mxconsole.store import Store, mxc_home


class UnknownModel(Exception):
    pass


def graph_cache_dir(name: str) -> Path:
    """The one compiled-graph cache that is clearly this model's: <MXC_HOME>/graph-cache/<name>.

    MAX itself keeps compiled graphs in its own shared cache (~/.modular/.max_cache, or under
    MODULAR_DERIVED_PATH), keyed by a hash of the graph, not by model name. Nothing there can be
    tied to one model, so removal never touches it; it is shared by every model and by other MAX
    users on this machine.
    """
    return mxc_home() / "graph-cache" / name


def hf_repo_dir(weights_path: str | None) -> Path | None:
    """models--<org>--<name> above a snapshot path in a Hugging Face cache, or None for any other path."""
    if not weights_path:
        return None
    path = Path(weights_path)
    for parent in path.parents:
        if parent.name.startswith("models--") and path.parts[len(parent.parts):][:1] == ("snapshots",):
            return parent
    return None


def remove(store: Store, name: str, *, purge: bool = False) -> dict:
    model = store.get_model(name)
    if model is None:
        raise UnknownModel(name)
    repo = hf_repo_dir(model.weights_path)
    store.remove_model(name)
    cache = graph_cache_dir(name)
    cache_deleted = cache.is_dir()
    if cache_deleted:
        shutil.rmtree(cache, ignore_errors=True)
    result = {"name": name, "removed": True, "graph_cache_deleted": cache_deleted,
              "purged": False, "purged_path": None, "purge_note": None}
    if purge:
        if repo is None:
            result["purge_note"] = "its weights are not in a Hugging Face cache, so nothing was deleted"
        else:
            sharing = [m.name for m in store.models() if hf_repo_dir(m.weights_path) == repo]
            if sharing:
                result["purge_note"] = f"kept the weights: {', '.join(sharing)} still uses them"
            elif repo.is_dir():
                shutil.rmtree(repo, ignore_errors=True)
                result.update(purged=True, purged_path=str(repo))
            else:
                result["purge_note"] = "the weights were already gone"
    return result
