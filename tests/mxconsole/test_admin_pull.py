"""/admin/pulls: the guards, the lifecycle over HTTP, and the test-only fake hub hook."""
import asyncio
import logging
import sys
from pathlib import Path

import httpx
import pytest
from fake_hub import CHUNK, SlowHub
from fakes import CATALOG

from mxconsole.gateway import create_app
from mxconsole.store import Store
from mxconsole.supervisor import Supervisor

ADMIN = {"X-Mst-Admin": "1"}
FAKE_HUB_FILE = Path(__file__).parent / "fake_hub.py"


@pytest.fixture
def hub(monkeypatch):
    h = SlowHub(weights_bytes=6 * CHUNK, chunk_delay=0.02)
    monkeypatch.setattr("mxconsole.resolve.HubClient", lambda: h)
    monkeypatch.setattr("mxconsole.resolve.load_catalog", lambda **kw: CATALOG)
    return h


@pytest.fixture
async def http(tmp_path, hub):
    store = Store(tmp_path / "mst.db")
    sup = Supervisor(store, max_command=(sys.executable, "-c", "pass"))
    app = create_app(store, sup)
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 5000))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:11435") as c:
        c.store = store
        yield c


async def settle(http, pull_id, until=("done", "failed", "cancelled", "unsupported")):
    for _ in range(300):
        job = (await http.get(f"/admin/pulls/{pull_id}", headers=ADMIN)).json()
        if job["status"] in until:
            return job
        await asyncio.sleep(0.02)
    raise AssertionError("pull did not finish")


async def test_every_pull_route_refuses_non_admin_callers(http):
    routes = [("GET", "/admin/pulls"), ("POST", "/admin/pulls"), ("GET", "/admin/pulls/1"), ("POST", "/admin/pulls/1/cancel")]
    for method, path in routes:
        kw = {"content": b'{"ref": "qwen2.5-0.5b"}'}
        assert (await http.request(method, path, **kw)).status_code == 403, f"{method} {path} without the admin header"
        assert (await http.request(method, path, headers={**ADMIN, "X-Forwarded-For": "192.168.1.9"}, **kw)).status_code == 403
        assert (await http.request(method, path, headers={**ADMIN, "Origin": "https://evil.example"}, **kw)).status_code == 403
        assert (await http.request(method, path, headers={**ADMIN, "Host": "evil.example"}, **kw)).status_code == 403
    assert http.store.models() == []  # nothing was pulled by the refused calls


async def test_start_poll_finish_and_list(http):
    started = await http.post("/admin/pulls", json={"ref": "qwen2.5-0.5b"}, headers=ADMIN)
    assert started.status_code == 202
    job = started.json()
    assert job["ref"] == "qwen2.5-0.5b" and [s["id"] for s in job["steps"]][2] == "download"
    done = await settle(http, job["id"])
    assert done["status"] == "done" and done["percent"] == 100.0 and done["model"] == "qwen2.5-0.5b"
    listing = (await http.get("/admin/pulls", headers=ADMIN)).json()
    assert [p["id"] for p in listing["pulls"]] == [job["id"]]
    assert listing["curated"]["qwen2.5-0.5b"] == "Qwen/Qwen2.5-0.5B-Instruct"
    models = (await http.get("/admin/models", headers=ADMIN)).json()
    assert [m["name"] for m in models] == ["qwen2.5-0.5b"]  # it shows up in Models


async def test_second_pull_of_the_same_ref_is_a_409_with_the_running_job(http):
    first = (await http.post("/admin/pulls", json={"ref": "qwen2.5-0.5b"}, headers=ADMIN)).json()
    again = await http.post("/admin/pulls", json={"ref": "Qwen/Qwen2.5-0.5B-Instruct"}, headers=ADMIN)
    assert again.status_code == 409
    body = again.json()
    assert body["error"]["code"] == "pull_in_progress" and body["pull"]["id"] == first["id"]
    await settle(http, first["id"])


async def test_cancel_over_http(http, hub):
    hub.weights_bytes, hub.chunk_delay = 60 * CHUNK, 0.02
    job = (await http.post("/admin/pulls", json={"ref": "qwen2.5-0.5b"}, headers=ADMIN)).json()
    await asyncio.sleep(0.15)  # let some bytes flow first
    cancelled = await http.post(f"/admin/pulls/{job['id']}/cancel", headers=ADMIN)
    assert cancelled.status_code == 200
    final = await settle(http, job["id"])
    assert final["status"] == "cancelled"
    assert http.store.get_model("qwen2.5-0.5b") is None
    assert (await http.post("/admin/pulls/999/cancel", headers=ADMIN)).status_code == 404


async def test_unsupported_ends_with_the_port_hint(http):
    job = (await http.post("/admin/pulls", json={"ref": "acme/weird"}, headers=ADMIN)).json()
    final = await settle(http, job["id"])
    assert final["status"] == "unsupported" and final["hint"] == "mxc port acme/weird"


async def test_bad_requests_are_400_and_unknown_pull_is_404(http):
    for body in ({}, {"ref": 5}, {"ref": "../x"}, {"ref": "qwen2.5-0.5b", "name": "../x"}, {"ref": "qwen2.5-0.5b", "name": 3}):
        r = await http.post("/admin/pulls", json=body, headers=ADMIN)
        assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_request_body", body
    assert (await http.post("/admin/pulls", content=b"nope", headers=ADMIN)).status_code == 400
    assert (await http.get("/admin/pulls/999", headers=ADMIN)).status_code == 404


def test_the_fake_hub_hook_needs_the_exact_file_and_ignores_anything_else(monkeypatch, tmp_path, caplog):
    from mxconsole import admin_pull
    monkeypatch.delenv("MXC_FAKE_HUB", raising=False)
    assert admin_pull._fake_hub() is None
    caplog.set_level(logging.WARNING)
    for bad in ("1", "true", "tests/mxconsole/fake_hub.py", str(tmp_path / "fake_hub.py"), str(Path(__file__))):
        monkeypatch.setenv("MXC_FAKE_HUB", bad)
        assert admin_pull._fake_hub() is None, bad
    assert "ignoring it" in caplog.text
    monkeypatch.setenv("MXC_FAKE_HUB", str(FAKE_HUB_FILE))
    module = admin_pull._fake_hub()
    assert module is not None and callable(module.make_hub) and "Qwen2ForCausalLM" in str(module.CATALOG)
    assert "FAKE HUGGING FACE" in caplog.text
