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
    from mst.store import mst_home
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
