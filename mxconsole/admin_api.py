"""Admin routes added for the 1.0 console (F-024): remove a model, search, rotate a key, learn notebooks.

`register(app, store, supervisor, admin_only, refused=..., error=...)` is called once from
gateway.create_app. Every route is admin-only (loopback + the admin header), like the others.
"""
from __future__ import annotations

import asyncio
import json
import logging

from starlette.requests import Request
from starlette.responses import JSONResponse

from pathlib import Path

from mxconsole import expose, jupyter, keys, learn, removal, resolve, search
from mxconsole.kinds import LabelError
from mxconsole.store import Store
from mxconsole.supervisor import Supervisor

log = logging.getLogger("mxconsole.admin_api")

NOTEBOOK_APP = "_notebook"  # the reserved key Jupyter's kernels use to call the daemon


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
        if len(q) > search.MAX_QUERY:
            return error(400, "The search text is too long.", "invalid_query")
        try:  # the catalog and Hugging Face are slow and blocking: keep the event loop free
            return JSONResponse(await asyncio.to_thread(search.search, q, search_sources))
        except resolve.ResolveError as e:
            return error(503, str(e), "search_unavailable", "server_error")

    # Jupyter's kernels call the endpoint with their own key (rotated on each start; an earlier server's key stops)
    launch = jupyter_launcher or jupyter.JupyterLauncher(
        api_key=lambda: keys.create_key(store, NOTEBOOK_APP, rotate=True, internal=True))

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
        return JSONResponse({"name": name, "path": path, "url": url, "embed_url": jupyter.embed_url(url)})

    @app.post("/admin/keys/{app_name}/rotate")
    def admin_key_rotate(request: Request, app_name: str):
        if not admin_only(request):
            return refused()
        try:
            plaintext, revived = keys.rotate_key(store, app_name)
        except keys.KeyNotFound as e:
            return error(404, str(e), "key_not_found")
        return JSONResponse({"app": app_name, "key": plaintext, "revived": revived})  # the only time it is shown

    @app.delete("/admin/models/{name}")
    async def admin_model_remove(request: Request, name: str):
        if not admin_only(request):
            return refused()
        if store.get_model(name) is None:
            return not_registered(name)
        purge = request.query_params.get("purge", "") in ("1", "true", "yes")
        # Unregister first: from here a request for this model gets 404 and cannot respawn a child
        # that the stop below would then miss.
        try:
            model = removal.unregister(store, name)
        except removal.UnknownModel:  # removed by a concurrent call between the check and here
            return not_registered(name)
        await supervisor.stop(name)
        result = removal.cleanup(store, model, purge=purge)
        try:  # LiteLLM lists models by name: refresh it so the LAN endpoint stops offering this one
            result["lan_refreshed"] = bool(await asyncio.to_thread(expose.refresh_if_running, store))
        except Exception:
            log.exception("could not refresh LiteLLM after removing %s", name)
            result["lan_refreshed"] = False
        return JSONResponse(result)

    # ---- your labels (F-054): the library's groups column and tick-and-label bar call these ----

    STATUS = {"label_not_found": 404, "model_not_found": 404, "label_exists": 409}

    async def body_of(request: Request, field: str, kind: type) -> object:
        """The JSON body's `field`, or None when the body is not an object holding one of that type."""
        try:
            body = json.loads(await request.body())
        except ValueError:
            return None
        value = body.get(field) if isinstance(body, dict) else None
        return value if isinstance(value, kind) else None

    def bad_body(field: str, what: str) -> JSONResponse:
        return error(400, f"Send JSON with `{field}` ({what}).", "invalid_request_body")

    def label_error(e: LabelError) -> JSONResponse:
        return error(STATUS.get(e.code, 400), str(e), e.code)

    @app.get("/admin/labels")
    def admin_labels(request: Request):
        if not admin_only(request):
            return refused()
        return JSONResponse([{"name": l.name, "position": l.position, "count": l.count} for l in store.labels()])

    @app.post("/admin/labels")
    async def admin_label_create(request: Request):
        if not admin_only(request):
            return refused()
        name = await body_of(request, "name", str)
        if name is None:
            return bad_body("name", "the new label")
        try:
            store.create_label(name)
        except LabelError as e:
            return label_error(e)
        return JSONResponse({"name": name})

    @app.put("/admin/labels/order")
    async def admin_label_order(request: Request):
        if not admin_only(request):
            return refused()
        names = await body_of(request, "names", list)
        if names is None or not all(isinstance(n, str) for n in names):
            return bad_body("names", "every label, in the order you want")
        try:
            store.reorder_labels(names)
        except LabelError as e:
            return label_error(e)
        return JSONResponse({"names": names})

    @app.patch("/admin/labels/{name}")
    async def admin_label_rename(request: Request, name: str):
        if not admin_only(request):
            return refused()
        new = await body_of(request, "name", str)
        if new is None:
            return bad_body("name", "the new name")
        try:
            store.rename_label(name, new)
        except LabelError as e:
            return label_error(e)
        return JSONResponse({"name": new})

    @app.delete("/admin/labels/{name}")
    def admin_label_delete(request: Request, name: str):
        if not admin_only(request):
            return refused()
        try:
            store.delete_label(name)
        except LabelError as e:
            return label_error(e)
        return JSONResponse({"name": name})

    def on_models(action):
        async def route(request: Request, name: str):
            if not admin_only(request):
                return refused()
            models = await body_of(request, "models", list)
            if models is None or not all(isinstance(m, str) for m in models):
                return bad_body("models", "a list of model names")
            try:
                action(models, [name])
            except LabelError as e:
                return label_error(e)
            return JSONResponse({"name": name, "models": models})
        return route

    app.post("/admin/labels/{name}/add")(on_models(store.add_labels))
    app.post("/admin/labels/{name}/remove")(on_models(store.remove_labels))
