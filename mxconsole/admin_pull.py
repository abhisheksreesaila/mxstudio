"""The daemon's pull routes: GET/POST /admin/pulls, GET /admin/pulls/{id}, POST /admin/pulls/{id}/cancel.

`register(app, store, supervisor, admin_only)` is called once from gateway.create_app. Every route
refuses anything that is not an admin call from this machine, like the other /admin routes.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import os
from pathlib import Path

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from mxconsole import resolve
from mxconsole.pulls import BadPull, PullBusy, PullManager

log = logging.getLogger("mxconsole.admin_pull")

# TEST-ONLY hook, so a scratch daemon can pull from a fake Hugging Face (no network, no real model).
# It is off unless MXC_FAKE_HUB is the absolute path of an existing file called exactly `fake_hub.py`
# (tests/mxconsole/fake_hub.py), which must define `make_hub()` and `CATALOG`. Any other value is
# ignored, and a log warning says so, so it cannot be switched on by a stray or misspelled variable.
FAKE_HUB_ENV = "MXC_FAKE_HUB"
FAKE_HUB_FILE = "fake_hub.py"


def _fake_hub():
    value = os.environ.get(FAKE_HUB_ENV)
    if not value:
        return None
    path = Path(value)
    if not (path.is_absolute() and path.name == FAKE_HUB_FILE and path.is_file()):
        log.warning("%s is set but is not the absolute path of a %s: ignoring it", FAKE_HUB_ENV, FAKE_HUB_FILE)
        return None
    spec = importlib.util.spec_from_file_location("mxc_fake_hub", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    log.warning("PULLS USE A FAKE HUGGING FACE (%s=%s): test mode, nothing real is downloaded", FAKE_HUB_ENV, path)
    return module


def _error(status: int, message: str, code: str, **extra) -> JSONResponse:
    body = {"error": {"message": message, "type": "invalid_request_error", "param": None, "code": code}}
    return JSONResponse({**body, **extra}, status_code=status)


def register(app, store, supervisor, admin_only, *, manager: PullManager | None = None) -> PullManager:
    fake = _fake_hub()
    if manager is None:
        manager = PullManager(store, hub_factory=fake.make_hub if fake else None,
                              catalog_loader=(lambda: fake.CATALOG) if fake else None)

    def refused() -> Response:
        return Response("admin is only served to this machine", status_code=403)

    @app.get("/admin/pulls")
    def pulls_list(request: Request):
        if not admin_only(request):
            return refused()
        return JSONResponse({"pulls": manager.list(), "curated": resolve.CURATED})

    @app.post("/admin/pulls")
    async def pulls_start(request: Request):
        if not admin_only(request):
            return refused()
        try:
            body = json.loads(await request.body())
        except ValueError:
            body = None
        name = body.get("name") if isinstance(body, dict) else None
        if not isinstance(body, dict) or not isinstance(body.get("ref"), str) or not (name is None or isinstance(name, str)):
            return _error(400, 'Send JSON like {"ref": "qwen2.5-0.5b", "name": "optional"}.', "invalid_request_body")
        try:
            job = manager.start(body["ref"], name or None)
        except BadPull as e:
            return _error(400, str(e), "invalid_request_body")
        except PullBusy as e:
            return _error(409, f"{e.job['ref']} is already being pulled.", "pull_in_progress", pull=e.job)
        return JSONResponse(job, status_code=202)

    @app.get("/admin/pulls/{pull_id}")
    def pulls_get(request: Request, pull_id: int):
        if not admin_only(request):
            return refused()
        job = manager.get(pull_id)
        if job is None:
            return _error(404, f"There is no pull number {pull_id}.", "pull_not_found")
        return JSONResponse(job)

    @app.post("/admin/pulls/{pull_id}/cancel")
    def pulls_cancel(request: Request, pull_id: int):
        if not admin_only(request):
            return refused()
        job = manager.cancel(pull_id)
        if job is None:
            return _error(404, f"There is no pull number {pull_id}.", "pull_not_found")
        return JSONResponse(job)

    return manager
