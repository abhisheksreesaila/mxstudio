from typer.testing import CliRunner

from mxconsole.cli import app
from mxconsole.store import Store

runner = CliRunner()


def test_help_lists_core_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "ls" in result.output


def test_ls_with_no_models_says_so(mxc_home):
    result = runner.invoke(app, ["ls"])
    assert result.exit_code == 0
    assert "no models yet" in result.output
    assert (mxc_home / "mst.db").exists()


def test_ls_shows_registered_models():
    Store.open().add_model(
        name="qwen2.5-0.5b",
        repo_id="Qwen/Qwen2.5-0.5B-Instruct",
        revision="abc123",
        source="max-catalog",
        architecture="Qwen2ForCausalLM",
    )
    result = runner.invoke(app, ["ls"])
    assert result.exit_code == 0
    assert "qwen2.5-0.5b" in result.output
    assert "max-catalog" in result.output
    assert "5m" in result.output  # default keep_alive
