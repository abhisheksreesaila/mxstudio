"""Admin routes added for the 1.0 console (F-024): remove a model, search, rotate a key, learn notebooks.

`register(app, store, supervisor, admin_only, refused=..., error=...)` is called once from
gateway.create_app. Every route is admin-only (loopback + the admin header), like the others.
"""
from __future__ import annotations

import asyncio
import logging

from starlette.requests import Request
from starlette.responses import JSONResponse

from mxconsole import expose, removal
from mxconsole.store import Store
from mxconsole.supervisor import Supervisor

log = logging.getLogger("mxconsole.admin_api")


def register(app, store: Store, supervisor: Supervisor, admin_only, *, refused, error) -> None:
    """`refused()` is the gateway's 403 answer, `error(status, message, code, type_)` its OpenAI-shaped error."""

    def not_registered(name: str) -> JSONResponse:
        return error(404, f"The model `{name}` is not registered.", "model_not_found")

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
