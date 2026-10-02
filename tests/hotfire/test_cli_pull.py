from fakes import CATALOG, make_hf
from typer.testing import CliRunner

from hotfire import cli
from hotfire.store import Store

runner = CliRunner()


def _patch(monkeypatch, hf):
    monkeypatch.setattr("hotfire.resolve.load_catalog", lambda **kw: CATALOG)
    monkeypatch.setattr("hotfire.resolve.HubClient", lambda: hf)


def test_pull_registers_model(monkeypatch):
    _patch(monkeypatch, make_hf())
    result = runner.invoke(cli.app, ["pull", "qwen2.5-0.5b"])
    assert result.exit_code == 0, result.output
    assert "qwen2.5-0.5b" in result.output
    assert Store.open().get_model("qwen2.5-0.5b") is not None


def test_pull_unsupported_prints_architecture_and_port(monkeypatch):
    hf = make_hf()
    _patch(monkeypatch, hf)
    result = runner.invoke(cli.app, ["pull", "acme/weird"])
    assert result.exit_code == 1
    assert "WeirdForCausalLM" in result.output and "mst port acme/weird" in result.output
    assert hf.downloads == []


def test_pull_missing_gives_fix(monkeypatch):
    _patch(monkeypatch, make_hf())
    result = runner.invoke(cli.app, ["pull", "nobody/nothing"])
    assert result.exit_code == 1 and "check the name" in result.output


def test_dry_run_resolves_only(monkeypatch):
    hf = make_hf()
    _patch(monkeypatch, hf)
    result = runner.invoke(cli.app, ["pull", "qwen2.5-0.5b", "--dry-run"])
    assert result.exit_code == 0
    assert "max-catalog" in result.output
    assert hf.downloads == [] and Store.open().models() == []
