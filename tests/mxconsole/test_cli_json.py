import json

from typer.testing import CliRunner

from mxconsole.cli import app
from mxconsole.store import Store

runner = CliRunner()


def add(name="tiny"):
    Store.open().add_model(name=name, repo_id="org/tiny", revision="abc123", source="hf-supported",
                           architecture="Qwen2ForCausalLM")


def test_ls_json_is_only_json_with_lifecycle_and_null_state_without_daemon(monkeypatch):
    monkeypatch.setenv("MXC_PORT", "1")  # nothing listens
    assert json.loads(runner.invoke(app, ["ls", "--json"]).output) == []
    add()
    row = json.loads(runner.invoke(app, ["ls", "--json"]).output)[0]
    assert row["name"] == "tiny" and row["pinned"] is False and row["keep_alive"] == 300 and row["state"] == "cold" and row["daemon_running"] is False
    assert row["repo_id"] == "org/tiny"


def test_ls_human_output_ends_with_the_console_link(monkeypatch):
    monkeypatch.setenv("MXC_PORT", "1")
    add()
    assert runner.invoke(app, ["ls"]).output.strip().endswith("http://127.0.0.1:1/console")


def test_keys_ls_json_never_has_the_secret():
    key = runner.invoke(app, ["keys", "new", "notes"]).output.split()[0]
    out = runner.invoke(app, ["keys", "ls", "--json"]).output
    rows = json.loads(out)
    assert rows[0]["app"] == "notes" and rows[0]["status"] == "active" and rows[0]["prefix"] == key[:12]
    assert key not in out and "key_hash" not in out
    runner.invoke(app, ["keys", "revoke", "notes"])
    assert json.loads(runner.invoke(app, ["keys", "ls", "--json"]).output)[0]["status"] == "revoked"


def test_ls_json_reports_live_state_and_daemon_running(monkeypatch):
    from mxconsole import serve
    add()
    add("other")
    monkeypatch.setattr(serve, "live_states", lambda: {"tiny": "ready"})
    monkeypatch.setattr(serve, "daemon_running", lambda: True)
    rows = {r["name"]: r for r in json.loads(runner.invoke(app, ["ls", "--json"]).output)}
    assert rows["tiny"]["state"] == "ready" and rows["other"]["state"] == "cold"
    assert all(r["daemon_running"] is True for r in rows.values())
