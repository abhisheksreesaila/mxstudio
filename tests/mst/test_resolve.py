import json

import pytest
from fakes import CATALOG, FILES, FakeHF, make_hf

from mst import resolve as r
from mst.store import Store


def test_example_repo_is_max_catalog():
    res = r.resolve("Qwen/Qwen2.5-0.5B-Instruct", catalog=CATALOG, hf=make_hf())
    assert res.kind == "max-catalog"
    assert res.revision == "abc123"
    assert res.architecture == "Qwen2ForCausalLM"
    assert res.encodings == ["bfloat16", "float32"]


def test_other_repo_with_supported_architecture_is_hf_supported():
    res = r.resolve("someone/qwen-finetune", catalog=CATALOG, hf=make_hf())
    assert res.kind == "hf-supported"


def test_short_name_maps_to_repo():
    res = r.resolve("qwen2.5-0.5b", catalog=CATALOG, hf=make_hf())
    assert res.repo_id == "Qwen/Qwen2.5-0.5B-Instruct"


def test_unsupported_names_architecture_and_port():
    res = r.resolve("acme/weird", catalog=CATALOG, hf=make_hf())
    assert res.kind == "unsupported"
    assert res.architecture == "WeirdForCausalLM"
    assert "mst port acme/weird" in res.message


def test_missing_repo():
    res = r.resolve("nobody/nothing", catalog=CATALOG, hf=make_hf())
    assert res.kind == "not-found"
    assert "check the name" in res.message


def test_gated_repo_mentions_token():
    res = r.resolve("someone/qwen-finetune", catalog=CATALOG,
                    hf=make_hf(gated=["someone/qwen-finetune"]))
    assert res.kind == "gated"
    assert "HF_TOKEN" in res.message


def test_pull_downloads_pinned_safetensors_and_registers():
    hf = make_hf()
    store = Store.open()
    model = r.pull("qwen2.5-0.5b", store=store, catalog=CATALOG, hf=hf)
    repo, rev, patterns = hf.downloads[0]
    assert (repo, rev) == ("Qwen/Qwen2.5-0.5B-Instruct", "abc123")
    assert "model.safetensors" in patterns and "pytorch_model.bin" not in patterns
    assert model.name == "qwen2.5-0.5b"
    assert model.source == "max-catalog" and model.revision == "abc123"
    assert store.get_model("qwen2.5-0.5b").weights_path == "/cache/snap"


def test_pull_repo_id_name_is_lowercased_tail():
    hf = FakeHF({"someone/Qwen-FineTune": ("Qwen2ForCausalLM", FILES)})
    model = r.pull("someone/Qwen-FineTune", store=Store.open(), catalog=CATALOG, hf=hf)
    assert model.name == "qwen-finetune"


def test_pull_unsupported_downloads_nothing():
    hf = make_hf()
    store = Store.open()
    with pytest.raises(r.PullError) as err:
        r.pull("acme/weird", store=store, catalog=CATALOG, hf=hf)
    assert err.value.resolution.kind == "unsupported"
    assert hf.downloads == [] and store.models() == []


def test_catalog_is_cached_in_mst_home(mst_home):
    calls = []

    def runner():
        calls.append(1)
        return json.dumps({"architectures": CATALOG})

    assert r.load_catalog(runner=runner) == CATALOG
    assert r.load_catalog(runner=runner) == CATALOG
    assert len(calls) == 1
    assert (mst_home / "max_catalog.json").exists()
    r.load_catalog(runner=runner, refresh=True)
    assert len(calls) == 2
