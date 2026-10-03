"""`mxc ps`: the crew, from the same data as the Daemon page."""
import json
import subprocess
import sys

from typer.testing import CliRunner

from mxconsole.cli import app
from tests.mxconsole.test_serve import daemon_env  # noqa: F401  (real daemon backed by the fake max)

runner = CliRunner()


def test_ps_without_the_daemon_shows_what_pid_files_know(mxc_home, monkeypatch):
    monkeypatch.setenv("MXC_PORT", "1")
    monkeypatch.setenv("MXC_MAX_CMD", f"{sys.executable} -c 'import time; time.sleep(30)'")
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", "serve", "--port", "6001"])
    try:
        (mxc_home / "run").mkdir(parents=True)
        (mxc_home / "run" / f"tiny.{child.pid}.pid").write_text(f"{child.pid} {child.pid}")
        crew = json.loads(runner.invoke(app, ["ps", "--json"]).output)
        by = {c["id"]: c for c in crew}
        assert by["daemon"]["state"] == "stopped" and by["daemon"]["pid"] is None
        assert by["model:tiny"]["pid"] == child.pid and by["model:tiny"]["port"] == 6001
        assert by["model:tiny"]["rss_mb"] > 0
        out = runner.invoke(app, ["ps"]).output
        assert "max serve · tiny" in out and "stopped" in out and "#daemon" in out
    finally:
        child.kill()
        child.wait()


def test_ps_with_a_running_daemon_shows_its_pid_and_each_model(daemon_env):  # noqa: F811
    assert runner.invoke(app, ["serve", "tiny"]).exit_code == 0
    crew = {c["id"]: c for c in json.loads(runner.invoke(app, ["ps", "--json"]).output)}
    assert crew["daemon"]["state"] == "running" and crew["daemon"]["pid"] > 0
    assert crew["daemon"]["port"] == int(daemon_env.rsplit(":", 1)[1])
    assert crew["model:tiny"]["state"] == "ready" and crew["model:tiny"]["port"]
    assert crew["caddy"]["state"] == "stopped"


def test_ps_json_is_only_json(mxc_home, monkeypatch):
    monkeypatch.setenv("MXC_PORT", "1")
    out = runner.invoke(app, ["ps", "--json"]).output
    assert [c["id"] for c in json.loads(out)] == ["daemon", "caddy", "litellm"]


def test_ps_never_takes_the_daemon_lock(mxc_home, monkeypatch):
    import fcntl
    monkeypatch.setenv("MXC_PORT", "1")
    (mxc_home / "run").mkdir(parents=True)
    with open(mxc_home / "run" / "daemon.lock", "a+") as starting:  # a daemon taking the lock right now
        fcntl.flock(starting, fcntl.LOCK_EX)
        assert runner.invoke(app, ["ps", "--json"]).exit_code == 0
        fcntl.flock(starting, fcntl.LOCK_EX | fcntl.LOCK_NB)  # still ours: ps neither released nor stole it
