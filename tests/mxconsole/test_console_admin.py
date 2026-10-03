"""Admin routes added for Mission Control: series, insights, keys, network, expose, try-it, docs redirect."""
import time

from mxconsole import expose, keys, metrics
from test_console import ADMIN
from test_gateway import CHAT, auth, client, http, key, store, sup  # noqa: F401  (shared fixtures)
from test_metrics import rec


ADMIN_ROUTES = [("GET", "/admin/series"), ("GET", "/admin/insights"), ("GET", "/admin/keys"),
                ("POST", "/admin/keys"), ("DELETE", "/admin/keys/x"), ("GET", "/admin/network"),
                ("POST", "/admin/expose"), ("POST", "/admin/expose/stop"), ("POST", "/admin/try/chat")]


async def test_every_new_admin_route_needs_the_admin_header_and_loopback(http, store, sup):
    from mxconsole.gateway import create_app
    for method, path in ADMIN_ROUTES:
        assert (await http.request(method, path)).status_code == 403, path
        assert (await http.request(method, path, headers={**ADMIN, "X-Forwarded-For": "10.0.0.2"})).status_code == 403, path
        assert (await http.request(method, path, headers={**ADMIN, "Origin": "https://evil.example"})).status_code == 403, path
    async with client(create_app(store, sup), host="192.168.1.20") as lan:
        for method, path in ADMIN_ROUTES:
            assert (await lan.request(method, path, headers=ADMIN)).status_code == 403, path


async def test_series_returns_buckets_per_model(http, store):
    rec(store, ts=time.time() - 20, model="tiny")
    s = (await http.get("/admin/series?range=5m", headers=ADMIN)).json()
    assert s["bucket_seconds"] == 10 and len(s["buckets"]) == 30
    assert sum(b["requests"] for b in s["models"]["tiny"]) == 1
    assert (await http.get("/admin/series?range=1h", headers=ADMIN)).json()["bucket_seconds"] == 60
    assert (await http.get("/admin/series?range=nope", headers=ADMIN)).status_code == 400
    assert (await http.get("/admin/series", headers=ADMIN)).json()["range"] == "1h"


async def test_metrics_and_requests_accept_a_range_and_a_model(http, store):
    rec(store, ts=time.time() - 1800, model="tiny")
    rec(store, ts=time.time() - 10, model="other")
    assert (await http.get("/admin/metrics", headers=ADMIN)).json()["requests"] == 1  # 5 minutes by default
    assert (await http.get("/admin/metrics?range=1h", headers=ADMIN)).json()["requests"] == 2
    assert (await http.get("/admin/metrics?range=9y", headers=ADMIN)).status_code == 400
    rows = (await http.get("/admin/requests?model=tiny", headers=ADMIN)).json()
    assert [r["model"] for r in rows] == ["tiny"]


async def test_info_counts_blocked_requests_today(http, store):
    rec(store, ts=time.time(), status=401, app=None, model=None, ttft_ms=None, completion_tokens=None,
        prompt_tokens=None)
    rec(store, ts=time.time() - 3 * 86400, status=401, app=None, model=None, ttft_ms=None,
        completion_tokens=None, prompt_tokens=None)
    info = (await http.get("/admin/info", headers=ADMIN)).json()
    assert info["blocked_today"] == 1


async def test_insights_say_nothing_when_nothing_applies(http, store):
    assert (await http.get("/admin/insights", headers=ADMIN)).json() == []
    for _ in range(2):
        rec(store, ts=time.time() - 30, app=None, model=None, status=401, ttft_ms=None,
            completion_tokens=None, prompt_tokens=None)
    ids = [i["id"] for i in (await http.get("/admin/insights", headers=ADMIN)).json()]
    assert ids == ["nokey"]


async def test_keys_list_hides_internal_keys_and_never_returns_hashes(http, store):
    keys.create_key(store, "notes")
    keys.create_key(store, "_litellm", internal=True)
    rows = (await http.get("/admin/keys", headers=ADMIN)).json()
    assert [r["app"] for r in rows] == ["notes"]
    assert set(rows[0]) == {"app", "prefix", "created_at", "last_used_at", "revoked_at", "status"}
    assert rows[0]["status"] == "active" and rows[0]["prefix"].startswith("mst-")


async def test_new_key_is_shown_once_and_works(http, store):
    r = await http.post("/admin/keys", headers=ADMIN, json={"app": "finx"})
    assert r.status_code == 201
    plain = r.json()["key"]
    assert plain.startswith("mst-") and r.json()["app"] == "finx"
    assert keys.verify_key(store, plain) == "finx"
    listed = (await http.get("/admin/keys", headers=ADMIN)).json()
    assert "key" not in listed[0] and plain not in str(listed)
    assert (await http.post("/admin/keys", headers=ADMIN, json={"app": "finx"})).status_code == 409


async def test_new_key_rejects_bad_names(http):
    for body in ({"app": ""}, {"app": "has space"}, {"app": "_internal"}, {}, {"app": 5}):
        assert (await http.post("/admin/keys", headers=ADMIN, json=body)).status_code == 400, body


async def test_revoke_a_key(http, store, key):
    assert (await http.delete("/admin/keys/myapp", headers=ADMIN)).status_code == 200
    assert keys.verify_key(store, key) is None
    assert (await http.delete("/admin/keys/myapp", headers=ADMIN)).status_code == 404
    keys.create_key(store, "_litellm", internal=True)
    assert (await http.delete("/admin/keys/_litellm", headers=ADMIN)).status_code == 404  # mxc's own


async def test_network_reports_ports_models_and_the_apps_seen(http, store, sup, key, monkeypatch):
    monkeypatch.setattr(expose, "status", lambda: {"caddy": True, "litellm": False})
    monkeypatch.setattr(expose, "lan_endpoint", lambda: "https://box.local:8443/v1")
    await http.post("/v1/chat/completions", json=CHAT, headers=auth(key))
    rec(store, ts=time.time(), app="_litellm")
    n = (await http.get("/admin/network", headers=ADMIN)).json()
    assert n["expose"] == {"caddy": True, "litellm": False} and n["lan_endpoint"] == "https://box.local:8443/v1"
    assert n["ports"]["daemon"] == 11435 and n["ports"]["litellm"] == expose.LITELLM_PORT
    assert n["ports"]["caddy"] == expose.EXPOSE_PORT
    tiny = next(m for m in n["models"] if m["name"] == "tiny")
    assert tiny["state"] == "ready" and isinstance(tiny["port"], int) and tiny["recent"] == 1
    assert next(m for m in n["models"] if m["name"] == "other")["port"] is None
    assert [a["app"] for a in n["apps"]] == ["myapp"]  # internal keys are not clients
    assert n["recent_requests"] == 2 and "ca_path" in n


async def test_expose_and_stop_call_expose(http, store, monkeypatch):
    calls = []
    monkeypatch.setattr(expose, "start", lambda **kw: calls.append(("start", kw)) or {})
    monkeypatch.setattr(expose, "stop", lambda **kw: calls.append(("stop", kw)))
    monkeypatch.setattr(expose, "default_host", lambda: "box.local")
    monkeypatch.setattr(expose, "lan_endpoint", lambda: "https://box.local:8443/v1")
    r = await http.post("/admin/expose", headers=ADMIN, json={})
    assert r.status_code == 200 and r.json()["lan_endpoint"] == "https://box.local:8443/v1"
    assert calls[0][0] == "start" and calls[0][1]["host"] == "box.local" and calls[0][1]["litellm"] is True
    assert calls[0][1]["store"] is store
    r = await http.post("/admin/expose", headers=ADMIN, json={"host": "10.0.0.5", "litellm": False})
    assert calls[1][1]["host"] == "10.0.0.5" and calls[1][1]["litellm"] is False
    assert (await http.post("/admin/expose/stop", headers=ADMIN)).status_code == 200
    assert calls[2][0] == "stop" and calls[2][1]["store"] is store


async def test_expose_failures_come_back_as_messages(http, monkeypatch):
    def broken(**kw):
        raise expose.ExposeError("caddy not found (caddy). Install it, then run mxc expose again.")
    monkeypatch.setattr(expose, "start", broken)
    r = await http.post("/admin/expose", headers=ADMIN, json={})
    assert r.status_code == 500 and "caddy not found" in r.json()["error"]["message"]
    for body in ({"host": 5}, {"host": "bad host!"}, {"litellm": "yes"}):
        monkeypatch.setattr(expose, "start", lambda **kw: expose.render_caddyfile(hosts=[kw["host"]]))
        assert (await http.post("/admin/expose", headers=ADMIN, json=body)).status_code == 400, body


async def test_try_chat_goes_through_the_gateway_as_the_console(http, store):
    body = {"model": "tiny", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 16}
    r = await http.post("/admin/try/chat", headers=ADMIN, json=body)
    assert r.status_code == 200 and r.json()["choices"]
    row = metrics.recent(store, 1)[0]
    assert row["app"] == "console" and row["model"] == "tiny" and row["status"] == 200
    streamed = await http.post("/admin/try/chat", headers=ADMIN, json={**body, "stream": True})
    assert "data:" in streamed.text and "[DONE]" in streamed.text


async def test_try_chat_always_enforces_the_token_cap(http):
    from mxconsole.gateway import TRY_MAX_TOKENS
    base = {"model": "tiny", "messages": [{"role": "user", "content": "hi"}]}
    for extra in ({}, {"max_tokens": TRY_MAX_TOKENS * 100}, {"max_completion_tokens": 999999},
                  {"max_tokens": 16, "max_completion_tokens": 999999}):
        r = await http.post("/admin/try/chat", headers=ADMIN, json={**base, **extra})
        assert r.status_code == 200, extra
        seen = r.json()["seen_limits"]
        assert seen["max_tokens"] <= TRY_MAX_TOKENS and "max_completion_tokens" not in seen, (extra, seen)
    assert (await http.post("/admin/try/chat", headers=ADMIN, json={**base, "max_tokens": 16})).json()["seen_limits"]["max_tokens"] == 16


async def test_try_chat_validates_the_body(http):
    for body in ({}, {"model": "tiny"}, {"model": 3, "messages": []}, {"model": "tiny", "messages": "hi"},
                 {"model": "tiny", "messages": [], "max_tokens": "lots"}):
        assert (await http.post("/admin/try/chat", headers=ADMIN, json=body)).status_code == 400, body
    r = await http.post("/admin/try/chat", headers=ADMIN, json={"model": "nope", "messages": [{"role": "user", "content": "x"}]})
    assert r.status_code == 404 and r.json()["error"]["code"] == "model_not_found"


async def test_docs_redirects_to_the_console_api_page(http):
    r = await http.get("/docs", follow_redirects=False)
    assert r.status_code in (301, 302, 307, 308) and r.headers["location"] == "/console#api"
    assert "scalar" not in (await http.get("/docs", follow_redirects=True)).text.lower()
    assert (await http.get("/docs", headers={"X-Forwarded-For": "192.168.1.20"})).status_code == 403
    assert (await http.get("/openapi.json")).status_code == 200  # the spec stays


async def test_only_listed_console_files_are_served(http):
    from mxconsole.gateway import CONSOLE_ASSETS, CONSOLE_DIR
    assert "console.css" in CONSOLE_ASSETS and "geist-variable.woff2" in CONSOLE_ASSETS
    assert not any("source-" in a for a in CONSOLE_ASSETS)
    for asset in sorted(CONSOLE_ASSETS):
        assert (CONSOLE_DIR / asset).is_file(), asset  # nothing listed is missing
        r = await http.get(f"/console/{asset}")
        assert r.status_code == 200, asset
    on_disk = {p.name for p in CONSOLE_DIR.iterdir() if p.name not in ("index.html", "LICENSE-fonts.txt")}
    assert on_disk == set(CONSOLE_ASSETS)  # nothing unlisted ships, nothing listed is missing
    for bad in ("index.html", "LICENSE-fonts.txt", "console.js", "source-sans-3-400.woff2", "..%2Fgateway.py", "%2e%2e/gateway.py"):
        assert (await http.get(f"/console/{bad}")).status_code == 404, bad


def test_console_code_never_writes_html_or_loads_from_elsewhere():
    """Data goes in as text nodes; the CSP is default-src 'self'. Both are cheap to break by accident."""
    import re
    from mxconsole.gateway import CONSOLE_DIR
    banned = re.compile(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|\beval\(|new Function|setAttribute\(\s*['\"]style['\"]")
    for path in CONSOLE_DIR.glob("*.js"):
        text = path.read_text()
        assert not banned.search(text), f"{path.name} writes HTML or an inline style"
        assert not re.search(r"(fetch|import)\(\s*['\"`]https?:", text), f"{path.name} loads from another origin"
    for name in ("index.html", "console.css"):
        text = (CONSOLE_DIR / name).read_text()
        assert "http://" not in text and "https://" not in text, f"{name} points outside the daemon"
        assert not re.search(r"<style|\sstyle=\"|<script(?![^>]*\ssrc=)", text), f"{name} has inline code the CSP blocks"
