"""The operations console: its page, and the admin routes it calls."""
from test_gateway import client, http, key, store, sup  # noqa: F401  (shared fixtures)

ADMIN = {"X-Mst-Admin": "1"}


async def test_console_page_is_served_to_this_machine(http):
    r = await http.get("/console")
    assert r.status_code == 200
    assert "MX Studio" in r.text and "/console/console.js" in r.text
    js = await http.get("/console/console.js")
    assert js.status_code == 200 and "X-Mst-Admin" in js.text
    css = await http.get("/console/console.css")
    assert css.status_code == 200 and "--accent" in css.text


async def test_console_is_refused_through_a_proxy_or_a_foreign_host(http):
    assert (await http.get("/console", headers={"X-Forwarded-For": "192.168.1.20"})).status_code == 403
    assert (await http.get("/console", headers={"Host": "evil.example"})).status_code == 403
    assert (await http.get("/console/console.js", headers={"Host": "evil.example"})).status_code == 403


async def test_console_assets_cannot_escape_their_folder(http):
    assert (await http.get("/console/..%2Fgateway.py")).status_code == 404
    assert (await http.get("/console/nope.js")).status_code == 404


async def test_admin_models_merges_the_store_with_live_state(http):
    rows = (await http.get("/admin/models", headers=ADMIN)).json()
    tiny = next(r for r in rows if r["name"] == "tiny")
    assert tiny["repo_id"] == "org/tiny" and tiny["revision"] == "abc"
    assert tiny["state"] == "cold" and tiny["pinned"] is False and tiny["keep_alive"] == 300
    assert (await http.get("/admin/models")).status_code == 403  # the CLI/console header is required


async def test_patch_changes_pin_and_keep_alive(http, store):
    r = await http.patch("/admin/models/tiny", headers=ADMIN, json={"pinned": True})
    assert r.status_code == 200 and r.json()["pinned"] is True
    assert store.get_model("tiny").pinned is True
    r = await http.patch("/admin/models/tiny", headers=ADMIN, json={"pinned": False, "keep_alive": 600})
    assert r.json()["keep_alive"] == 600 and r.json()["pinned"] is False


async def test_patch_rejects_bad_values_and_unknown_models(http):
    for body in ({"keep_alive": -5}, {"keep_alive": "10m"}, {"pinned": "yes"}, {"device": "gpu1"}):
        assert (await http.patch("/admin/models/tiny", headers=ADMIN, json=body)).status_code == 400
    assert (await http.patch("/admin/models/nope", headers=ADMIN, json={"pinned": True})).status_code == 404


async def test_model_log_tail(http, store, monkeypatch, tmp_path):
    from hotfire.store import mst_home
    logs = mst_home() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "tiny.log").write_text("line\n" * 2000 + "boom: cannot load model\n")
    r = await http.get("/admin/models/tiny/log", headers=ADMIN)
    assert r.status_code == 200
    assert r.text.endswith("boom: cannot load model\n") and len(r.text) <= 8000
    assert (await http.get("/admin/models/other/log", headers=ADMIN)).text == ""
    assert (await http.get("/admin/models/nope/log", headers=ADMIN)).status_code == 404


async def test_admin_info_for_the_top_bar(http):
    info = (await http.get("/admin/info", headers=ADMIN)).json()
    assert {"host", "endpoint", "docs"} <= set(info)


async def test_console_cannot_be_framed_and_loads_nothing_from_elsewhere(http):
    for path in ("/console", "/console/console.js", "/console/console.css"):
        r = await http.get(path)
        assert r.headers["x-frame-options"] == "DENY"
        csp = r.headers["content-security-policy"]
        assert "frame-ancestors 'none'" in csp and "default-src 'self'" in csp
    page = (await http.get("/console")).text
    assert "fonts.googleapis.com" not in page  # fonts ship with mst; no outside calls


async def test_console_fonts_are_served_locally(http):
    css = (await http.get("/console/console.css")).text
    for font in ("source-sans-3-400.woff2", "source-sans-3-600.woff2", "source-code-pro-400.woff2", "source-code-pro-500.woff2"):
        assert f"/console/{font}" in css
        r = await http.get(f"/console/{font}")
        assert r.status_code == 200 and r.headers["content-type"] == "font/woff2" and r.content[:4] == b"wOF2"


async def test_requests_are_recorded_with_app_model_and_tokens(http, key):
    from test_gateway import CHAT, auth
    await http.post("/v1/chat/completions", json=CHAT, headers=auth(key))
    await http.post("/v1/chat/completions", json={**CHAT, "stream": True}, headers=auth(key))
    await http.get("/v1/models", headers=auth("mst-wrong"))
    rows = (await http.get("/admin/requests", headers=ADMIN)).json()
    assert [r["status"] for r in rows[:3]] == [401, 200, 200]
    streamed, plain = rows[1], rows[2]
    assert plain["app"] == "myapp" and plain["model"] == "tiny" and plain["completion_tokens"] == 12
    assert streamed["completion_tokens"] == 3 and streamed["estimated"] is True
    assert streamed["ttft_ms"] is not None and streamed["duration_ms"] >= streamed["ttft_ms"]


async def test_overview_metrics_and_info(http, key):
    from test_gateway import CHAT, auth
    await http.post("/v1/chat/completions", json=CHAT, headers=auth(key))
    m = (await http.get("/admin/metrics", headers=ADMIN)).json()
    assert m["requests"] == 1 and "gpus" in m and "ttft_p50_ms" in m
    info = (await http.get("/admin/info", headers=ADMIN)).json()
    assert {"max_version", "uptime_seconds", "active_keys", "lan_endpoint"} <= set(info)
    assert info["active_keys"] == 1
    assert (await http.get("/admin/metrics")).status_code == 403
    assert (await http.get("/admin/requests")).status_code == 403


async def test_ttft_and_decode_speed_come_only_from_streamed_replies(http, key):
    """A non-streamed reply arrives in one piece: its first byte is the whole answer, not the first token."""
    from test_gateway import CHAT, auth
    await http.post("/v1/chat/completions", json=CHAT, headers=auth(key))
    plain = (await http.get("/admin/requests", headers=ADMIN)).json()[0]
    assert plain["ttft_ms"] is None and plain["completion_tokens"] == 12


async def test_a_malformed_upstream_chunk_still_reaches_the_client(http, key, monkeypatch):
    from test_gateway import CHAT, auth
    from hotfire import metrics

    def boom(self, data):
        raise RuntimeError("meter bug")
    monkeypatch.setattr(metrics.StreamMeter, "feed", boom)
    r = await http.post("/v1/chat/completions", json={**CHAT, "stream": True}, headers=auth(key))
    assert r.status_code == 200 and "[DONE]" in r.text


async def test_ttft_does_not_include_the_cold_start(http, key, monkeypatch):
    from test_gateway import CHAT, auth
    monkeypatch.setenv("FAKE_MAX_DELAY", "1.0")  # the model takes a second to load
    await http.post("/v1/chat/completions", json={**CHAT, "stream": True}, headers=auth(key))
    row = (await http.get("/admin/requests", headers=ADMIN)).json()[0]
    assert row["ttft_ms"] < 900
