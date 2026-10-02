"""The gateway: one OpenAI-compatible endpoint in front of every model, with per-app keys."""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import socket
import time
from pathlib import Path

import httpx
import logging
from fasthtml.common import FastHTML
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse

from mst import keys, metrics
from mst.store import Store, mst_home
from mst.supervisor import StartFailed, Supervisor, UnknownModel

log = logging.getLogger("mst.gateway")
ROUTE_NAMES = {"/v1/chat/completions": "chat", "/v1/completions": "completions", "/v1/embeddings": "embeddings"}
PROXIED = ("/v1/chat/completions", "/v1/completions", "/v1/embeddings")
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
ADMIN_HEADER = "x-mst-admin"
CONSOLE_DIR = Path(__file__).parent / "console"
CONSOLE_FONTS = {"source-sans-3-400.woff2", "source-sans-3-600.woff2",
                 "source-code-pro-400.woff2", "source-code-pro-500.woff2"}
CONSOLE_ASSETS = {"console.css", "console.js"} | CONSOLE_FONTS
LOG_TAIL_BYTES = 8000
# no-cache: an upgraded mst never shows a stale console. The rest: no other site can frame the
# console (clickjacking Load/Stop) and the page loads nothing from anywhere but the daemon.
CONSOLE_HEADERS = {"Cache-Control": "no-cache", "X-Frame-Options": "DENY",
                   "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
                                              "form-action 'none'; object-src 'none'",
                   "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}
HOP_BY_HOP = {"content-length", "transfer-encoding", "connection", "content-encoding"}

SCALAR_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>MX Studio API</title>
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body><div id="app"></div>
<script src="https://cdn.jsdelivr.net/npm/@scalar/api-reference"></script>
<script>Scalar.createApiReference('#app', {url: '/openapi.json'})</script>
</body></html>"""


def openai_error(status: int, message: str, code: str, type_: str = "invalid_request_error") -> JSONResponse:
    return JSONResponse({"error": {"message": message, "type": type_, "param": None, "code": code}},
                        status_code=status)


def _bearer(request: Request) -> str:
    value = request.headers.get("authorization", "")
    scheme, _, token = value.partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


def openapi_spec(model_names: list[str]) -> dict:
    secured = [{"bearerAuth": []}]

    def ref(name):
        return {"$ref": f"#/components/schemas/{name}"}

    def body(schema):
        return {"required": True, "content": {"application/json": {"schema": ref(schema)}}}

    ok = {"200": {"description": "OpenAI-compatible response (an SSE stream when `stream` is true)",
                  "content": {"application/json": {"schema": {"type": "object"}}}},
          "401": {"description": "Missing or invalid key"},
          "404": {"description": "Model not registered: run `mst pull <name>`"},
          "503": {"description": "The model failed to start; the message has the log tail"}}
    return {
        "openapi": "3.1.0",
        "info": {"title": "MX Studio API", "version": "1.0.0",
                 "description": "OpenAI-compatible endpoint for local models on Modular MAX. "
                                "Point any OpenAI client at `/v1` with an `mst keys new <app>` key."},
        "servers": [{"url": "/"}],
        "paths": {
            "/v1/models": {"get": {"summary": "List models", "security": secured, "responses": {
                "200": {"description": "Registered models, with load state in the `mst` field",
                        "content": {"application/json": {"schema": {"type": "object"}}}},
                "401": {"description": "Missing or invalid key"}}}},
            "/v1/chat/completions": {"post": {"summary": "Create a chat completion", "security": secured,
                                              "requestBody": body("ChatRequest"), "responses": ok}},
            "/v1/completions": {"post": {"summary": "Create a text completion", "security": secured,
                                         "requestBody": body("CompletionRequest"), "responses": ok}},
            "/v1/embeddings": {"post": {"summary": "Create embeddings", "security": secured,
                                        "requestBody": body("EmbeddingRequest"), "responses": ok}},
            "/health": {"get": {"summary": "Liveness, no key needed",
                                "responses": {"200": {"description": "ok"}}}},
            "/auth/check": {"get": {"summary": "Key check for a reverse proxy (Caddy forward_auth)",
                                    "security": secured,
                                    "responses": {"200": {"description": "Valid key; app name in X-Mst-App"},
                                                  "401": {"description": "Invalid key"}}}},
        },
        "components": {
            "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}},
            "schemas": {
                "ModelName": {"type": "string", "enum": model_names,
                              "description": "A registered model. Add more with `mst pull`."},
                "ChatRequest": {"type": "object", "required": ["model", "messages"], "properties": {
                    "model": ref("ModelName"),
                    "messages": {"type": "array", "items": {
                        "type": "object", "required": ["role", "content"],
                        "properties": {"role": {"type": "string"}, "content": {"type": "string"}}}},
                    "stream": {"type": "boolean", "default": False},
                    "max_tokens": {"type": "integer"}, "temperature": {"type": "number"}}},
                "CompletionRequest": {"type": "object", "required": ["model", "prompt"], "properties": {
                    "model": ref("ModelName"), "prompt": {"type": "string"},
                    "stream": {"type": "boolean", "default": False}, "max_tokens": {"type": "integer"}}},
                "EmbeddingRequest": {"type": "object", "required": ["model", "input"], "properties": {
                    "model": ref("ModelName"), "input": {"type": ["string", "array"]}}},
            },
        },
    }


def create_app(store: Store, supervisor: Supervisor, *, reap_interval: float = 15.0):
    async def lifespan(app):
        async def reaper():
            while True:
                await asyncio.sleep(reap_interval)
                try:
                    metrics.prune(store)
                    await supervisor.reap_idle()
                except Exception:  # a failed sweep must not end the loop
                    log.exception("idle sweep failed")

        supervisor.acquire_lock()  # a second daemon must exit before touching the first's children
        supervisor.sweep_orphans()  # max children left behind by a daemon that died
        task = asyncio.create_task(reaper())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            await supervisor.stop_all()
            await proxy.aclose()

    boot = time.monotonic()
    # a literal secret_key stops FastHTML writing a .sesskey file; the gateway uses no sessions
    app = FastHTML(lifespan=lifespan, secret_key="mst-gateway-unused", default_hdrs=False)
    # no read timeout: cold starts and long generations are normal
    proxy = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=10.0))

    def authenticate(request: Request) -> str | None:
        return keys.verify_key(store, _bearer(request))

    def unauthorized() -> JSONResponse:
        return openai_error(401, "Incorrect API key provided. Create one with `mst keys new <app>`.",
                            "invalid_api_key", "authentication_error")

    def loopback_only(request: Request) -> bool:
        # A proxy (Caddy) connects from loopback but sets X-Forwarded-For for a LAN client.
        proxied = "x-forwarded-for" in request.headers or "forwarded" in request.headers
        # Host guards against DNS rebinding: a web page that rebinds its own name to 127.0.0.1.
        host = request.headers.get("host", "").rsplit(":", 1)[0].strip("[]")
        return (bool(request.client) and request.client.host in LOOPBACK and not proxied
                and host in LOOPBACK)

    def admin_only(request: Request) -> bool:
        """Admin calls come from the mst CLI. The custom header can't be sent cross-site without a
        CORS preflight (which the gateway never grants), so browser pages can't reach these routes."""
        origin = request.headers.get("origin")
        origin_ok = origin is None or origin.split("://", 1)[-1].rsplit(":", 1)[0].strip("[]") in LOOPBACK
        return loopback_only(request) and request.headers.get(ADMIN_HEADER) == "1" and origin_ok

    @app.get("/health")
    def health():
        return JSONResponse({"status": "ok", "service": "mst"})

    @app.get("/auth/check")
    def auth_check(request: Request):
        name = authenticate(request)
        if name is None:
            return Response(status_code=401)
        return Response(status_code=200, headers={"X-Mst-App": name})

    @app.get("/v1/models")
    def list_models(request: Request):
        started = time.monotonic()
        app_name = authenticate(request)
        if app_name is None:
            note(None, None, "models", 401, started)
            return unauthorized()
        note(app_name, None, "models", 200, started)
        states = {s["name"]: s for s in supervisor.status()}
        data = [{"id": m.name, "object": "model", "created": 0, "owned_by": "mst",
                 "mst": {"state": states.get(m.name, {}).get("state", "cold"), "repo_id": m.repo_id,
                         "pinned": m.pinned, "keep_alive": m.keep_alive}}
                for m in store.models()]
        return JSONResponse({"object": "list", "data": data})

    async def proxy_route(request: Request):
        started = time.monotonic()
        route = ROUTE_NAMES[request.url.path]
        app_name = authenticate(request)
        if app_name is None:
            note(None, None, route, 401, started)
            return unauthorized()
        raw = await request.body()
        try:
            body = json.loads(raw)
            name = body["model"]
            if not isinstance(name, str):
                raise TypeError
        except (ValueError, KeyError, TypeError):
            note(app_name, None, route, 400, started)
            return openai_error(400, "Request body must be JSON with a string `model` field.",
                                "invalid_request_body")
        try:
            base = await supervisor.ensure_running(name)
        except UnknownModel:
            note(app_name, name, route, 404, started)
            return openai_error(404, f"The model `{name}` is not registered. Run `mst pull {name}`.",
                                "model_not_found")
        except StartFailed as e:
            note(app_name, name, route, 503, started)
            return openai_error(503, f"Model `{name}` failed to start: {e}", "model_unavailable",
                                "server_error")
        in_flight = contextlib.AsyncExitStack()
        await in_flight.enter_async_context(supervisor.request(name))  # never reaped mid-request
        target = base + request.url.path.removeprefix("/v1")
        # identity: the body is passed through raw, so it must never arrive compressed
        headers = {"content-type": request.headers.get("content-type", "application/json"),
                   "accept-encoding": "identity"}
        try:
            upstream = await proxy.send(
                proxy.build_request("POST", target, content=raw, headers=headers), stream=True)
        except httpx.TransportError as e:
            await in_flight.aclose()
            note(app_name, name, route, 502, started)
            return openai_error(502, f"Model `{name}` is not answering: {e}", "bad_gateway", "server_error")

        passthrough = {k: v for k, v in upstream.headers.items() if k.lower() not in HOP_BY_HOP}

        streamed = "text/event-stream" in upstream.headers.get("content-type", "")
        meter = metrics.StreamMeter() if streamed else metrics.JsonMeter()

        async def body_iter():
            first: float | None = None
            try:
                async for chunk in upstream.aiter_raw():
                    if first is None:
                        first = time.monotonic()
                    meter.feed(chunk)
                    yield chunk
            finally:
                await upstream.aclose()
                await in_flight.aclose()
                if not streamed:
                    meter.finish()
                # TTFT (and so decode speed) only means something when tokens arrive one by one
                note(app_name, name, route, upstream.status_code, started, meter=meter,
                     ttft_ms=(first - started) * 1000 if streamed and first is not None else None)

        return StreamingResponse(body_iter(), status_code=upstream.status_code, headers=passthrough)

    for path in PROXIED:
        app.route(path, methods=["POST"])(proxy_route)

    @app.route("/v1/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def unknown_v1(request: Request, path: str):
        if authenticate(request) is None:
            return unauthorized()
        return openai_error(404, f"mst does not serve /v1/{path}. Supported: /v1/models, "
                                 + ", ".join(PROXIED) + ".", "unknown_route")

    def note(app_name, model, route, status, started, meter=None, ttft_ms=None):
        """Record one /v1 request for the console. A metrics failure never fails the request."""
        try:
            metrics.record(store, app=app_name, model=model, route=route, status=status,
                           prompt_tokens=meter.prompt_tokens if meter else None,
                           completion_tokens=meter.completion_tokens if meter else None,
                           ttft_ms=ttft_ms, duration_ms=(time.monotonic() - started) * 1000,
                           estimated=bool(meter and meter.estimated))
        except Exception:
            log.exception("could not record request metrics")

    @app.get("/admin/requests")
    def admin_requests(request: Request):
        if not admin_only(request):
            return admin_refused()
        try:
            limit = max(1, min(500, int(request.query_params.get("limit", "50"))))
        except ValueError:
            limit = 50
        return JSONResponse(metrics.recent(store, limit))

    @app.get("/admin/metrics")
    async def admin_metrics(request: Request):
        if not admin_only(request):
            return admin_refused()
        out = metrics.summary(store, window=300)
        out["gpus"] = await asyncio.to_thread(metrics.gpus)
        return JSONResponse(out)

    def admin_refused() -> Response:
        return Response("admin is only served to this machine", status_code=403)

    def model_row(model, live: dict) -> dict:
        return {"name": model.name, "repo_id": model.repo_id, "revision": model.revision,
                "source": model.source, "architecture": model.architecture, "pinned": model.pinned,
                "keep_alive": model.keep_alive, "state": live.get("state", "cold"),
                "port": live.get("port"), "idle_seconds": live.get("idle_seconds"),
                "starting_seconds": live.get("starting_seconds"),
                "in_flight": live.get("in_flight", 0)}

    @app.get("/admin/models")
    def admin_models(request: Request):
        if not admin_only(request):
            return admin_refused()
        live = {row["name"]: row for row in supervisor.status()}
        return JSONResponse([model_row(m, live.get(m.name, {})) for m in store.models()])

    @app.patch("/admin/models/{name}")
    async def admin_patch(request: Request, name: str):
        if not admin_only(request):
            return admin_refused()
        if store.get_model(name) is None:
            return openai_error(404, f"The model `{name}` is not registered.", "model_not_found")
        try:
            body = json.loads(await request.body())
        except ValueError:
            body = None
        allowed = {"pinned": bool, "keep_alive": int}
        if (not isinstance(body, dict) or not body or set(body) - set(allowed)
                or any(type(v) is not allowed[k] for k, v in body.items())
                or body.get("keep_alive", 0) < 0):
            return openai_error(400, "Send JSON with `pinned` (true/false) and/or `keep_alive` "
                                     "(seconds, 0 or more).", "invalid_request_body")
        store.set_lifecycle(name, pinned=body.get("pinned"), keep_alive=body.get("keep_alive"))
        live = {row["name"]: row for row in supervisor.status()}
        return JSONResponse(model_row(store.get_model(name), live.get(name, {})))

    @app.get("/admin/models/{name}/log")
    def admin_log(request: Request, name: str):
        if not admin_only(request):
            return admin_refused()
        if store.get_model(name) is None:
            return openai_error(404, f"The model `{name}` is not registered.", "model_not_found")
        path = mst_home() / "logs" / f"{name}.log"
        try:
            with open(path, "rb") as f:
                f.seek(0, os.SEEK_END)
                f.seek(max(0, f.tell() - LOG_TAIL_BYTES))
                tail = f.read().decode(errors="replace")
        except FileNotFoundError:
            tail = ""
        return Response(tail, media_type="text/plain; charset=utf-8")

    @app.get("/admin/info")
    def admin_info(request: Request):
        if not admin_only(request):
            return admin_refused()
        base = f"http://{request.headers.get('host', '127.0.0.1')}"
        from mst import expose
        from mst.port import mst_max_version
        exposed = expose.status()["caddy"]
        try:
            lan_host = (expose.expose_dir() / "host").read_text().strip() if exposed else None
        except OSError:
            lan_host = None
        active = sum(1 for k in keys.list_keys(store) if k.revoked_at is None and not k.app.startswith("_"))
        return JSONResponse({"host": socket.gethostname(), "endpoint": f"{base}/v1", "docs": f"{base}/docs",
                             "lan_endpoint": f"https://{lan_host}:{expose.EXPOSE_PORT}/v1" if lan_host else None,
                             "max_version": mst_max_version(), "uptime_seconds": round(time.monotonic() - boot),
                             "active_keys": active})

    @app.get("/favicon.ico")
    def favicon():
        return Response(status_code=204)  # browsers ask for it; an empty answer keeps their console clean

    @app.get("/console")
    def console_page(request: Request):
        if not loopback_only(request):
            return Response("the console is only served to this machine", status_code=403)
        return FileResponse(CONSOLE_DIR / "index.html", headers=CONSOLE_HEADERS)

    @app.get("/console/{asset}")
    def console_asset(request: Request, asset: str):
        if not loopback_only(request):
            return Response("the console is only served to this machine", status_code=403)
        if asset not in CONSOLE_ASSETS:  # a fixed list: no path ever reaches the filesystem
            return Response("not found", status_code=404)
        media = "font/woff2" if asset in CONSOLE_FONTS else None
        return FileResponse(CONSOLE_DIR / asset, headers=CONSOLE_HEADERS, media_type=media)

    @app.get("/admin/status")
    def admin_status(request: Request):
        if not admin_only(request):
            return Response("admin is only served to this machine", status_code=403)
        return JSONResponse(supervisor.status())

    @app.post("/admin/models/{name}/load")
    async def admin_load(request: Request, name: str):
        if not admin_only(request):
            return Response("admin is only served to this machine", status_code=403)
        try:
            base = await supervisor.ensure_running(name)
        except UnknownModel:
            return openai_error(404, f"The model `{name}` is not registered. Run `mst pull {name}`.",
                                "model_not_found")
        except StartFailed as e:
            return openai_error(503, f"Model `{name}` failed to start: {e}", "model_unavailable", "server_error")
        return JSONResponse({"name": name, "upstream": base})

    @app.post("/admin/models/{name}/stop")
    async def admin_stop(request: Request, name: str):
        if not admin_only(request):
            return Response("admin is only served to this machine", status_code=403)
        if store.get_model(name) is None:
            return openai_error(404, f"The model `{name}` is not registered.", "model_not_found")
        await supervisor.stop(name)
        return JSONResponse({"name": name, "state": "cold"})

    @app.post("/admin/shutdown")
    async def admin_shutdown(request: Request):
        if not admin_only(request):
            return Response("admin is only served to this machine", status_code=403)
        await supervisor.stop_all()
        asyncio.get_running_loop().call_later(0.2, lambda: os.kill(os.getpid(), signal.SIGTERM))
        return JSONResponse({"stopping": True})

    @app.get("/openapi.json")
    def openapi(request: Request):
        if not loopback_only(request):
            return Response("docs are only served to this machine", status_code=403)
        return JSONResponse(openapi_spec(sorted(m.name for m in store.models())))

    @app.get("/docs")
    def docs(request: Request):
        if not loopback_only(request):
            return Response("docs are only served to this machine", status_code=403)
        return HTMLResponse(SCALAR_PAGE)

    return app
