"""The daemon's automatic-port routes: GET/POST /admin/ports, GET /admin/ports/{id}, POST /admin/ports/{id}/cancel.

`register(app, store, supervisor, admin_only)` is called once from gateway.create_app. Every route
refuses anything that is not an admin call from this machine, like the other /admin routes.
"""
from __future__ import annotations

import asyncio
import json

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from mxconsole.port_auto import DEFAULT_ATTEMPTS, PortBusy, PortManager, cleanup_at_exit
from mxconsole.pulls import BadPull

MAX_BODY = 4096
SERVE_TIMEOUT = 40 * 60


def _error(status: int, message: str, code: str, **extra) -> JSONResponse:
    body = {"error": {"message": message, "type": "invalid_request_error", "param": None, "code": code}}
    return JSONResponse({**body, **extra}, status_code=status)


def register(app, store, supervisor, admin_only, *, manager: PortManager | None = None) -> PortManager:
    holder: dict = {}

    def serve(name: str) -> None:
        """Called from the job's thread: start the model on the daemon's own event loop."""
        loop = holder.get("loop")
        if loop is None:
            return
        asyncio.run_coroutine_threadsafe(supervisor.ensure_running(name), loop).result(SERVE_TIMEOUT)

    if manager is None:
        manager = PortManager(store, max_command=supervisor.max_command, serve=serve)

    cleanup_at_exit(manager)  # a stopping daemon must not leave an agent running

    def refused() -> Response:
        return Response("admin is only served to this machine", status_code=403)

    @app.get("/admin/ports")
    def ports_list(request: Request):
        if not admin_only(request):
            return refused()
        return JSONResponse({"ports": manager.list()})

    @app.post("/admin/ports")
    async def ports_start(request: Request):
        if not admin_only(request):
            return refused()
        holder["loop"] = asyncio.get_running_loop()
        raw = b""
        async for chunk in request.stream():
            raw += chunk
            if len(raw) > MAX_BODY:
                return _error(413, "That request is too large for a port.", "request_too_large")
        try:
            body = json.loads(raw)
        except ValueError:
            body = None
        name = body.get("name") if isinstance(body, dict) else None
        attempts = body.get("max_attempts", DEFAULT_ATTEMPTS) if isinstance(body, dict) else None
        if not isinstance(body, dict) or not isinstance(body.get("ref"), str) or not (name is None or isinstance(name, str)):
            return _error(400, 'Send JSON like {"ref": "owner/repo", "max_attempts": 3}.', "invalid_request_body")
        try:
            job = manager.start(body["ref"], name or None, attempts)
        except BadPull as e:
            return _error(400, str(e), "invalid_request_body")
        except PortBusy as e:
            return _error(409, f"{e.job['ref']} is already being ported.", "port_in_progress", port=e.job)
        return JSONResponse(job, status_code=202)

    @app.get("/admin/ports/{port_id}")
    def ports_get(request: Request, port_id: int):
        if not admin_only(request):
            return refused()
        job = manager.get(port_id)
        if job is None:
            return _error(404, f"There is no port number {port_id}.", "port_not_found")
        return JSONResponse(job)

    @app.post("/admin/ports/{port_id}/cancel")
    def ports_cancel(request: Request, port_id: int):
        if not admin_only(request):
            return refused()
        job = manager.cancel(port_id)
        if job is None:
            return _error(404, f"There is no port number {port_id}.", "port_not_found")
        return JSONResponse(job)

    return manager
