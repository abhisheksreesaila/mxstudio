"""F-024: `mxc rm` and DELETE /admin/models/{name}."""
import asyncio
import json

import pytest

from typer.testing import CliRunner

from mxconsole import cli
from mxconsole.store import Store
from test_console import ADMIN
from mxconsole import keys
from test_gateway import CHAT, auth, client, http, key, state, store, sup  # noqa: F401  (shared fixtures)

runner = CliRunner()


@pytest.fixture(autouse=True)
def hf_cache(tmp_path, monkeypatch):
    """Point the Hugging Face cache root at tmp_path/hub, where fake_hf_cache builds its layout."""
    monkeypatch.setattr("mxconsole.removal.hf_cache_root", lambda: tmp_path / "hub")


def fake_hf_cache(tmp_path, repo="org--tiny"):
    """A Hugging Face cache layout: models--org--tiny/{blobs,snapshots/<rev>}."""
    repo_dir = tmp_path / "hub" / f"models--{repo}"
    snap = repo_dir / "snapshots" / "abc"
    snap.mkdir(parents=True)
    (repo_dir / "blobs").mkdir()
    (repo_dir / "blobs" / "w").write_bytes(b"x" * 10)
    (snap / "model.safetensors").symlink_to(repo_dir / "blobs" / "w")
    return repo_dir, snap


def register(store, name, snap, repo_id="org/tiny"):
    store.add_model(name=name, repo_id=repo_id, revision="abc", source="hf", architecture="X", weights_path=str(snap))


async def test_delete_stops_unregisters_and_keeps_weights(http, store, sup, tmp_path):
    repo_dir, snap = fake_hf_cache(tmp_path)
    register(store, "tiny", snap)
    await http.post("/admin/models/tiny/load", headers=ADMIN)
    assert state(sup, "tiny") == "ready"
    r = await http.delete("/admin/models/tiny", headers=ADMIN)
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "tiny" and body["removed"] is True and body["purged"] is False
    assert store.get_model("tiny") is None
    assert "tiny" not in [s["name"] for s in sup.status()]  # process gone, model gone
    assert snap.exists()  # weights stay without ?purge
    assert [m["name"] for m in (await http.get("/admin/models", headers=ADMIN)).json()] == ["other"]


async def test_delete_deletes_only_this_models_graph_cache(http, store, mxc_home):
    mine, theirs = mxc_home / "graph-cache" / "tiny", mxc_home / "graph-cache" / "other"
    for d in (mine, theirs):
        d.mkdir(parents=True)
        (d / "g.mef").write_text("x")
    body = (await http.delete("/admin/models/tiny", headers=ADMIN)).json()
    assert not mine.exists() and theirs.exists()
    assert body["graph_cache_deleted"] is True


async def test_purge_deletes_the_hf_snapshot(http, store, tmp_path):
    repo_dir, snap = fake_hf_cache(tmp_path)
    register(store, "tiny", snap)
    body = (await http.delete("/admin/models/tiny?purge=1", headers=ADMIN)).json()
    assert body["purged"] is True and body["purged_path"] == str(repo_dir)
    assert not repo_dir.exists()


async def test_purge_keeps_weights_another_model_still_uses(http, store, tmp_path):
    repo_dir, snap = fake_hf_cache(tmp_path)
    for name in ("tiny", "tiny-copy"):
        register(store, name, snap)
    body = (await http.delete("/admin/models/tiny?purge=1", headers=ADMIN)).json()
    assert body["purged"] is False and "tiny-copy" in body["purge_note"]
    assert snap.exists()


async def test_purge_never_deletes_outside_a_hf_cache(http, store, tmp_path):
    odd = tmp_path / "my-weights"
    odd.mkdir()
    register(store, "tiny", odd)
    body = (await http.delete("/admin/models/tiny?purge=1", headers=ADMIN)).json()
    assert body["purged"] is False and odd.exists() and body["purge_note"]


async def test_delete_unknown_model_is_404_and_admin_only(http, store):
    assert (await http.delete("/admin/models/nope", headers=ADMIN)).status_code == 404
    assert (await http.delete("/admin/models/tiny")).status_code == 403
    assert (await http.delete("/admin/models/tiny", headers={**ADMIN, "X-Forwarded-For": "10.0.0.2"})).status_code == 403
    assert store.get_model("tiny") is not None


def test_cli_rm_through_the_daemon(inproc_daemon, tmp_path):
    store = Store.open()
    repo_dir, snap = fake_hf_cache(tmp_path)
    register(store, "tiny", snap)
    r = runner.invoke(cli.app, ["rm", "tiny", "--purge", "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["purged"] is True
    assert store.get_model("tiny") is None and not repo_dir.exists()
    bad = runner.invoke(cli.app, ["rm", "tiny"])
    assert bad.exit_code == 1 and "not registered" in bad.output


def test_cli_rm_without_a_daemon_edits_the_store_directly(monkeypatch):
    monkeypatch.setattr("mxconsole.serve.daemon_running", lambda: False)
    store = Store.open()
    store.add_model(name="tiny", repo_id="org/tiny", revision="abc", source="hf", architecture="X")
    r = runner.invoke(cli.app, ["rm", "tiny"])
    assert r.exit_code == 0 and "removed tiny" in r.output
    assert store.get_model("tiny") is None


async def test_a_request_during_removal_gets_404_and_leaves_no_child(http, store, sup, monkeypatch):
    """Unregister comes before stop: a request landing between them must not respawn the model."""
    real_stop, seen = sup.stop, {}

    async def stop_after_a_request(name):
        seen["during"] = await http.post("/v1/chat/completions", headers=auth(seen["key"]), json=CHAT)
        await real_stop(name)
    monkeypatch.setattr(sup, "stop", stop_after_a_request)
    seen["key"] = keys.create_key(store, "app")
    r = await http.delete("/admin/models/tiny", headers=ADMIN)
    assert r.status_code == 200
    assert seen["during"].status_code == 404 and seen["during"].json()["error"]["code"] == "model_not_found"
    assert "tiny" not in [s["name"] for s in sup.status()]


async def test_removing_a_model_while_a_request_is_starting_it(http, store, sup, monkeypatch):
    monkeypatch.setenv("FAKE_MAX_DELAY", "1.5")  # a slow start, so the request is still in flight
    k = keys.create_key(store, "app")
    chat = asyncio.create_task(http.post("/v1/chat/completions", headers=auth(k), json=CHAT))
    for _ in range(100):
        if "tiny" in sup._starts:
            break
        await asyncio.sleep(0.02)
    assert "tiny" in sup._starts
    assert (await http.delete("/admin/models/tiny", headers=ADMIN)).status_code == 200
    assert (await chat).status_code in (404, 503)  # never a 200 from a model that was removed
    assert "tiny" not in [s["name"] for s in sup.status()] and not sup._children
    again = await http.post("/v1/chat/completions", headers=auth(k), json=CHAT)
    assert again.status_code == 404 and not sup._children


async def test_graph_cache_deletion_stays_inside_graph_cache(store, mxc_home, tmp_path):
    from mxconsole import removal
    outside = tmp_path / "precious"
    outside.mkdir()
    (outside / "keep").write_text("x")
    (mxc_home / "graph-cache").mkdir(parents=True)
    (mxc_home / "graph-cache" / "linked").symlink_to(outside, target_is_directory=True)
    store.add_model(name="linked", repo_id="o/l", revision="a", source="hf", architecture="X")
    assert removal.remove(store, "linked")["graph_cache_deleted"] is False
    assert (outside / "keep").exists()
    store.add_model(name="..", repo_id="o/dots", revision="a", source="hf", architecture="X")  # a name that should never exist
    (mxc_home / "marker").write_text("x")
    assert removal.remove(store, "..")["graph_cache_deleted"] is False
    assert (mxc_home / "graph-cache").exists() and (mxc_home / "marker").exists()


async def test_purge_only_inside_the_hf_cache_root_and_never_a_symlink(store, tmp_path):
    from mxconsole import removal
    other = tmp_path / "elsewhere" / "models--org--tiny"
    snap = other / "snapshots" / "abc"
    snap.mkdir(parents=True)
    register(store, "tiny", snap)
    out = removal.remove(store, "tiny", purge=True)
    assert out["purged"] is False and other.exists()  # looks like a HF cache, but not THE cache
    real, snap2 = fake_hf_cache(tmp_path, "org--linked")
    link = tmp_path / "hub" / "models--org--sym"
    link.symlink_to(real, target_is_directory=True)
    register(store, "sym", link / "snapshots" / "abc", repo_id="org/sym")
    assert removal.remove(store, "sym", purge=True)["purged"] is False and real.exists()
