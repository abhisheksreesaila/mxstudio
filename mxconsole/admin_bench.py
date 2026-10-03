"""Admin routes for benchmarks: start one in the background, read the latest run and its progress, history."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from mxconsole import bench, keys

log = logging.getLogger("mxconsole.bench")


def _error(status: int, message: str, code: str) -> JSONResponse:
    return JSONResponse({"error": {"message": message, "type": "invalid_request_error", "param": None, "code": code}},
                        status_code=status)


def register(app, store, supervisor, admin_only, *, http_factory=None):
    """Add the bench routes. Returns `shutdown`, a coroutine function that cancels a running benchmark and
    waits for it, so its cleanup (revoking the key, stopping the models it loaded) runs."""
    bench.ensure_schema(store)
    bench.mark_interrupted(store)
    keys.revoke_key(store, bench.BENCH_APP)  # a daemon that died mid-run left the key active
    state: dict = {"task": None, "progress": None}

    def refused() -> Response:
        return Response("admin is only served to this machine", status_code=403)

    def running() -> bool:
        return state["task"] is not None and not state["task"].done()

    @app.post("/admin/bench")
    async def admin_bench_start(request: Request):
        if not admin_only(request):
            return refused()
        try:
            body = json.loads(await request.body() or b"{}")
        except ValueError:
            body = None
        names, runs = None, bench.DEFAULT_RUNS
        if isinstance(body, dict) and set(body) <= {"models", "runs"}:
            names, runs = body.get("models"), body.get("runs", bench.DEFAULT_RUNS)
        valid = (isinstance(body, dict) and set(body) <= {"models", "runs"}
                 and type(runs) is int and 1 <= runs <= bench.MAX_RUNS
                 and (names is None or (isinstance(names, list) and names and all(isinstance(n, str) for n in names))))
        if not valid:
            return _error(400, f"Send JSON like {{\"models\": [\"qwen2.5-0.5b\"], \"runs\": 5}}: `models` is optional "
                               f"(default: every registered model), `runs` is 1 to {bench.MAX_RUNS}.", "invalid_request_body")
        registered = [m.name for m in store.models()]
        names = list(dict.fromkeys(names)) if names else registered
        if not names:
            return _error(400, "There are no models to benchmark. Add one with `mxc pull <name>`.", "no_models")
        unknown = [n for n in names if n not in registered]
        if unknown:
            return _error(404, f"The model `{unknown[0]}` is not registered. See `mxc ls`.", "model_not_found")
        if running():
            return _error(409, "A benchmark is already running. Wait for it to finish.", "bench_running")
        progress = bench.start_run(store, names, runs)
        state["progress"] = progress

        async def go():
            try:
                await bench.run_all(store, supervisor, progress, http_factory=http_factory)
            except asyncio.CancelledError:
                raise
            except Exception:  # the run row is already marked by run_all
                log.exception("benchmark failed")

        state["task"] = asyncio.create_task(go())
        return JSONResponse({"id": progress.run_id, "models": names, "runs": runs}, status_code=202)

    # async: cheap reads on the loop thread. Since F-022 every thread has its own SQLite connection,
    # so sync would be safe too.
    @app.get("/admin/bench")
    async def admin_bench_latest(request: Request):
        if not admin_only(request):
            return refused()
        live = running()
        return JSONResponse({"running": live, "progress": state["progress"].as_dict() if live else None,
                             "latest": bench.latest(store)})

    @app.get("/admin/bench/history")
    async def admin_bench_history(request: Request):
        if not admin_only(request):
            return refused()
        try:
            limit = max(1, min(100, int(request.query_params.get("limit", "20"))))
        except ValueError:
            limit = 20
        return JSONResponse(bench.history(store, limit))

    async def shutdown() -> None:
        task = state["task"]
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    return shutdown
