import pytest
from fakes import CATALOG, make_hf

from hotfire import resolve as r
from hotfire.store import Store


def test_no_safetensors_is_its_own_kind():
    res = r.resolve("acme/pickle", catalog=CATALOG, hf=make_hf())
    assert res.kind == "no-safetensors" and "safetensors" in res.message


def test_sharded_index_downloads_only_referenced_shards():
    hf = make_hf()
    r.pull("acme/sharded", store=Store.open(), catalog=CATALOG, hf=hf)
    patterns = hf.downloads[0][2]
    assert "consolidated.safetensors" not in patterns
    assert {"model-00001.safetensors", "model-00002.safetensors",
            "model.safetensors.index.json"} <= set(patterns)


def test_name_collision_refuses_and_name_option_resolves():
    store = Store.open()
    hf = make_hf()
    r.pull("alice/foo", store=store, catalog=CATALOG, hf=hf)
    with pytest.raises(r.PullError) as err:
        r.pull("bob/foo", store=store, catalog=CATALOG, hf=hf)
    assert "--name" in str(err.value) and err.value.resolution.kind == "name-taken"
    assert store.get_model("foo").repo_id == "alice/foo"
    assert r.pull("bob/foo", store=store, catalog=CATALOG, hf=hf, name="bobfoo").name == "bobfoo"
    r.pull("alice/foo", store=store, catalog=CATALOG, hf=hf)  # re-pull of the same repo is fine


def test_full_repo_id_of_curated_model_uses_alias():
    model = r.pull("Qwen/Qwen2.5-0.5B-Instruct", store=Store.open(), catalog=CATALOG, hf=make_hf())
    assert model.name == "qwen2.5-0.5b"


@pytest.mark.parametrize("output", ["", "not json", "{}", '{"architectures": {}}'])
def test_invalid_catalog_is_not_cached(hotfire_home, output):
    with pytest.raises(r.ResolveError):
        r.load_catalog(runner=lambda: output)
    assert not (hotfire_home / "max_catalog.json").exists()


def test_missing_max_binary_is_a_resolve_error(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("max")
    monkeypatch.setattr("subprocess.run", boom)
    with pytest.raises(r.ResolveError, match="max"):
        r.load_catalog()


def test_max_list_failure_is_a_resolve_error(monkeypatch):
    import subprocess
    monkeypatch.setattr("subprocess.run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "boom\n"))
    with pytest.raises(r.ResolveError, match="boom"):
        r.load_catalog()


def test_hub_network_error_is_a_resolve_error():
    import httpx
    client = r.HubClient.__new__(r.HubClient)
    client.token = None

    class Api:
        def model_info(self, repo_id):
            raise httpx.ConnectError("down")
    client.api = Api()
    with pytest.raises(r.ResolveError, match="network"):
        client.inspect("a/b")
