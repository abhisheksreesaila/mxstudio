import nbformat
from typer.testing import CliRunner

from hotfire import learn
from hotfire.cli import app
from hotfire.learn import HFInfo
from hotfire.store import Store

runner = CliRunner()

# Qwen2.5-0.5B-like config; safetensors total as Hugging Face reports it.
CONFIG = {
    "model_type": "qwen2", "architectures": ["Qwen2ForCausalLM"], "hidden_size": 896,
    "num_hidden_layers": 24, "num_attention_heads": 14, "num_key_value_heads": 2,
    "intermediate_size": 4864, "vocab_size": 151936, "max_position_embeddings": 32768,
    "rope_theta": 1000000.0, "tie_word_embeddings": True,
}


class FakeHF:
    def __init__(self, config=CONFIG, total=494_032_768):
        self._config, self._total = config, total
        self.calls = []

    def info(self, repo_id, revision):
        self.calls.append(("info", repo_id, revision))
        return HFInfo(repo_id=repo_id, revision="abc123", pipeline_tag="text-generation", license="apache-2.0",
                      downloads=1234567, likes=987, safetensors_total=self._total)

    def card_text(self, repo_id, revision):
        return "# Qwen2.5\n\nA small instruct model.\n" + ("filler line\n" * 2000)

    def config(self, repo_id, revision):
        return self._config


def text_of(nb):
    return "\n".join(c.source for c in nb.cells)


def build(source=None, finder=lambda arch: []):
    return learn.build_notebook(model_id="qwen2.5-0.5b", repo_id="Qwen/Qwen2.5-0.5B-Instruct",
                                source=source or FakeHF(), max_finder=finder)


def test_notebook_is_valid_and_describes_the_model():
    nb = build()
    nbformat.validate(nb)
    text = text_of(nb)
    for needle in ("apache-2.0", "text-generation", "1,234,567", "987", "494.03M", "A small instruct model"):
        assert needle in text


def test_model_card_is_trimmed():
    nb = build()
    assert "model card trimmed" in text_of(nb)
    assert len(text_of(nb)) < 40_000


def test_architecture_table_has_gqa_and_explanations():
    text = text_of(build())
    for needle in ("hidden_size", "896", "num_hidden_layers", "24", "num_key_value_heads", "7:1",
                   "rope_theta", "tie_word_embeddings", "grouped-query attention"):
        assert needle in text


def test_parameter_breakdown_matches_safetensors_total():
    b = learn.parameter_breakdown(CONFIG)
    assert abs(b["total"] - 494_032_768) / 494_032_768 < 0.02
    assert b["lm_head"] == 0  # tied
    text = text_of(build())
    assert "safetensors total" in text and "close enough" in text


def test_breakdown_flags_a_bad_estimate():
    text = text_of(build(FakeHF(total=5_000_000_000)))
    assert "estimate is off" in text


def test_missing_config_degrades_gracefully():
    nb = build(FakeHF(config={}, total=None))
    nbformat.validate(nb)
    assert "No `config.json`" in text_of(nb)


def test_sample_code_uses_env_and_covers_three_styles():
    nb = build()
    code = "\n".join(c.source for c in nb.cells if c.cell_type == "code")
    assert 'os.environ.get("HOTFIRE_BASE_URL", "http://127.0.0.1:11435/v1")' in code
    assert 'os.environ["HOTFIRE_API_KEY"]' in code
    assert "from openai import OpenAI" in code
    assert "stream=True" in code
    assert "import httpx" in code
    assert 'MODEL = "qwen2.5-0.5b"' in code


def test_max_implementation_pointer(tmp_path):
    root = tmp_path / "architectures"
    (root / "qwen2").mkdir(parents=True)
    (root / "qwen2" / "arch.py").write_text('name = "Qwen2ForCausalLM"')
    (root / "llama3").mkdir()
    (root / "llama3" / "arch.py").write_text('name = "LlamaForCausalLM"')
    assert learn.find_max_implementation("Qwen2ForCausalLM", root) == [root / "qwen2"]
    assert learn.find_max_implementation("NopeForCausalLM", root) == []
    found = text_of(build(finder=lambda a: learn.find_max_implementation(a, root)))
    assert str(root / "qwen2") in found
    missing = text_of(build(finder=lambda a: []))
    assert "was not found in the installed `max` package" in missing


def test_cli_writes_notebook_for_registered_model(hotfire_home, monkeypatch):
    Store.open().add_model(name="qwen2.5-0.5b", repo_id="Qwen/Qwen2.5-0.5B-Instruct", revision="rev1",
                           source="max-catalog", architecture="Qwen2ForCausalLM")
    fake = FakeHF()
    monkeypatch.setattr(learn, "default_source", lambda: fake)
    result = runner.invoke(app, ["learn", "qwen2.5-0.5b", "--no-open"])
    assert result.exit_code == 0, result.output
    path = hotfire_home / "notebooks" / "qwen2.5-0.5b.ipynb"
    assert path.exists() and str(path) in result.output
    nbformat.validate(nbformat.read(path, as_version=4))
    assert fake.calls == [("info", "Qwen/Qwen2.5-0.5B-Instruct", "rev1")]


def test_cli_accepts_raw_repo_id(hotfire_home, monkeypatch):
    monkeypatch.setattr(learn, "default_source", lambda: FakeHF())
    result = runner.invoke(app, ["learn", "Qwen/Qwen2.5-0.5B-Instruct", "--no-open"])
    assert result.exit_code == 0, result.output
    assert (hotfire_home / "notebooks" / "qwen2.5-0.5b.ipynb").exists()


def test_cli_reports_hf_failure(monkeypatch):
    class Boom(FakeHF):
        def info(self, repo_id, revision):
            raise RuntimeError("404 not found")
    monkeypatch.setattr(learn, "default_source", lambda: Boom())
    result = runner.invoke(app, ["learn", "no/such-model", "--no-open"])
    assert result.exit_code == 1
    assert "no/such-model" in result.output


def test_hubsource_card_text_downloads_readme(tmp_path, monkeypatch):
    import huggingface_hub
    readme = tmp_path / "README.md"
    readme.write_text("---\nlicense: mit\n---\n# Hello card\n")
    seen = {}

    def fake_download(repo_id, filename, **kw):
        seen.update(repo_id=repo_id, filename=filename, **kw)
        return str(readme)

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", fake_download)
    text = learn.HubSource().card_text("a/b", "rev9")
    assert "Hello card" in text and "license" not in text
    assert seen["filename"] == "README.md" and seen["revision"] == "rev9"


def test_model_name_for_pulled_and_unpulled_models(monkeypatch):
    monkeypatch.setattr(learn, "default_source", lambda: FakeHF())
    # curated alias, not pulled yet
    nb = learn.build_notebook(model_id="qwen2.5-0.5b", repo_id="Qwen/Qwen2.5-0.5B-Instruct",
                              source=FakeHF(), registered=False, max_finder=lambda a: [])
    assert "hotfire pull qwen2.5-0.5b" in text_of(nb)
    assert learn.resolve("qwen2.5-0.5b") == ("qwen2.5-0.5b", "Qwen/Qwen2.5-0.5B-Instruct", None, False)
    assert learn.resolve("Qwen/Qwen2.5-0.5B-Instruct")[0] == "qwen2.5-0.5b"  # curated alias, as hotfire pull registers it
    assert learn.resolve("Foo/Bar-7B")[0] == "bar-7b"
    # pulled: looked up by repo id returns the registered name
    Store.open().add_model(name="qwen2.5-0.5b", repo_id="Qwen/Qwen2.5-0.5B-Instruct", revision="r",
                           source="max-catalog", architecture="Qwen2ForCausalLM")
    assert learn.resolve("Qwen/Qwen2.5-0.5B-Instruct") == ("qwen2.5-0.5b", "Qwen/Qwen2.5-0.5B-Instruct", "r", True)
    nb = learn.build_notebook(model_id="qwen2.5-0.5b", repo_id="x/y", source=FakeHF(),
                              max_finder=lambda a: [])
    assert "hotfire pull" not in text_of(nb)


def test_base_url_default_comes_from_settings(monkeypatch):
    monkeypatch.setattr(learn, "daemon_url", lambda: "http://127.0.0.1:9999")
    code = "\n".join(c.source for c in build().cells if c.cell_type == "code")
    assert '"http://127.0.0.1:9999/v1"' in code


def test_cli_launches_jupyter_detached(monkeypatch):
    import subprocess
    monkeypatch.setattr(learn, "default_source", lambda: FakeHF())
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: calls.append((cmd, kw)))
    result = runner.invoke(app, ["learn", "Qwen/Qwen2.5-0.5B-Instruct"])
    assert result.exit_code == 0, result.output
    cmd, kw = calls[0]
    assert cmd[:3] == ["jupyter", "lab", cmd[2]] and cmd[2].endswith(".ipynb")
    assert kw["start_new_session"] is True


def test_cli_without_jupyter_prints_fallback(monkeypatch):
    import subprocess
    monkeypatch.setattr(learn, "default_source", lambda: FakeHF())

    def missing(*a, **k):
        raise FileNotFoundError("jupyter")
    monkeypatch.setattr(subprocess, "Popen", missing)
    result = runner.invoke(app, ["learn", "Qwen/Qwen2.5-0.5B-Instruct"])
    assert result.exit_code == 0
    assert "pixi run jupyter lab" in result.output and ".ipynb" in result.output
