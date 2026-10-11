"""F-024: `mxc search` and GET /admin/search. No GPU, no network: catalog, Hugging Face, GPUs and RAM are fakes."""
import json

import pytest
from typer.testing import CliRunner

from mxconsole import cli, resolve
from mxconsole.gateway import create_app
from mxconsole.search import Sources, estimate_vram_gb, fit_for, params_from_name, search
from test_console import ADMIN
from test_gateway import client, key, store, sup  # noqa: F401  (shared fixtures)

runner = CliRunner()

CATALOG = {
    "Qwen2ForCausalLM": {"example_repo_ids": ["Qwen/Qwen2.5-0.5B-Instruct", "Qwen/Qwen2.5-1.5B-Instruct",
                                              "Qwen/Qwen2.5-3B-Instruct", "Qwen/Qwen2.5-32B-Instruct"],
                         "supported_encodings": ["bfloat16"]},
    "LlamaForCausalLM": {"example_repo_ids": ["meta-llama/Llama-3.2-1B-Instruct", "meta-llama/Llama-3.1-70B-Instruct"],
                         "supported_encodings": ["bfloat16"]},
}
GB = 1024


class FakeHub:
    def __init__(self, repos=None, sizes=None, fail=None):
        self.repos, self.sizes, self.fail, self.asked = repos or {}, sizes or {}, fail, []

    def inspect(self, repo_id):
        self.asked.append(repo_id)
        if self.fail:
            raise self.fail
        if repo_id not in self.repos:
            raise resolve.RepoNotFound(repo_id)
        arch, files = self.repos[repo_id]
        return resolve.RepoInfo(sha="abc", architecture=arch, files=files)

    def param_count(self, repo_id):
        return self.sizes.get(repo_id)


def sources(*, free_gb=24, total_gb=24, hub=None, gpus=True):
    gpu = [{"index": 0, "name": "RTX Fake", "used_mb": int((total_gb - free_gb) * GB), "total_mb": total_gb * GB, "util": 0}]
    return Sources(catalog=lambda: CATALOG, hub=lambda: hub or FakeHub(), gpus=(lambda: gpu) if gpus else (lambda: []),
                   ram=lambda: (12.0, 32.0))


def test_names_carry_their_size():
    assert params_from_name("Qwen/Qwen2.5-1.5B-Instruct") == 1_500_000_000
    assert params_from_name("HuggingFaceTB/SmolLM3-3B") == 3_000_000_000
    assert params_from_name("allenai/OLMoE-1B-7B-0125-Instruct") == 7_000_000_000
    assert params_from_name("org/model-135M") == 135_000_000
    assert params_from_name("sentence-transformers/all-MiniLM-L6-v2") is None


def test_the_estimate_is_weights_times_1_2_plus_headroom_and_fit_has_four_verdicts():
    assert estimate_vram_gb(10.0) == pytest.approx(13.0)
    assert fit_for(10, 24, True) == "ok" and fit_for(20, 24, True) == "tight"
    assert fit_for(30, 24, True) == "no" and fit_for(1, 24, False) == "port" and fit_for(None, 24, True) is None


def test_a_curated_name_finds_its_model_with_every_field():
    out = search("qwen2.5-1.5b", sources())
    first = out["results"][0]
    assert first["id"] == "qwen2.5-1.5b" and first["repo_id"] == "Qwen/Qwen2.5-1.5B-Instruct"
    assert first["source"] == "max" and first["architecture"] == "Qwen2ForCausalLM" and first["supported"] is True
    assert first["params"] == 1_500_000_000 and first["size_gb"] == 3.0
    assert first["est_vram_gb"] == 4.6 and first["fit"] == "ok" and first["suggestions"] == []
    assert out["memory"] == {"basis": "gpu", "name": "RTX Fake", "free_gb": 24.0, "total_gb": 24.0}


def test_the_max_catalog_is_searched_by_repo_or_architecture():
    ids = [r["id"] for r in search("llama", sources())["results"]]
    assert "llama3.2-1b" in ids and "meta-llama/Llama-3.1-70B-Instruct" in ids
    assert [r["repo_id"] for r in search("Qwen2ForCausalLM 32b", sources())["results"]] == ["Qwen/Qwen2.5-32B-Instruct"]


def test_a_repo_id_asks_hugging_face_and_checks_the_architecture():
    hub = FakeHub({"acme/weird-7b": ("WeirdForCausalLM", ["config.json", "model.safetensors"]),
                   "acme/qwen-ft": ("Qwen2ForCausalLM", ["config.json", "model.safetensors"]),
                   "acme/pickle": ("Qwen2ForCausalLM", ["config.json", "pytorch_model.bin"])},
                  sizes={"acme/qwen-ft": 2_000_000_000})
    port = search("acme/weird-7b", sources(hub=hub))["results"][0]
    assert port["source"] == "hf" and port["supported"] is False and port["fit"] == "port"
    assert port["architecture"] == "WeirdForCausalLM" and port["params"] == 7_000_000_000
    ft = search("acme/qwen-ft", sources(hub=hub))["results"][0]
    assert ft["supported"] is True and ft["params"] == 2_000_000_000 and ft["fit"] == "ok"  # HF's count wins over the name
    assert search("acme/pickle", sources(hub=hub))["results"][0]["supported"] is False  # no safetensors
    assert search("acme/nothing", sources(hub=hub))["results"] == []


def test_plain_names_and_known_repos_never_touch_hugging_face():
    hub = FakeHub()
    search("qwen", sources(hub=hub))
    search("Qwen/Qwen2.5-0.5B-Instruct", sources(hub=hub))  # in the catalog already
    assert hub.asked == []


def test_a_network_failure_is_a_warning_not_a_crash():
    out = search("acme/model", sources(hub=FakeHub(fail=resolve.ResolveError("could not reach Hugging Face"))))
    assert out["results"] == [] and out["warnings"] == ["could not reach Hugging Face"]


def test_too_big_models_get_up_to_three_suggestions_that_fit():
    out = search("Qwen/Qwen2.5-32B-Instruct", sources(free_gb=8, total_gb=24))
    big = out["results"][0]
    assert big["fit"] == "no" and big["est_vram_gb"] == 77.8
    sug = big["suggestions"]
    assert 1 <= len(sug) <= 3
    assert all(s["est_vram_gb"] <= 8 * 0.8 and s["est_vram_gb"] < big["est_vram_gb"] for s in sug)
    assert sug[0]["architecture"] == "Qwen2ForCausalLM" and sug[0]["id"] == "qwen2.5-1.5b"  # same family first, biggest that fits (3b needs 8.2 GB)
    assert out["results"][0]["fit"] == "no"


def test_tight_when_it_only_just_fits():
    row = search("qwen2.5-3b", sources(free_gb=7, total_gb=24))["results"][0]  # est 8.2 GB > 7 free
    assert row["fit"] == "no"
    row = search("qwen2.5-3b", sources(free_gb=9, total_gb=24))["results"][0]  # 8.2 <= 9 but > 80% of 9
    assert row["fit"] == "tight"


def test_without_a_gpu_system_ram_is_used_and_said():
    out = search("qwen2.5-3b", sources(gpus=False))
    assert out["memory"]["basis"] == "ram" and out["memory"]["free_gb"] == 12.0 and "no NVIDIA GPU" in out["memory"]["name"]
    assert out["results"][0]["fit"] == "ok"


async def test_the_route_returns_the_same_answer_and_is_admin_only(store, sup):
    async with client(create_app(store, sup, api_deps={"search_sources": sources()})) as http:
        r = await http.get("/admin/search", params={"q": "qwen2.5-0.5b"}, headers=ADMIN)
        assert r.status_code == 200 and r.json()["results"][0]["id"] == "qwen2.5-0.5b"
        assert (await http.get("/admin/search", params={"q": "qwen"})).status_code == 403
        assert (await http.get("/admin/search?q=qwen", headers={**ADMIN, "X-Forwarded-For": "10.0.0.2"})).status_code == 403
        assert (await http.get("/admin/search", params={"q": "x" * 300}, headers=ADMIN)).status_code == 400


async def test_a_missing_max_is_a_503_with_the_fix(store, sup):
    def broken():
        raise resolve.ResolveError("`max` not found: run mxc inside the pixi env (pixi run mxc ...)")
    src = Sources(catalog=broken, gpus=lambda: [], ram=lambda: (1.0, 2.0))
    async with client(create_app(store, sup, api_deps={"search_sources": src})) as http:
        r = await http.get("/admin/search", params={"q": "qwen"}, headers=ADMIN)
        assert r.status_code == 503 and "pixi" in r.json()["error"]["message"]


def test_cli_search_prints_a_table_and_json(inproc_daemon, monkeypatch):
    monkeypatch.setattr("mxconsole.search.Sources", lambda: sources())
    # the daemon-less path: search runs in the CLI process, no daemon needed
    text = runner.invoke(cli.app, ["search", "qwen2.5-1.5b"])
    assert text.exit_code == 0, text.output
    assert "qwen2.5-1.5b" in text.output and "ok" in text.output and "RTX Fake" in text.output
    out = json.loads(runner.invoke(cli.app, ["search", "qwen2.5-1.5b", "--json"]).output)
    assert out["results"][0]["fit"] == "ok" and out["memory"]["basis"] == "gpu"
    none = runner.invoke(cli.app, ["search", "zzz-nothing"])
    assert none.exit_code == 0 and "no models match" in none.output
