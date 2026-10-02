import json
from pathlib import Path

import pytest
from fakes import CATALOG, make_hf
from typer.testing import CliRunner

from mst import cli, port
from mst.store import Store, mst_home

runner = CliRunner()

CAT = {
    "LlamaForCausalLM": {"example_repo_ids": [], "supported_encodings": ["bfloat16"]},
    "Qwen2ForCausalLM": {"example_repo_ids": [], "supported_encodings": ["bfloat16"]},
    "Qwen3ForCausalLM": {"example_repo_ids": [], "supported_encodings": ["bfloat16"]},
    "OlmoForCausalLM": {"example_repo_ids": [], "supported_encodings": ["bfloat16"]},
}


def fake_skills(root: Path):
    for name in ("import-model", "debug-model"):
        (root / name).mkdir(parents=True)
        (root / name / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
    (root / "docs").mkdir()
    (root / ".git").mkdir()


class Runner:
    def __init__(self):
        self.calls = []

    def __call__(self, cmd, cwd=None):
        self.calls.append(cmd)
        if cmd[:2] == ["git", "clone"]:
            fake_skills(Path(cmd[-1]))
        return 0, ""


def test_ensure_skills_clones_then_pulls():
    r = Runner()
    path = port.ensure_skills(runner=r)
    assert path == mst_home() / "skills" / "modular"
    assert r.calls[0][:4] == ["git", "clone", "--depth", "1"]
    assert port.list_skills(path) == ["debug-model", "import-model"]
    port.ensure_skills(runner=r)
    assert r.calls[1] == ["git", "pull", "--ff-only"]


def test_ensure_skills_failure_is_explained():
    with pytest.raises(port.PortError, match="skills"):
        port.ensure_skills(runner=lambda cmd, cwd=None: (1, "no network"))


def test_pick_donor_prefers_same_family_and_version(tmp_path):
    archs = tmp_path / "architectures"
    for d in ("qwen2", "qwen3", "qwen3_modulev3", "llama3", "olmo"):
        (archs / d).mkdir(parents=True)
    d = port.pick_donor("Qwen3NextForCausalLM", "qwen3_next", CAT, arch_dir=archs)
    assert d.architecture == "Qwen3ForCausalLM"
    assert d.source_dir == archs / "qwen3"
    assert "qwen3" in d.reason


def test_pick_donor_falls_back_to_llama():
    d = port.pick_donor("ZzzForCausalLM", "zzz", CAT, arch_dir=None)
    assert d.architecture == "LlamaForCausalLM"
    assert "fallback" in d.reason.lower()


def fake_fetch(repo_id, revision):
    p = mst_home() / "dl-config.json"
    p.write_text(json.dumps({"architectures": ["WeirdForCausalLM"], "model_type": "weird",
                             "hidden_size": 64, "num_hidden_layers": 2, "vocab_size": 10, "extra": 1}))
    return p


def test_prepare_creates_workspace(tmp_path):
    archs = tmp_path / "a"
    (archs / "llama3").mkdir(parents=True)
    res = port.prepare("acme/weird", catalog=CAT, hf=make_hf(), runner=Runner(),
                       fetch_config=fake_fetch, arch_dir=archs)
    ws = res.workspace
    assert ws == mst_home() / "ports" / "weird"
    assert json.loads((ws / "config.json").read_text())["model_type"] == "weird"
    md = (ws / "PORT.md").read_text()
    for needle in ("WeirdForCausalLM", "acme/weird", "abc123", "hidden_size", "LlamaForCausalLM",
                   str(archs / "llama3"), "compare"):
        assert needle in md
    link = ws / ".claude" / "skills" / "import-model"
    assert link.is_symlink() and (link / "SKILL.md").exists()
    assert res.command == f'cd {ws} && claude "/import-model acme/weird"'
    assert res.skills == ["debug-model", "import-model"]


def test_prepare_supported_model_makes_no_workspace():
    res = port.prepare("qwen2.5-0.5b", catalog=CATALOG, hf=make_hf(), runner=Runner(), fetch_config=fake_fetch)
    assert res.workspace is None and "mst pull" in res.message
    assert not (mst_home() / "ports").exists()


def make_port(d, with_arch=True):
    d.mkdir(parents=True)
    if with_arch:
        (d / "arch.py").write_text("")
        (d / "__init__.py").write_text("ARCHITECTURES = []\n")
    return d


def test_register_port_stores_it(tmp_path):
    d = make_port(tmp_path / "weird")
    m = port.register("acme/weird", d, catalog=CAT, hf=make_hf())
    assert (m.source, m.custom_arch_path, m.weights_path) == ("port", str(d), "/cache/snap")
    assert m.architecture == "WeirdForCausalLM" and m.name == "weird"
    assert Store.open().get_model("weird") == m


def test_register_rejects_incomplete_port(tmp_path):
    d = make_port(tmp_path / "weird", with_arch=False)
    with pytest.raises(port.PortError, match="arch.py"):
        port.register("acme/weird", d, catalog=CAT, hf=make_hf())
    assert Store.open().models() == []


def _patch(monkeypatch, hf, run):
    monkeypatch.setattr("mst.resolve.load_catalog", lambda **kw: CAT)
    monkeypatch.setattr("mst.resolve.HubClient", lambda: hf)
    monkeypatch.setattr("mst.port._run", run)
    monkeypatch.setattr("mst.port._download_config", fake_fetch)


def test_cli_port_prints_command(monkeypatch):
    _patch(monkeypatch, make_hf(), Runner())
    out = runner.invoke(cli.app, ["port", "acme/weird"])
    assert out.exit_code == 0, out.output
    assert "import-model" in out.output and 'claude "/import-model acme/weird"' in out.output


def test_cli_port_supported_suggests_pull(monkeypatch):
    _patch(monkeypatch, make_hf(), Runner())
    monkeypatch.setattr("mst.resolve.load_catalog", lambda **kw: CATALOG)
    out = runner.invoke(cli.app, ["port", "qwen2.5-0.5b"])
    assert out.exit_code == 0 and "mst pull qwen2.5-0.5b" in out.output


def test_cli_pull_port_registers(monkeypatch, tmp_path):
    _patch(monkeypatch, make_hf(), Runner())
    d = make_port(tmp_path / "weird")
    out = runner.invoke(cli.app, ["pull", "acme/weird", "--port", str(d)])
    assert out.exit_code == 0, out.output
    assert Store.open().get_model("weird").source == "port"
    bad = runner.invoke(cli.app, ["pull", "acme/weird", "--port", str(tmp_path / "nope")])
    assert bad.exit_code == 1
