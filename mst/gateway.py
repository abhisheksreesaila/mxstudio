"""The gateway: one OpenAI-compatible endpoint in front of every model, with per-app keys."""
from __future__ import annotations

import asyncio
import contextlib
import json

import httpx
from fasthtml.common import FastHTML
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

from mst import keys
from mst.store import Store
from mst.supervisor import StartFailed, Supervisor, UnknownModel

PROXIED = ("/v1/chat/completions", "/v1/completions", "/v1/embeddings")
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
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
                    await supervisor.reap_idle()
                except Exception:  # a failed sweep must not end the loop
                    pass

        task = asyncio.create_task(reaper())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            await supervisor.stop_all()

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
        return bool(request.client) and request.client.host in LOOPBACK and not proxied

    @app.get("/health")
    def health():
        return JSONResponse({"status": "ok"})

    @app.get("/auth/check")
    def auth_check(request: Request):
        name = authenticate(request)
        if name is None:
            return Response(status_code=401)
        return Response(status_code=200, headers={"X-Mst-App": name})

    @app.get("/v1/models")
    def list_models(request: Request):
        if authenticate(request) is None:
            return unauthorized()
        states = {s["name"]: s for s in supervisor.status()}
        data = [{"id": m.name, "object": "model", "created": 0, "owned_by": "mst",
                 "mst": {"state": states.get(m.name, {}).get("state", "cold"), "repo_id": m.repo_id,
                         "pinned": m.pinned, "keep_alive": m.keep_alive}}
                for m in store.models()]
        return JSONResponse({"object": "list", "data": data})

    async def proxy_route(request: Request):
        if authenticate(request) is None:
            return unauthorized()
        raw = await request.body()
        try:
            body = json.loads(raw)
            name = body["model"]
            if not isinstance(name, str):
                raise TypeError
        except (ValueError, KeyError, TypeError):
            return openai_error(400, "Request body must be JSON with a string `model` field.",
                                "invalid_request_body")
        try:
            base = await supervisor.ensure_running(name)
        except UnknownModel:
            return openai_error(404, f"The model `{name}` is not registered. Run `mst pull {name}`.",
                                "model_not_found")
        except StartFailed as e:
            return openai_error(503, f"Model `{name}` failed to start: {e}", "model_unavailable",
                                "server_error")
        supervisor.touch(name)
        target = base + request.url.path.removeprefix("/v1")
        headers = {"content-type": request.headers.get("content-type", "application/json")}
        try:
            upstream = await proxy.send(
                proxy.build_request("POST", target, content=raw, headers=headers), stream=True)
        except httpx.TransportError as e:
            return openai_error(502, f"Model `{name}` is not answering: {e}", "bad_gateway", "server_error")

        passthrough = {k: v for k, v in upstream.headers.items() if k.lower() not in HOP_BY_HOP}

        async def body_iter():
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await upstream.aclose()
                supervisor.touch(name)

        return StreamingResponse(body_iter(), status_code=upstream.status_code, headers=passthrough)

    for path in PROXIED:
        app.route(path, methods=["POST"])(proxy_route)

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
