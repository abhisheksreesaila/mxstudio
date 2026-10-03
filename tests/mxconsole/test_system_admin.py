"""Admin routes for the System pages: guard, data, log tails, LiteLLM restart, and the daemon's counters."""
import os
import time

from mxconsole import expose, keys
from mxconsole.gateway import create_app
from test_console import ADMIN
from test_gateway import auth, client, http, key, store, sup  # noqa: F401  (shared fixtures)
from test_metrics import rec

SYSTEM_ROUTES = [("GET", "/admin/system"), ("GET", "/admin/gateway"), ("GET", "/admin/daemon/log"),
                 ("GET", "/admin/caddy/log"), ("GET", "/admin/litellm/log"), ("POST", "/admin/litellm/restart")]


async def test_every_system_route_needs_the_admin_header_and_loopback(http, store, sup):
    for method, path in SYSTEM_ROUTES:
        assert (await http.request(method, path)).status_code == 403, path
        assert (await http.request(method, path, headers={**ADMIN, "X-Forwarded-For": "10.0.0.2"})).status_code == 403, path
        assert (await http.request(method, path, headers={**ADMIN, "Origin": "https://evil.example"})).status_code == 403, path
    async with client(create_app(store, sup), host="192.168.1.20") as lan:
        for method, path in SYSTEM_ROUTES:
            assert (await lan.request(method, path, headers=ADMIN)).status_code == 403, path


async def test_system_lists_the_crew_listeners_and_log_sources(http, key):
    await http.post("/admin/models/tiny/load", headers=ADMIN)
    body = (await http.get("/admin/system", headers=ADMIN)).json()
    by = {c["id"]: c for c in body["crew"]}
    assert by["daemon"]["pid"] == os.getpid() and by["daemon"]["rss_mb"] > 0
    assert by["model:tiny"]["state"] == "ready" and by["model:tiny"]["pid"] and by["model:tiny"]["port"]
    assert "model:other" not in by  # cold models have no process
    assert by["caddy"]["state"] == "stopped" and by["litellm"]["state"] == "stopped"
    ports = {r["name"]: r for r in body["listeners"]}
    assert ports["max serve · tiny"]["port"] == by["model:tiny"]["port"] and ports["Caddy"]["state"] == "off"
    assert {"id": "model:other", "label": "other"} in body["logs"]
    assert not any(c.get("gpu_mb") for c in body["crew"])


async def test_gateway_counts_what_the_daemon_can_know(http, store, key):
    keys.create_key(store, "second")
    keys.revoke_key(store, "second")
    await http.get("/v1/models")  # 401
    await http.get("/v1/models", headers=auth("mst-nope"))  # 401
    await http.get("/auth/check", headers=auth("mst-nope"))  # 401: what Caddy's forward_auth sends
    assert (await http.get("/auth/check", headers=auth(key))).status_code == 200  # a good key is not counted
    rec(store, ts=time.time() - 3 * 86400, status=401)  # not today
    await http.get("/v1/nothing", headers=auth(key))
    await http.post("/v1/images", headers=auth(key))
    await http.get("/admin/keys")  # refused: no admin header
    g = (await http.get("/admin/gateway", headers=ADMIN)).json()
    assert g["unauthorized_today"] == 3 and g["unknown_routes_today"] == 2
    assert g["admin_refused_today"] == 1
    assert g["keys"] == {"active": 1, "revoked": 1}
    assert g["tls"] == {"on": False, "endpoint": None}
    assert "dot_segments" in g["enforced_by_caddy"]  # said, not counted
    assert "dot_segments" not in g and "caddy_400s" not in g


async def test_admin_refusals_count_every_refused_admin_route(http):
    for path in ("/admin/status", "/admin/models", "/admin/info"):
        await http.get(path)
    await http.get("/admin/status", headers={**ADMIN, "X-Forwarded-For": "10.0.0.2"})
    assert (await http.get("/admin/gateway", headers=ADMIN)).json()["admin_refused_today"] == 4


async def test_daemon_log_is_a_capped_text_tail(http, mxc_home):
    assert (await http.get("/admin/daemon/log", headers=ADMIN)).text == ""  # no file yet
    (mxc_home / "logs").mkdir(parents=True)
    (mxc_home / "logs" / "daemon.log").write_text("x" * 100000 + "<b>last</b>")
    r = await http.get("/admin/daemon/log", headers=ADMIN)
    assert r.headers["content-type"].startswith("text/plain") and r.text.endswith("<b>last</b>")
    assert len(r.text) == 16000
    assert len((await http.get("/admin/daemon/log?bytes=100", headers=ADMIN)).text) == 100
    assert len((await http.get("/admin/daemon/log?bytes=99999999", headers=ADMIN)).text) == 65536
    assert len((await http.get("/admin/daemon/log?bytes=junk", headers=ADMIN)).text) == 16000


async def test_caddy_and_litellm_logs_come_from_the_expose_folder(http, mxc_home):
    (mxc_home / "expose").mkdir(parents=True)
    (mxc_home / "expose" / "caddy.log").write_text("caddy says hi")
    (mxc_home / "expose" / "litellm.log").write_text("litellm says hi")
    assert (await http.get("/admin/caddy/log", headers=ADMIN)).text == "caddy says hi"
    assert (await http.get("/admin/litellm/log", headers=ADMIN)).text == "litellm says hi"


async def test_litellm_restart_refreshes_only_when_it_runs(http, monkeypatch):
    r = await http.post("/admin/litellm/restart", headers=ADMIN)
    assert r.status_code == 409 and r.json()["error"]["code"] == "litellm_not_running"
    monkeypatch.setattr(expose, "refresh_if_running", lambda store=None: True)
    assert (await http.post("/admin/litellm/restart", headers=ADMIN)).json() == {"restarted": True}

    def boom(store=None):
        raise expose.ExposeError("litellm exited")
    monkeypatch.setattr(expose, "refresh_if_running", boom)
    r = await http.post("/admin/litellm/restart", headers=ADMIN)
    assert r.status_code == 500 and "litellm exited" in r.json()["error"]["message"]


async def test_litellm_restart_refuses_a_second_one_while_one_runs(http, monkeypatch):
    import asyncio
    import threading
    started, release = threading.Event(), threading.Event()

    def slow(store=None):
        started.set()
        release.wait(5)
        return True
    monkeypatch.setattr(expose, "refresh_if_running", slow)
    first = asyncio.create_task(http.post("/admin/litellm/restart", headers=ADMIN))
    for _ in range(100):
        if started.is_set():
            break
        await asyncio.sleep(0.02)
    second = await http.post("/admin/litellm/restart", headers=ADMIN)
    assert second.status_code == 409 and "a restart is already running" in second.json()["error"]["message"]
    release.set()
    assert (await first).json() == {"restarted": True}
    assert (await http.post("/admin/litellm/restart", headers=ADMIN)).status_code == 200  # lock released
