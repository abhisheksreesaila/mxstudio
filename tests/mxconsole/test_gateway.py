import asyncio
import importlib.util
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


# -- F-063: what LiteLLM's drop_params did, now in the daemon -------------------------------------------
async def test_max_itself_refuses_an_unknown_field(store, monkeypatch):
    """The premise, checked against MAX's own request model: one unknown field fails the whole request."""
    from max.serve.schemas import openai as schemas
    assert schemas.CreateChatCompletionRequest.model_config.get("extra") == "forbid"
    with pytest.raises(Exception):
        schemas.CreateChatCompletionRequest.model_validate({**CHAT, "drop_me": 1})


async def test_unknown_top_level_fields_are_dropped_and_named(http, key, monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_MAX_STRICT", "1")
    bodies = tmp_path / "bodies.jsonl"
    monkeypatch.setenv("FAKE_MAX_BODY_LOG", str(bodies))
    r = await http.post("/v1/chat/completions", headers=auth(key),
                        json={**CHAT, "temperature": 0.2, "drop_me": 1, "another_vendor_extra": {"x": 1}})
    assert r.status_code == 200, r.text
    assert r.headers["x-mxc-dropped-params"] == "another_vendor_extra,drop_me"
    sent = json.loads(bodies.read_text().splitlines()[-1])
    assert sent == {**CHAT, "temperature": 0.2}  # known fields (and nested message fields) are untouched


async def test_a_clean_body_passes_through_unchanged_with_no_header(http, key, monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_MAX_STRICT", "1")
    bodies = tmp_path / "bodies.jsonl"
    monkeypatch.setenv("FAKE_MAX_BODY_LOG", str(bodies))
    for path, body in (("/v1/chat/completions", {**CHAT, "stream": True}),
                       ("/v1/completions", {"model": "tiny", "prompt": "hi", "top_k": 5}),
                       ("/v1/embeddings", {"model": "tiny", "input": "hi"})):
        r = await http.post(path, headers=auth(key), json=body)
        assert r.status_code == 200, (path, r.text)
        assert "x-mxc-dropped-params" not in r.headers
        assert json.loads(bodies.read_text().splitlines()[-1]) == body


async def test_embeddings_and_completions_drop_their_own_unknowns(http, key, monkeypatch):
    monkeypatch.setenv("FAKE_MAX_STRICT", "1")
    r = await http.post("/v1/embeddings", headers=auth(key), json={"model": "tiny", "input": "hi", "messages": []})
    assert r.status_code == 200 and r.headers["x-mxc-dropped-params"] == "messages"
    r = await http.post("/v1/completions", headers=auth(key), json={"model": "tiny", "prompt": "hi", "messages": []})
    assert r.status_code == 200 and r.headers["x-mxc-dropped-params"] == "messages"


def test_without_a_readable_max_schema_the_body_passes_through(monkeypatch):
    from mxconsole import params
    monkeypatch.setattr(params, "accepted", lambda path: None)
    body = {**CHAT, "drop_me": 1}
    assert params.drop_unknown(body, "/v1/chat/completions") == (body, [])


# -- F-063 review: the dropped-params header is safe, and nothing leaks the in-flight count ------------------
def in_flight(sup, name="tiny"):
    return next(s for s in sup.status() if s["name"] == name)["in_flight"]


async def test_hostile_unknown_keys_never_reach_the_header_and_never_pin_the_model(http, key, sup, monkeypatch):
    monkeypatch.setenv("FAKE_MAX_STRICT", "1")
    hostile = {**CHAT, "naïve_ключ": 1, "x\r\nSet-Cookie: a=b": 1, "fine_extra": 1}
    r = await http.post("/v1/chat/completions", headers=auth(key), content=json.dumps(hostile))
    assert r.status_code == 200, r.text
    header = r.headers["x-mxc-dropped-params"]
    assert header == "fine_extra,+2 more" and "\r" not in header and "set-cookie" not in r.headers
    assert in_flight(sup) == 0


async def test_a_hundred_unknown_keys_give_a_bounded_header(http, key, sup, monkeypatch):
    monkeypatch.setenv("FAKE_MAX_STRICT", "1")
    body = {**CHAT, **{f"extra_{i:03d}": i for i in range(100)}}
    r = await http.post("/v1/chat/completions", headers=auth(key), json=body)
    assert r.status_code == 200, r.text
    names = r.headers["x-mxc-dropped-params"].split(",")
    assert len(names) == 21 and names[-1] == "+80 more" and names[0] == "extra_000"
    assert in_flight(sup) == 0


async def test_unprintable_names_are_only_counted(http, key, sup, monkeypatch):
    monkeypatch.setenv("FAKE_MAX_STRICT", "1")
    r = await http.post("/v1/chat/completions", headers=auth(key), content=json.dumps({**CHAT, "ключ": 1}))
    assert r.status_code == 200 and r.headers["x-mxc-dropped-params"] == "+1 more"
    assert in_flight(sup) == 0


async def test_a_failure_after_the_upstream_answered_releases_the_model(http, key, sup, monkeypatch):
    from mxconsole import metrics

    def broken(*a, **kw):
        raise RuntimeError("meter exploded")
    monkeypatch.setattr(metrics, "JsonMeter", broken)
    with pytest.raises(RuntimeError):
        await http.post("/v1/chat/completions", headers=auth(key), json=CHAT)
    assert in_flight(sup) == 0


# -- F-063 review: semantic fields are never dropped ----------------------------------------------------------
def test_semantic_fields_are_never_dropped_even_if_max_does_not_list_them(monkeypatch):
    from mxconsole import params
    monkeypatch.setattr(params, "accepted", lambda path: frozenset({"model", "input"}))
    body = {"model": "tiny", "input": "hi", "tools": [], "tool_choice": "auto", "functions": [], "function_call": "auto",
            "response_format": {"type": "json_object"}, "logprobs": True, "top_logprobs": 2, "n": 2, "seed": 1,
            "stop": ["x"], "vendor_extra": 1}
    out, dropped = params.drop_unknown(body, "/v1/embeddings")
    assert dropped == ["vendor_extra"]
    assert out == {k: v for k, v in body.items() if k != "vendor_extra"}


async def test_a_semantic_field_max_refuses_comes_back_as_maxs_own_error(http, key, monkeypatch):
    """`tools` on /v1/completions isn't something MAX can honour there: the client hears so instead of losing it."""
    monkeypatch.setenv("FAKE_MAX_STRICT", "1")
    r = await http.post("/v1/completions", headers=auth(key),
                        json={"model": "tiny", "prompt": "hi", "tools": [], "vendor_extra": 1})
    assert r.status_code == 400 and "tools" in r.text
    assert r.headers["x-mxc-dropped-params"] == "vendor_extra"


# -- F-063 review: MAX's schema location ---------------------------------------------------------------------
@pytest.mark.skipif(importlib.util.find_spec("max") is None, reason="MAX is not installed")
def test_maxs_request_schemas_are_where_params_reads_them():
    """Fails when MAX moves or renames its request models: then params.py would silently stop dropping."""
    from mxconsole import params
    params.accepted.cache_clear()
    for path in ("/v1/chat/completions", "/v1/completions", "/v1/embeddings"):
        fields = params.accepted(path)
        assert fields is not None and "model" in fields, path


def test_an_unreadable_max_schema_warns_once(monkeypatch, caplog):
    import builtins
    import logging
    from mxconsole import params
    real_import = builtins.__import__

    def no_max(name, *a, **kw):
        if name.startswith("max.serve"):
            raise ImportError("moved")
        return real_import(name, *a, **kw)
    monkeypatch.setattr(builtins, "__import__", no_max)
    monkeypatch.setattr(params, "_warned", False)
    params.accepted.cache_clear()
    try:
        with caplog.at_level(logging.WARNING, logger="mxconsole.params"):
            for path in ("/v1/chat/completions", "/v1/completions", "/v1/embeddings"):
                assert params.accepted(path) is None
        assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1
    finally:
        monkeypatch.undo()
        params.accepted.cache_clear()
