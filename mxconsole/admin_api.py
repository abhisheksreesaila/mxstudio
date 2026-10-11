"""Admin routes added for the 1.0 console (F-024): remove a model, search, rotate a key, learn notebooks.

`register(app, store, supervisor, admin_only, refused=..., error=...)` is called once from
gateway.create_app. Every route is admin-only (loopback + the admin header), like the others.
"""
from __future__ import annotations

import asyncio
import logging

from starlette.requests import Request
from starlette.responses import JSONResponse

from pathlib import Path

from mxconsole import expose, jupyter, keys, learn, removal, resolve, search
from mxconsole.store import Store
from mxconsole.supervisor import Supervisor

log = logging.getLogger("mxconsole.admin_api")


def register(app, store: Store, supervisor: Supervisor, admin_only, *, refused, error,
             search_sources: search.Sources | None = None, learn_source=None, jupyter_launcher=None) -> None:
    """`refused()` is the gateway's 403 answer, `error(status, message, code, type_)` its OpenAI-shaped error."""

    def not_registered(name: str) -> JSONResponse:
        return error(404, f"The model `{name}` is not registered.", "model_not_found")

    @app.get("/admin/search")
    async def admin_search(request: Request):
        if not admin_only(request):
            return refused()
        q = request.query_params.get("q", "")
        if len(q) > 200:
            return error(400, "The search text is too long.", "invalid_query")
        try:  # the catalog and Hugging Face are slow and blocking: keep the event loop free
            return JSONResponse(await asyncio.to_thread(search.search, q, search_sources))
        except resolve.ResolveError as e:
            return error(503, str(e), "search_unavailable", "server_error")

    launch = jupyter_launcher or jupyter.JupyterLauncher()

    async def notebook(name: str) -> dict:
        return await asyncio.to_thread(learn.notebook_payload, name, learn_source, store)

    @app.get("/admin/learn/{name}")
    async def admin_learn(request: Request, name: str):
        if not admin_only(request):
            return refused()
        if store.get_model(name) is None:
            return not_registered(name)
        try:
            return JSONResponse(await notebook(name))
        except Exception as e:  # Hugging Face unreachable, gated, a model gone: say it in one line
            return error(502, f"could not build a notebook for {name}: {e}", "learn_failed", "server_error")

    @app.post("/admin/learn/{name}/open")
    async def admin_learn_open(request: Request, name: str):
        if not admin_only(request):
            return refused()
        if store.get_model(name) is None:
            return not_registered(name)
        try:
            path = (await notebook(name))["path"]
            url = await asyncio.to_thread(launch, Path(path))
        except jupyter.JupyterMissing as e:
            return error(501, str(e), "jupyter_missing", "server_error")
        except jupyter.JupyterFailed as e:
            return error(502, str(e), "jupyter_failed", "server_error")
        except Exception as e:
            return error(502, f"could not build a notebook for {name}: {e}", "learn_failed", "server_error")
        return JSONResponse({"name": name, "path": path, "url": url})

    @app.post("/admin/keys/{app_name}/rotate")
    def admin_key_rotate(request: Request, app_name: str):
        if not admin_only(request):
            return refused()
        try:
            plaintext = keys.rotate_key(store, app_name)
        except keys.KeyNotFound as e:
            return error(404, str(e), "key_not_found")
        return JSONResponse({"app": app_name, "key": plaintext})  # the only time it is shown

    @app.delete("/admin/models/{name}")
    async def admin_model_remove(request: Request, name: str):
        if not admin_only(request):
            return refused()
        if store.get_model(name) is None:
            return not_registered(name)
        purge = request.query_params.get("purge", "") in ("1", "true", "yes")
        await supervisor.stop(name)
        try:
            result = removal.remove(store, name, purge=purge)
        except removal.UnknownModel:  # removed by a concurrent call between the check and here
            return not_registered(name)
        try:  # LiteLLM lists models by name: refresh it so the LAN endpoint stops offering this one
            result["lan_refreshed"] = bool(await asyncio.to_thread(expose.refresh_if_running, store))
        except Exception:
            log.exception("could not refresh LiteLLM after removing %s", name)
            result["lan_refreshed"] = False
        return JSONResponse(result)
