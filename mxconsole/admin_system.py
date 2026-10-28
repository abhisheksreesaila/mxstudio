"""Admin routes for the System pages: ports and the crew, the gateway's safeguards, daemon and Caddy logs."""
from __future__ import annotations

import asyncio
import os
import time

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from mxconsole import expose, keys, metrics, system
from mxconsole.store import Store
from mxconsole.supervisor import Supervisor


def register(app, store: Store, supervisor: Supervisor, admin_only, *, counters: system.Counters, refused,
             gpus=metrics.gpus, gpu_procs=metrics.gpu_processes, ppid=system.parent_pid) -> None:
    """Add the System routes. `admin_only` is the gateway's guard and `refused()` its 403 answer.
    `gpus()`, `gpu_procs()` and `ppid(pid)` are the nvidia-smi and /proc readers, replaceable in tests."""

    def crew_now() -> tuple[list[dict], dict | None, list[dict]]:
        crew = system.collect(supervisor.status(), daemon=os.getpid())
        summary, per_gpu = system.gpu_summary(gpus())
        system.add_vram(crew, gpu_procs() if summary else {}, ppid)
        return crew, summary, per_gpu

    @app.get("/admin/system")
    async def admin_system(request: Request):
        if not admin_only(request):
            return refused()
        crew, summary, per_gpu = await asyncio.to_thread(crew_now)
        names = [m.name for m in store.models()]
        return JSONResponse({"crew": crew, "listeners": system.listeners(crew), "home": str(system.mxc_home()),
                             "logs": system.log_sources(names), "gpu": summary, "gpus": per_gpu})

    @app.get("/admin/gateway")
    def admin_gateway(request: Request):
        if not admin_only(request):
            return refused()
        today = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))  # local midnight
        rows = [k for k in keys.list_keys(store) if not k.app.startswith("_")]
        lan = expose.lan_endpoint()
        return JSONResponse({
            "counters_since": counters.since,
            "tls": {"on": lan is not None, "endpoint": lan},
            "keys": {"active": sum(1 for k in rows if k.revoked_at is None),
                     "revoked": sum(1 for k in rows if k.revoked_at is not None)},
            "unauthorized_today": metrics.count_status(store, 401, today),
            "unknown_routes_today": counters.get("unknown_route"),
            "admin_refused_today": counters.get("admin_refused"),
            # Caddy answers these before the daemon sees them, so the daemon can't count them.
            "enforced_by_caddy": ["dot_segments", "secrets"],
        })

    def log_route(source: str):
        def route(request: Request):
            if not admin_only(request):
                return refused()
            try:
                limit = int(request.query_params.get("bytes", system.DEFAULT_LOG_BYTES))
            except ValueError:
                limit = system.DEFAULT_LOG_BYTES
            return Response(system.read_tail(system.log_path(source), limit), media_type="text/plain; charset=utf-8")
        return route

    for source in ("daemon", "caddy"):
        app.get(f"/admin/{source}/log")(log_route(source))
