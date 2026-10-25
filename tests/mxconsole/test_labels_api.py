"""/admin/labels and the kind/labels fields of /admin/models: what the console's library will call."""
from test_console import ADMIN
from test_gateway import client, http, store, sup  # noqa: F401  (shared fixtures)

from mxconsole.gateway import create_app

LABEL_ROUTES = [("GET", "/admin/labels"), ("POST", "/admin/labels"), ("PUT", "/admin/labels/order"),
                ("PATCH", "/admin/labels/x"), ("DELETE", "/admin/labels/x"),
                ("POST", "/admin/labels/x/add"), ("POST", "/admin/labels/x/remove")]


async def labels(http):
    return (await http.get("/admin/labels", headers=ADMIN)).json()


async def test_label_routes_need_the_admin_header_loopback_and_a_local_origin(http, store, sup):
    for method, path in LABEL_ROUTES:
        assert (await http.request(method, path)).status_code == 403, path
        assert (await http.request(method, path, headers={**ADMIN, "X-Forwarded-For": "10.0.0.2"})).status_code == 403, path
        assert (await http.request(method, path, headers={**ADMIN, "Origin": "https://evil.example"})).status_code == 403, path
    async with client(create_app(store, sup), host="192.168.1.20") as lan:
        for method, path in LABEL_ROUTES:
            assert (await lan.request(method, path, headers=ADMIN)).status_code == 403, path
    assert store.labels() == []


async def test_models_list_kind_and_labels(http, store):
    store.set_kind("tiny", "coder")
    store.add_labels(["tiny"], ["work"])
    rows = {r["name"]: r for r in (await http.get("/admin/models", headers=ADMIN)).json()}
    assert (rows["tiny"]["kind"], rows["tiny"]["labels"]) == ("coder", ["work"])
    assert (rows["other"]["kind"], rows["other"]["labels"]) == ("chat", [])


async def test_correct_a_kind_with_patch(http, store):
    r = await http.patch("/admin/models/tiny", headers=ADMIN, json={"kind": "vision"})
    assert r.status_code == 200 and r.json()["kind"] == "vision"
    assert store.get_model("tiny").kind == "vision"
    r = await http.patch("/admin/models/tiny", headers=ADMIN, json={"kind": "fast"})
    assert r.status_code == 400 and "chat, coder, vision, embeddings, image" in r.json()["error"]["message"]
    assert store.get_model("tiny").kind == "vision"


async def test_create_list_rename_reorder_delete(http, store):
    for name in ("work", "home"):
        r = await http.post("/admin/labels", headers=ADMIN, json={"name": name})
        assert r.status_code == 200 and r.json() == {"name": name}
    assert await labels(http) == [{"name": "work", "position": 0, "count": 0}, {"name": "home", "position": 1, "count": 0}]

    r = await http.put("/admin/labels/order", headers=ADMIN, json={"names": ["home", "work"]})
    assert r.status_code == 200
    assert [l["name"] for l in await labels(http)] == ["home", "work"]

    store.add_labels(["tiny"], ["work"])
    r = await http.patch("/admin/labels/work", headers=ADMIN, json={"name": "office"})
    assert r.status_code == 200 and r.json() == {"name": "office"}
    assert store.get_model("tiny").labels == ("office",)

    assert (await http.delete("/admin/labels/office", headers=ADMIN)).status_code == 200
    assert await labels(http) == [{"name": "home", "position": 0, "count": 0}]
    assert store.get_model("tiny").labels == ()


async def test_add_and_remove_a_label_on_many_models(http, store):
    r = await http.post("/admin/labels/work/add", headers=ADMIN, json={"models": ["tiny", "other"]})
    assert r.status_code == 200
    assert await labels(http) == [{"name": "work", "position": 0, "count": 2}]
    r = await http.post("/admin/labels/work/remove", headers=ADMIN, json={"models": ["tiny"]})
    assert r.status_code == 200
    assert await labels(http) == [{"name": "work", "position": 0, "count": 1}]
    assert store.get_model("tiny").labels == () and store.get_model("other").labels == ("work",)


async def test_errors_say_what_is_wrong(http, store):
    async def err(method, path, body, status, code, words):
        r = await http.request(method, path, headers=ADMIN, json=body)
        assert (r.status_code, r.json()["error"]["code"]) == (status, code), r.text
        assert words in r.json()["error"]["message"]

    await err("POST", "/admin/labels", {"name": "Bad Name"}, 400, "invalid_label", "lower-case")
    await err("POST", "/admin/labels", {}, 400, "invalid_request_body", "name")
    await http.post("/admin/labels", headers=ADMIN, json={"name": "work"})
    await err("POST", "/admin/labels", {"name": "work"}, 409, "label_exists", "already exists")
    await err("PATCH", "/admin/labels/nope", {"name": "x"}, 404, "label_not_found", "no label named nope")
    await err("PATCH", "/admin/labels/work", {"name": "UP"}, 400, "invalid_label", "lower-case")
    await err("DELETE", "/admin/labels/nope", None, 404, "label_not_found", "no label named nope")
    await err("PUT", "/admin/labels/order", {"names": ["x"]}, 400, "invalid_order", "every label exactly once")
    await err("POST", "/admin/labels/work/add", {"models": ["ghost"]}, 404, "model_not_found", "no model named ghost")
    await err("POST", "/admin/labels/work/add", {"models": "tiny"}, 400, "invalid_request_body", "models")
    assert store.get_model("tiny").labels == ()


async def test_removing_a_model_through_the_api_drops_its_labels(http, store):
    store.add_labels(["tiny", "other"], ["work"])
    assert (await http.delete("/admin/models/tiny", headers=ADMIN)).status_code == 200
    assert await labels(http) == [{"name": "work", "position": 0, "count": 1}]


async def test_models_list_the_size_of_their_weights_on_disk(http, store, tmp_path):
    """F-062: the My models Size column. A snapshot folder adds up its files (links followed); no weights means no number."""
    snap = tmp_path / "snap"
    (snap / "sub").mkdir(parents=True)
    (snap / "a.safetensors").write_bytes(b"x" * 1000)
    (snap / "sub" / "b.bin").write_bytes(b"y" * 234)
    (tmp_path / "blob").write_bytes(b"z" * 66)
    (snap / "link").symlink_to(tmp_path / "blob")
    store.add_model(name="sized", repo_id="org/sized", revision="main", source="hf", architecture="X", weights_path=str(snap))
    store.add_model(name="gone", repo_id="org/gone", revision="main", source="hf", architecture="X", weights_path=str(tmp_path / "missing"))
    rows = {r["name"]: r for r in (await http.get("/admin/models", headers=ADMIN)).json()}
    assert rows["sized"]["size_bytes"] == 1300
    assert rows["tiny"]["size_bytes"] is None and rows["gone"]["size_bytes"] is None
