import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

from mxconsole import keys
from mxconsole.gateway import create_app
from mxconsole.store import Store
from mxconsole.supervisor import Supervisor

FAKE = str(Path(__file__).parent / "fake_max.py")


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "mst.db")
    s.add_model(name="tiny", repo_id="org/tiny", revision="abc", source="hf", architecture="X")
    s.add_model(name="other", repo_id="org/other", revision="main", source="hf", architecture="X")
    return s


@pytest.fixture
def key(store):
    return keys.create_key(store, "myapp")


@pytest.fixture
async def sup(store):
    s = Supervisor(store, max_command=(sys.executable, FAKE), health_timeout=20)
    yield s
    await s.stop_all()


def client(app, host="127.0.0.1", **kw):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(host, 5000)),
                             base_url="http://127.0.0.1:11435", **kw)  # the Host a local client sends


@pytest.fixture
async def http(store, sup):
    async with client(create_app(store, sup)) as c:
        yield c


def auth(key):
    return {"Authorization": f"Bearer {key}"}


def state(sup, name):
    return next(s for s in sup.status() if s["name"] == name)["state"]


CHAT = {"model": "tiny", "messages": [{"role": "user", "content": "hi"}]}


async def test_health_needs_no_key(http):
    assert (await http.get("/health")).status_code == 200


async def test_v1_get_needs_key(http):
    r = await http.get("/v1/models")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_api_key"
    assert (await http.get("/v1/models", headers=auth("mst-nope"))).status_code == 401


async def test_v1_post_needs_key(http):
    assert (await http.post("/v1/chat/completions", json=CHAT)).status_code == 401


async def test_revoked_key_is_401(http, store, key):
    keys.revoke_key(store, "myapp")
    assert (await http.get("/v1/models", headers=auth(key))).status_code == 401


async def test_models_list_openai_format_with_state(http, key):
    body = (await http.get("/v1/models", headers=auth(key))).json()
    assert body["object"] == "list"
    assert {m["id"]: m["mxconsole"]["state"] for m in body["data"]} == {"tiny": "cold", "other": "cold"}
    assert all(m["object"] == "model" and m["owned_by"] == "omxconsole" for m in body["data"])
    await http.post("/v1/chat/completions", json=CHAT, headers=auth(key))
    body = (await http.get("/v1/models", headers=auth(key))).json()
    assert {m["id"]: m["mxconsole"]["state"] for m in body["data"]}["tiny"] == "ready"


async def test_chat_completion_proxied(http, key):
    r = await http.post("/v1/chat/completions", json=CHAT, headers=auth(key))
    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"] == "hello from tiny"


async def test_completions_and_embeddings_proxied(http, key):
    r = await http.post("/v1/completions", json={"model": "other", "prompt": "x"}, headers=auth(key))
    assert r.json()["choices"][0]["text"] == "hello from other"
    r = await http.post("/v1/embeddings", json={"model": "tiny", "input": "x"}, headers=auth(key))
    assert r.json()["data"][0]["embedding"] == [0.1, 0.2]


async def test_streaming_passthrough(http, key):
    async with http.stream("POST", "/v1/chat/completions", json={**CHAT, "stream": True},
                           headers=auth(key)) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        body = "".join([c async for c in r.aiter_text()])
    lines = [ln for ln in body.splitlines() if ln.startswith("data: ")]
    assert lines[-1] == "data: [DONE]"
    text = "".join(json.loads(ln[6:])["choices"][0]["delta"]["content"] for ln in lines[:-1])
    assert text == "hello from tiny"


async def test_unknown_model_404_names_pull(http, key):
    r = await http.post("/v1/chat/completions", json={**CHAT, "model": "nope"}, headers=auth(key))
    assert r.status_code == 404
    err = r.json()["error"]
    assert err["code"] == "model_not_found" and err["type"] == "invalid_request_error"
    assert "mxc pull nope" in err["message"]


async def test_missing_model_is_400(http, key):
    r = await http.post("/v1/chat/completions", json={"messages": []}, headers=auth(key))
    assert r.status_code == 400
    r = await http.post("/v1/chat/completions", content=b"not json", headers=auth(key))
    assert r.status_code == 400


async def test_start_failure_is_503_with_log_tail(http, key, monkeypatch):
    monkeypatch.setenv("FAKE_MAX_FAIL", "1")
    r = await http.post("/v1/chat/completions", json=CHAT, headers=auth(key))
    assert r.status_code == 503
    assert "cannot load model" in r.json()["error"]["message"]


async def test_auth_check(http, key):
    r = await http.get("/auth/check", headers=auth(key))
    assert r.status_code == 200 and r.headers["x-mst-app"] == "myapp"
    assert (await http.get("/auth/check")).status_code == 401
    assert (await http.get("/auth/check", headers=auth("bad"))).status_code == 401


async def test_openapi_lists_paths_and_registered_models(http):
    spec = (await http.get("/openapi.json")).json()
    assert spec["openapi"].startswith("3.1")
    assert {"/v1/models", "/v1/chat/completions", "/v1/completions", "/v1/embeddings"} <= set(spec["paths"])
    assert sorted(spec["components"]["schemas"]["ModelName"]["enum"]) == ["other", "tiny"]


async def test_docs_only_for_loopback(store, sup):
    async with client(create_app(store, sup), host="192.168.1.20") as c:
        assert (await c.get("/docs")).status_code == 403
        assert (await c.get("/openapi.json")).status_code == 403
        assert (await c.get("/health")).status_code == 200


async def test_docs_refuse_requests_that_came_through_a_proxy(http):
    # Caddy connects from loopback but adds X-Forwarded-For: the client is on the LAN.
    r = await http.get("/docs", headers={"X-Forwarded-For": "192.168.1.20"})
    assert r.status_code == 403


async def test_reaper_runs_and_stop_all_on_shutdown(store, sup, key):
    app = create_app(store, sup, reap_interval=0.05)
    async with app.router.lifespan_context(app):
        async with client(app) as c:
            await c.post("/v1/chat/completions", json=CHAT, headers=auth(key))
        assert state(sup, "tiny") == "ready"
        store.set_lifecycle("tiny", keep_alive=0)
        for _ in range(100):
            if state(sup, "tiny") == "cold":
                break
            await asyncio.sleep(0.05)
        assert state(sup, "tiny") == "cold"
        store.set_lifecycle("tiny", keep_alive=600)
        async with client(app) as c:
            await c.post("/v1/chat/completions", json=CHAT, headers=auth(key))
        assert state(sup, "tiny") == "ready"
    assert state(sup, "tiny") == "cold"


async def test_real_openai_client(store, sup, key):
    import openai
    async with client(create_app(store, sup)) as c:
        oc = openai.AsyncOpenAI(base_url="http://mst/v1", api_key=key, http_client=c)
        models = await oc.models.list()
        assert {m.id for m in models.data} == {"tiny", "other"}
        msgs = [{"role": "user", "content": "hi"}]
        r = await oc.chat.completions.create(model="tiny", messages=msgs)
        assert r.choices[0].message.content == "hello from tiny"
        stream = await oc.chat.completions.create(model="tiny", stream=True, messages=msgs)
        text = "".join([ch.choices[0].delta.content or "" async for ch in stream])
        assert text == "hello from tiny"
        with pytest.raises(openai.NotFoundError):
            await oc.chat.completions.create(model="nope", messages=msgs)
        bad = openai.AsyncOpenAI(base_url="http://mst/v1", api_key="bad", http_client=c)
        with pytest.raises(openai.AuthenticationError):
            await bad.models.list()


def test_daemon_command_exists():
    from typer.testing import CliRunner

    from mxconsole.cli import app
    r = CliRunner().invoke(app, ["daemon", "--help"])
    assert r.exit_code == 0 and "foreground" in r.output.lower()


async def test_replies_reach_the_client_uncompressed(http, key, monkeypatch):
    monkeypatch.setenv("FAKE_MAX_GZIP", "1")
    r = await http.post("/v1/chat/completions", json=CHAT, headers=auth(key))
    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"] == "hello from tiny"


async def test_unknown_v1_routes_need_a_key_and_answer_like_openai(http, key):
    assert (await http.get("/v1/foo")).status_code == 401
    r = await http.post("/v1/responses", json=CHAT, headers=auth(key))
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "unknown_route"
