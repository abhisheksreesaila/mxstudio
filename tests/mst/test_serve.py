"""`mst serve` end to end: a real daemon process, backed by the fake max binary."""
import shlex
import socket
import sys
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from mst.cli import app
from mst.store import Store

runner = CliRunner()
FAKE = str(Path(__file__).parent / "fake_max.py")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def daemon_env(monkeypatch):
    port = free_port()
    monkeypatch.setenv("MST_PORT", str(port))
    monkeypatch.setenv("MST_MAX_CMD", shlex.join([sys.executable, FAKE]))
    Store.open().add_model(name="tiny", repo_id="org/tiny", revision="abc123", source="hf-supported",
                           architecture="Qwen2ForCausalLM")
    yield f"http://127.0.0.1:{port}"
    runner.invoke(app, ["stop", "--all"])


def key_from(output: str) -> str:
    return next(tok for tok in output.split() if tok.startswith("mst-"))


def test_serve_starts_the_daemon_loads_the_model_and_prints_how_to_call_it(daemon_env):
    result = runner.invoke(app, ["serve", "tiny"])
    assert result.exit_code == 0, result.output
    assert f"{daemon_env}/v1" in result.output
    assert "curl" in result.output and "OpenAI(" in result.output
    key = key_from(result.output)  # a key is made on first use

    status = httpx.get(f"{daemon_env}/admin/status").json()
    assert {m["name"]: m["state"] for m in status}["tiny"] == "ready"
    r = httpx.post(f"{daemon_env}/v1/chat/completions", headers={"Authorization": f"Bearer {key}"},
                   json={"model": "tiny", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200, r.text


def test_second_serve_reuses_the_daemon_and_does_not_print_a_new_key(daemon_env):
    first = runner.invoke(app, ["serve", "tiny"])
    second = runner.invoke(app, ["serve", "tiny"])
    assert second.exit_code == 0, second.output
    assert "mst-" not in second.output.replace(key_from(first.output)[:12], "")
    assert "mst keys new" in second.output


def test_pin_and_keep_alive_are_recorded(daemon_env):
    assert runner.invoke(app, ["serve", "tiny", "--pin", "--no-wait"]).exit_code == 0
    assert Store.open().get_model("tiny").pinned is True
    assert runner.invoke(app, ["serve", "tiny", "--keep-alive", "10m", "--no-wait"]).exit_code == 0
    model = Store.open().get_model("tiny")
    assert model.keep_alive == 600 and model.pinned is False


def test_stop_unloads_one_model_and_stop_all_ends_the_daemon(daemon_env):
    runner.invoke(app, ["serve", "tiny"])
    result = runner.invoke(app, ["stop", "tiny"])
    assert result.exit_code == 0, result.output
    status = httpx.get(f"{daemon_env}/admin/status").json()
    assert {m["name"]: m["state"] for m in status}["tiny"] == "cold"
    assert runner.invoke(app, ["stop", "--all"]).exit_code == 0
    with pytest.raises(httpx.ConnectError):
        httpx.get(f"{daemon_env}/health")


def test_ls_shows_live_state_when_the_daemon_runs(daemon_env):
    runner.invoke(app, ["serve", "tiny"])
    result = runner.invoke(app, ["ls"])
    assert "ready" in result.output


def test_admin_routes_are_loopback_only_and_not_proxied(daemon_env):
    runner.invoke(app, ["serve", "tiny", "--no-wait"])
    r = httpx.get(f"{daemon_env}/admin/status", headers={"X-Forwarded-For": "192.168.1.20"})
    assert r.status_code == 403
