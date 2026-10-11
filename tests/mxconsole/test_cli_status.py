import json
import webbrowser

from typer.testing import CliRunner

from mxconsole.cli import app
from tests.mxconsole.test_serve import daemon_env  # noqa: F401  (real daemon backed by the fake max)

runner = CliRunner()


def test_status_without_daemon_says_so_and_json_is_only_json(monkeypatch):
    monkeypatch.setenv("MXC_PORT", "1")
    out = runner.invoke(app, ["status"]).output
    assert "not running" in out and "not exposed" in out and "http://127.0.0.1:1/console" in out
    data = json.loads(runner.invoke(app, ["status", "--json"]).output)
    assert data["daemon"] == {"running": False, "uptime_seconds": None}
    assert data["models"] == {"total": 0, "by_state": {}} and data["active_keys"] == 0
    assert data["lan"]["exposed"] is False and "max_version" in data


def test_status_with_a_real_daemon(daemon_env):  # noqa: F811
    runner.invoke(app, ["keys", "new", "notes"])
    assert runner.invoke(app, ["serve", "tiny"]).exit_code == 0
    data = json.loads(runner.invoke(app, ["status", "--json"]).output)
    assert data["daemon"]["running"] and data["daemon"]["uptime_seconds"] >= 0
    assert data["models"]["by_state"] == {"ready": 1} and data["active_keys"] >= 1
    assert data["endpoint"] == f"{daemon_env}/v1" and data["console"] == f"{daemon_env}/console"
    out = runner.invoke(app, ["status"]).output
    assert "running, up" in out and "1 (1 ready)" in out


def test_serve_prints_the_console_link(daemon_env):  # noqa: F811
    out = runner.invoke(app, ["serve", "tiny"]).output
    assert f"{daemon_env}/console" in out


def test_console_prints_link_opens_it_and_starts_the_daemon(daemon_env, monkeypatch):  # noqa: F811
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))
    r = runner.invoke(app, ["console", "daemon"])  # an old page name: it lives in Settings now
    assert r.exit_code == 0 and r.output.strip() == f"{daemon_env}/console#settings"
    assert opened == [f"{daemon_env}/console#settings"]
    import httpx
    assert httpx.get(f"{daemon_env}/health").status_code == 200
    opened.clear()
    r = runner.invoke(app, ["console", "--no-open"])
    assert r.output.strip() == f"{daemon_env}/console" and opened == []


def test_console_maps_pages_and_models_to_blades(daemon_env):  # noqa: F811
    def link(*args):
        r = runner.invoke(app, ["console", *args, "--no-open"])
        assert r.exit_code == 0, r.output
        return r.output.strip().removeprefix(daemon_env)
    assert link("keys") == "/console#keys" and link("pull") == "/console#add"
    assert link("model", "tiny") == "/console#model/tiny" and link("bench", "tiny") == "/console#bench/tiny"
    assert link("logs", "tiny") == "/console#logs/tiny" and link("notebook", "tiny") == "/console#notebook/tiny"
    assert link("api") == "/docs"
    r = runner.invoke(app, ["console", "nope", "--no-open"])
    assert r.exit_code != 0 and "no console page" in r.output


def test_console_unknown_page_is_a_one_line_error():
    r = runner.invoke(app, ["console", "nope", "--no-open"])
    assert r.exit_code == 1 and "metrics" in r.output and len(r.output.strip().splitlines()) == 1
