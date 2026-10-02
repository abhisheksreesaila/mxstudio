import json
import shlex
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
    (archs / "llama3_modulev3").mkdir()
    res = port.prepare("acme/weird", catalog=CAT, hf=make_hf(), runner=Runner(),
                       fetch_config=fake_fetch, arch_dir=archs)
    ws = res.workspace
    assert ws == mst_home() / "ports" / "weird"
    assert json.loads((ws / "config.json").read_text())["model_type"] == "weird"
    md = (ws / "PORT.md").read_text()
    for needle in ("WeirdForCausalLM", "acme/weird", "abc123", "hidden_size", "LlamaForCausalLM",
                   str(archs / "llama3"), "compare", "llama3_modulev3", "Choose the lane",
                   "scripts/scaffold.py acme/weird --start-from llama3_modulev3 --output-dir",
                   "<output_dir>/<slug>", "pixi run max serve", "skill root", "MAX versions"):
        assert needle in md
    link = ws / ".claude" / "skills" / "import-model"
    assert link.is_symlink() and (link / "SKILL.md").exists()
    assert res.command == f"cd {shlex.quote(str(ws))} && claude '/import-model acme/weird'"
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
    assert "import-model" in out.output and "claude '/import-model acme/weird'" in out.output


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


def test_prepare_twice_overwrites_cleanly():
    kw = dict(catalog=CAT, hf=make_hf(), runner=Runner(), fetch_config=fake_fetch, arch_dir=None)
    first = port.prepare("acme/weird", **kw)
    (first.workspace / "PORT.md").write_text("stale")
    second = port.prepare("acme/weird", **kw)
    assert "Choose the lane" in (second.workspace / "PORT.md").read_text()
    assert (second.workspace / ".claude" / "skills" / "import-model").is_symlink()


def test_port_dir_without_architectures_is_rejected(tmp_path):
    d = make_port(tmp_path / "weird")
    (d / "__init__.py").write_text("x = 1\n")
    with pytest.raises(port.PortError, match="ARCHITECTURES"):
        port.check_port_dir(d)


def test_port_dir_name_must_be_importable(tmp_path):
    with pytest.raises(port.PortError, match="identifier"):
        port.check_port_dir(make_port(tmp_path / "my-port"))
    with pytest.raises(port.PortError, match="identifier"):
        port.check_port_dir(make_port(tmp_path / "a:b" / "weird"))


def test_register_refuses_to_replace_another_repos_model(tmp_path):
    Store.open().add_model(name="weird", repo_id="other/weird", revision="r", source="hf", architecture="X")
    d = make_port(tmp_path / "weird")
    with pytest.raises(port.resolve.PullError, match="already registered"):
        port.register("acme/weird", d, catalog=CAT, hf=make_hf())
    assert Store.open().get_model("weird").repo_id == "other/weird"
    m = port.register("acme/weird", d, catalog=CAT, hf=make_hf(), name="weird2")
    assert m.name == "weird2"


def test_cli_pull_port_collision_and_name(monkeypatch, tmp_path):
    _patch(monkeypatch, make_hf(), Runner())
    Store.open().add_model(name="weird", repo_id="other/weird", revision="r", source="hf", architecture="X")
    d = make_port(tmp_path / "weird")
    out = runner.invoke(cli.app, ["pull", "acme/weird", "--port", str(d)])
    assert out.exit_code == 1 and "--name" in out.output
    ok = runner.invoke(cli.app, ["pull", "acme/weird", "--port", str(d), "--name", "weird-port"])
    assert ok.exit_code == 0 and Store.open().get_model("weird-port").source == "port"


def _boom_hf():
    hf = make_hf()

    def boom(*a, **k):
        raise port.resolve.ResolveError("could not reach Hugging Face")
    hf.inspect = boom
    return hf


def test_cli_pull_port_hf_error_is_not_a_traceback(monkeypatch, tmp_path):
    _patch(monkeypatch, _boom_hf(), Runner())
    out = runner.invoke(cli.app, ["pull", "acme/weird", "--port", str(make_port(tmp_path / "weird"))])
    assert out.exit_code == 1 and "could not reach" in out.output
    assert isinstance(out.exception, SystemExit)


def test_cli_pull_port_without_safetensors(monkeypatch, tmp_path):
    from fakes import FakeHF
    hf = FakeHF({"acme/weird": ("WeirdForCausalLM", ["config.json", "pytorch_model.bin"])})
    _patch(monkeypatch, hf, Runner())
    out = runner.invoke(cli.app, ["pull", "acme/weird", "--port", str(make_port(tmp_path / "weird"))])
    assert out.exit_code == 1 and "safetensors" in out.output and isinstance(out.exception, SystemExit)


def test_cli_port_hf_error_is_not_a_traceback(monkeypatch):
    _patch(monkeypatch, _boom_hf(), Runner())
    out = runner.invoke(cli.app, ["port", "acme/weird"])
    assert out.exit_code == 1 and "could not reach" in out.output and isinstance(out.exception, SystemExit)
