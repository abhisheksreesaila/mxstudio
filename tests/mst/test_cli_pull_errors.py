from fakes import CATALOG, make_hf
from typer.testing import CliRunner

from mst import cli, resolve

runner = CliRunner()


def _patch(monkeypatch, hf):
    monkeypatch.setattr("mst.resolve.load_catalog", lambda **kw: CATALOG)
    monkeypatch.setattr("mst.resolve.HubClient", lambda: hf)


def test_pull_no_safetensors_one_line(monkeypatch):
    _patch(monkeypatch, make_hf())
    result = runner.invoke(cli.app, ["pull", "acme/pickle"])
    assert result.exit_code == 1 and "safetensors" in result.output and "Traceback" not in result.output


def test_pull_resolve_error_is_one_line(monkeypatch):
    def boom(**kw):
        raise resolve.ResolveError("`max` not found")
    monkeypatch.setattr("mst.resolve.load_catalog", boom)
    monkeypatch.setattr("mst.resolve.HubClient", lambda: make_hf())
    result = runner.invoke(cli.app, ["pull", "qwen2.5-0.5b"])
    assert result.exit_code == 1 and "`max` not found" in result.output


def test_pull_name_collision_and_name_flag(monkeypatch):
    _patch(monkeypatch, make_hf())
    assert runner.invoke(cli.app, ["pull", "alice/foo"]).exit_code == 0
    clash = runner.invoke(cli.app, ["pull", "bob/foo"])
    assert clash.exit_code == 1 and "--name" in clash.output
    assert runner.invoke(cli.app, ["pull", "bob/foo", "--name", "bobfoo"]).exit_code == 0
