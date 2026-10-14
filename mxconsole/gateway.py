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

from mxconsole import admin_api, api_docs, admin_bench, admin_port, admin_pull, admin_system, expose, insights, keys, metrics, system
from mxconsole.settings import DAEMON_PORT
from mxconsole.store import Store, mxc_home
from mxconsole.supervisor import StartFailed, Supervisor, UnknownModel

log = logging.getLogger("mxconsole.gateway")
ROUTE_NAMES = {"/v1/chat/completions": "chat", "/v1/completions": "completions", "/v1/embeddings": "embeddings"}
PROXIED = ("/v1/chat/completions", "/v1/completions", "/v1/embeddings")
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
ADMIN_HEADER = "x-mst-admin"
CONSOLE_DIR = Path(__file__).parent / "console"
CONSOLE_FONTS = {"geist-variable.woff2", "geist-mono-variable.woff2"}
# An explicit list: the console is never a directory server. Add a file here when you add one to console/.
CONSOLE_SCRIPTS = {"theme.js", "main.js", "core.js", "icons.js", "routes.js",
                   # the blades console: engine, shell and one file per blade, each with its own CSS
                   "blades.js", "blade-layout.js", "blade-stack.js", "keyed.js", "prefs.js", "hardware.js", "system-data.js", "topbar.js", "blade-home.js",
                   "blade-model.js", "blade-add.js", "blade-security.js", "blade-keys.js", "blade-bench.js", "blade-logs.js",
                   "blade-notebook.js", "blade-gateway.js", "blade-settings.js"}
CONSOLE_STYLES = {"blades.css", "topbar.css", "docs.css", "blade-home.css", "blade-model.css", "blade-add.css",
                  "blade-security.css", "blade-keys.css", "blade-bench.css", "blade-logs.css", "blade-notebook.css",
                  "blade-gateway.css", "blade-settings.css"}
CONSOLE_IMAGES = {"favicon.svg"}
CONSOLE_ASSETS = CONSOLE_STYLES | CONSOLE_SCRIPTS | CONSOLE_FONTS | CONSOLE_IMAGES
LOG_TAIL_BYTES = 8000
TRY_MAX_TOKENS = 1024  # the API page's "Try it" is for a quick look, not for load
# no-cache: an upgraded mxconsole never shows a stale console. The rest: no other site can frame the
# console (clickjacking Load/Stop) and the page loads nothing from anywhere but the daemon, except that the
# Notebook blade may frame Jupyter, which only ever listens on 127.0.0.1 (framed as localhost when the console is).
CONSOLE_HEADERS = {"Cache-Control": "no-cache", "X-Frame-Options": "DENY",
                   "Content-Security-Policy": "default-src 'self'; frame-src http://127.0.0.1:* http://localhost:*; "
                                              "frame-ancestors 'none'; base-uri 'none'; form-action 'none'; object-src 'none'",
                   "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}
HOP_BY_HOP = {"content-length", "transfer-encoding", "connection", "content-encoding"}

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
          "404": {"description": "Model not registered: run `mxc pull <name>`"},
          "503": {"description": "The model failed to start; the message has the log tail"}}
    return {
        "openapi": "3.1.0",
        "info": {"title": "mxconsole API", "version": "1.0.0",
                 "description": "OpenAI-compatible endpoint for local models on Modular MAX. "
                                "Point any OpenAI client at `/v1` with a `mxc keys new <app>` key."},
        "servers": [{"url": "/"}],
        "paths": {
            "/v1/models": {"get": {"summary": "List models", "security": secured, "responses": {
                "200": {"description": "Registered models, with load state in the `mxconsole` field",
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
                              "description": "A registered model. Add more with `mxc pull`."},
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


def create_app(store: Store, supervisor: Supervisor, *, reap_interval: float = 15.0, api_deps: dict | None = None):
    """`api_deps` are test doubles: a "system" dict for admin_system.register (GPU readers) and the keyword
    arguments of admin_api.register (search sources, notebook source, Jupyter launcher)."""
    async def lifespan(app):
        async def reaper():
            while True:
                await asyncio.sleep(reap_interval)
                try:
                    metrics.prune(store)
                except Exception:  # a locked database must not stop idle unloading
                    log.exception("pruning request metrics failed")
                try:
                    await supervisor.reap_idle()
                except Exception:  # a failed sweep must not end the loop
                    log.exception("idle sweep failed")

        supervisor.acquire_lock()  # a second daemon must exit before touching the first's children
        supervisor.sweep_orphans()  # max children left behind by a daemon that died
        task = asyncio.create_task(reaper())
        try:
            yield
        finally:
            await stop_bench()  # before the models are stopped: its cleanup talks to the supervisor
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            await supervisor.stop_all()
            await proxy.aclose()

    async def stop_bench() -> None:
        await bench_shutdown[0]()

    bench_shutdown = [lambda: asyncio.sleep(0)]  # replaced once admin_bench registers its routes
    boot = time.monotonic()
    # a literal secret_key stops FastHTML writing a .sesskey file; the gateway uses no sessions
    app = FastHTML(lifespan=lifespan, secret_key="mst-gateway-unused", default_hdrs=False)
    # no read timeout: cold starts and long generations are normal
    proxy = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=10.0))

    def authenticate(request: Request) -> str | None:
        return keys.verify_key(store, _bearer(request))

    def unauthorized() -> JSONResponse:
        return openai_error(401, "Incorrect API key provided. Create one with `mxc keys new <app>`.",
                            "invalid_api_key", "authentication_error")

    def loopback_only(request: Request) -> bool:
        # A proxy (Caddy) connects from loopback but sets X-Forwarded-For for a LAN client.
        proxied = "x-forwarded-for" in request.headers or "forwarded" in request.headers
        # Host guards against DNS rebinding: a web page that rebinds its own name to 127.0.0.1.
        host = request.headers.get("host", "").rsplit(":", 1)[0].strip("[]")
        return (bool(request.client) and request.client.host in LOOPBACK and not proxied
                and host in LOOPBACK)

    counters = system.Counters()  # what only the daemon sees: admin refusals, unknown /v1 routes

    def admin_only(request: Request) -> bool:
        allowed = admin_allowed(request)
        if not allowed:
            counters.bump("admin_refused")
        return allowed

    def admin_allowed(request: Request) -> bool:
        """Admin calls come from the mxc CLI. The custom header can't be sent cross-site without a
        CORS preflight (which the gateway never grants), so browser pages can't reach these routes."""
        origin = request.headers.get("origin")
        origin_ok = origin is None or origin.split("://", 1)[-1].rsplit(":", 1)[0].strip("[]") in LOOPBACK
        return loopback_only(request) and request.headers.get(ADMIN_HEADER) == "1" and origin_ok

    admin_pull.register(app, store, supervisor, admin_only)  # /admin/pulls: background pulls with progress
    admin_port.register(app, store, supervisor, admin_only)  # /admin/ports: automatic ports (claude headless)

    @app.get("/health")
    def health():
        return JSONResponse({"status": "ok", "service": "mst"})

    @app.get("/auth/check")
    def auth_check(request: Request):
        started = time.monotonic()
        name = authenticate(request)
        if name is None:
            note(None, None, "auth_check", 401, started)  # a LAN client Caddy turned away, counted with the rest
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
        data = [{"id": m.name, "object": "model", "created": 0, "owned_by": "mxconsole",
                 "mxconsole": {"state": states.get(m.name, {}).get("state", "cold"), "repo_id": m.repo_id,
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
        return await forward(await request.body(), app_name, route, request.url.path,
                             request.headers.get("content-type", "application/json"), started)

    async def forward(raw: bytes, app_name: str, route: str, path: str, content_type: str, started: float):
        """Send one authenticated /v1 request to its model and stream the answer back."""
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
            return openai_error(404, f"The model `{name}` is not registered. Run `mxc pull {name}`.",
                                "model_not_found")
        except StartFailed as e:
            note(app_name, name, route, 503, started)
            return openai_error(503, f"Model `{name}` failed to start: {e}", "model_unavailable",
                                "server_error")
        started = time.monotonic()  # model latency from here: a cold start isn't counted as TTFT
        in_flight = contextlib.AsyncExitStack()
        await in_flight.enter_async_context(supervisor.request(name))  # never reaped mid-request
        target = base + path.removeprefix("/v1")
        # identity: the body is passed through raw, so it must never arrive compressed
        headers = {"content-type": content_type, "accept-encoding": "identity"}
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
                    try:
                        meter.feed(chunk)
                    except Exception:  # metrics never break a reply
                        log.exception("metrics meter failed")
                    yield chunk
            finally:
                await upstream.aclose()
                await in_flight.aclose()
                if not streamed:
                    try:
                        meter.finish()
                    except Exception:
                        log.exception("metrics meter failed")
                # TTFT (and so decode speed) only means something when tokens arrive one by one
                note(app_name, name, route, upstream.status_code, started, meter=meter,
                     ttft_ms=(first - started) * 1000 if streamed and first is not None else None)

        return StreamingResponse(body_iter(), status_code=upstream.status_code, headers=passthrough)

    for path in PROXIED:
        app.route(path, methods=["POST"])(proxy_route)

    @app.route("/v1/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def unknown_v1(request: Request, path: str):
        counters.bump("unknown_route")
        if authenticate(request) is None:
            return unauthorized()
        return openai_error(404, f"mxconsole does not serve /v1/{path}. Supported: /v1/models, "
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
        return JSONResponse(metrics.recent(store, limit, model=request.query_params.get("model")))

    @app.get("/admin/metrics")
    async def admin_metrics(request: Request):
        if not admin_only(request):
            return admin_refused()
        try:
            window = metrics.range_seconds(request.query_params.get("range", "5m"))
        except ValueError as e:
            return openai_error(400, str(e), "invalid_range")
        out = metrics.summary(store, window=window)
        out["gpus"] = await asyncio.to_thread(metrics.gpus)
        return JSONResponse(out)

    @app.get("/admin/series")
    def admin_series(request: Request):
        if not admin_only(request):
            return admin_refused()
        try:
            return JSONResponse(metrics.series(store, request.query_params.get("range", "1h")))
        except ValueError as e:
            return openai_error(400, str(e), "invalid_range")

    @app.get("/admin/insights")
    def admin_insights(request: Request):
        if not admin_only(request):
            return admin_refused()
        return JSONResponse(insights.compute(store, supervisor.status()))

    def admin_refused() -> Response:
        return Response("admin is only served to this machine", status_code=403)

    # -- keys -----------------------------------------------------------------------------------
    def key_row(k: keys.KeyInfo) -> dict:
        return {"app": k.app, "prefix": k.prefix, "created_at": k.created_at, "last_used_at": k.last_used_at,
                "revoked_at": k.revoked_at, "status": "revoked" if k.revoked_at else "active"}

    @app.get("/admin/keys")
    def admin_keys(request: Request):
        if not admin_only(request):
            return admin_refused()
        return JSONResponse([key_row(k) for k in keys.list_keys(store) if not k.app.startswith("_")])

    @app.post("/admin/keys")
    async def admin_key_new(request: Request):
        if not admin_only(request):
            return admin_refused()
        try:
            body = json.loads(await request.body())
            app_name = body["app"]
            if not isinstance(app_name, str):
                raise TypeError
            plaintext = keys.create_key(store, app_name)
        except keys.KeyExists as e:
            return openai_error(409, str(e), "key_exists")
        except (ValueError, KeyError, TypeError) as e:
            msg = str(e) if isinstance(e, ValueError) and "app name" in str(e) else \
                "Send JSON like {\"app\": \"my-app\"}: letters, digits, '.', '_' or '-'."
            return openai_error(400, msg, "invalid_request_body")
        return JSONResponse({"app": app_name, "key": plaintext}, status_code=201)  # the only time it is shown

    @app.delete("/admin/keys/{app_name}")
    def admin_key_revoke(request: Request, app_name: str):
        if not admin_only(request):
            return admin_refused()
        if app_name.startswith("_") or not keys.revoke_key(store, app_name):
            return openai_error(404, f"There is no active key for `{app_name}`.", "key_not_found")
        return JSONResponse({"app": app_name, "status": "revoked"})

    # -- network --------------------------------------------------------------------------------
    @app.get("/admin/network")
    def admin_network(request: Request):
        if not admin_only(request):
            return admin_refused()
        now = time.time()
        live = {row["name"]: row for row in supervisor.status()}
        recent = store.db.execute("SELECT model, COUNT(*) AS n FROM requests WHERE ts >= ? GROUP BY model",
                                  (now - 60,)).fetchall()
        per_model = {r["model"]: r["n"] for r in recent}
        apps = store.db.execute(
            """SELECT app, COUNT(*) AS requests, MAX(ts) AS last_ts FROM requests
               WHERE ts >= ? AND app IS NOT NULL AND app NOT LIKE '\\_%' ESCAPE '\\' AND app != 'console'
               GROUP BY app ORDER BY last_ts DESC LIMIT 8""", (now - 86400,)).fetchall()
        status = expose.status()
        return JSONResponse({
            "host": socket.gethostname(), "expose": status, "exposed": status["caddy"],
            "lan_endpoint": expose.lan_endpoint(), "default_host": expose.default_host(),
            "ca_path": str(expose.ca_root_path()), "ca_exists": expose.ca_root_path().exists(),
            "ports": {"daemon": DAEMON_PORT, "litellm": expose.LITELLM_PORT, "caddy": expose.EXPOSE_PORT},
            "models": [{"name": m.name, "state": live.get(m.name, {}).get("state", "cold"),
                        "port": live.get(m.name, {}).get("port"), "recent": per_model.get(m.name, 0)}
                       for m in store.models()],
            "apps": [dict(a) for a in apps],
            "recent_requests": sum(per_model.values()),
        })

    @app.post("/admin/expose")
    async def admin_expose(request: Request):
        if not admin_only(request):
            return admin_refused()
        try:
            body = json.loads(await request.body() or b"{}")
        except ValueError:
            body = None
        host = body.get("host", expose.default_host()) if isinstance(body, dict) else None
        litellm = body.get("litellm", True) if isinstance(body, dict) else None
        if not isinstance(host, str) or not isinstance(litellm, bool):
            return openai_error(400, "Send JSON like {\"host\": \"box.local\", \"litellm\": true} (both optional).",
                                "invalid_request_body")
        try:
            # starting LiteLLM takes seconds: keep the event loop free
            await asyncio.to_thread(expose.start, host=host, litellm=litellm, store=store)
        except ValueError as e:
            return openai_error(400, str(e), "invalid_host")
        except expose.ExposeError as e:
            return openai_error(500, str(e), "expose_failed", "server_error")
        return JSONResponse({"exposed": True, "lan_endpoint": expose.lan_endpoint(), "host": host})

    @app.post("/admin/expose/stop")
    async def admin_expose_stop(request: Request):
        if not admin_only(request):
            return admin_refused()
        await asyncio.to_thread(expose.stop, store=store)
        return JSONResponse({"exposed": False})

    # -- try it ---------------------------------------------------------------------------------
    @app.post("/admin/try/chat")
    async def admin_try_chat(request: Request):
        """The API page's "Try it": one chat request sent as the console, so the browser needs no app key."""
        if not admin_only(request):
            return admin_refused()
        raw = await request.body()
        try:
            body = json.loads(raw)
            ok = (isinstance(body, dict) and isinstance(body.get("model"), str) and isinstance(body.get("messages"), list)
                  and body["messages"] and type(body.get("max_tokens", 1)) is int and body.get("max_tokens", 1) > 0)
            if ok:  # the cap is always enforced: missing is filled in, too large is clamped, the newer alias is dropped
                body["max_tokens"] = min(body.get("max_tokens", TRY_MAX_TOKENS), TRY_MAX_TOKENS)
                body.pop("max_completion_tokens", None)
                body["n"] = 1  # one completion per try, so the cap really bounds the work
                raw = json.dumps(body).encode()
        except ValueError:
            ok = False
        if not ok:
            return openai_error(400, f"Send JSON with `model`, a non-empty `messages` list and optionally "
                                     f"`max_tokens` (a positive integer, capped at {TRY_MAX_TOKENS}) and `stream`.", "invalid_request_body")
        return await forward(raw, "console", "chat", "/v1/chat/completions", "application/json", time.monotonic())

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
        path = mxc_home() / "logs" / f"{name}.log"
        try:
            with open(path, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - LOG_TAIL_BYTES))
                tail = f.read().decode(errors="replace")
                cut = size > LOG_TAIL_BYTES
        except FileNotFoundError:
            tail, cut = "", False
        return Response(tail, media_type="text/plain; charset=utf-8", headers={"X-Log-Truncated": "1" if cut else "0"})

    @app.get("/admin/info")
    def admin_info(request: Request):
        if not admin_only(request):
            return admin_refused()
        base = f"http://{request.headers.get('host', '127.0.0.1')}"
        from mxconsole.port import mst_max_version
        active = sum(1 for k in keys.list_keys(store) if k.revoked_at is None and not k.app.startswith("_"))
        today = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))  # local midnight
        return JSONResponse({"host": socket.gethostname(), "endpoint": f"{base}/v1", "docs": f"{base}/docs",
                             "lan_endpoint": expose.lan_endpoint(),
                             "max_version": mst_max_version(), "uptime_seconds": round(time.monotonic() - boot),
                             "active_keys": active, "blocked_today": metrics.count_status(store, 401, today)})

    bench_shutdown[0] = admin_bench.register(app, store, supervisor, admin_only)

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
        media = "font/woff2" if asset in CONSOLE_FONTS else "image/svg+xml" if asset in CONSOLE_IMAGES else None
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
            return openai_error(404, f"The model `{name}` is not registered. Run `mxc pull {name}`.",
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
        return HTMLResponse(api_docs.render(openapi_spec(sorted(m.name for m in store.models())), str(request.base_url).rstrip("/")),
                            headers=CONSOLE_HEADERS)

    api_deps = dict(api_deps or {})
    admin_system.register(app, store, supervisor, admin_only, counters=counters,
                          refused=admin_refused, error=openai_error, **api_deps.pop("system", {}))
    admin_api.register(app, store, supervisor, admin_only, refused=admin_refused, error=openai_error, **api_deps)
    return app
