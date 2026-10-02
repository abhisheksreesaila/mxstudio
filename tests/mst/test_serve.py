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


def mst_home_path() -> Path:
    from mst.store import mst_home
    return mst_home()


def key_from(output: str) -> str:
    return next(tok for tok in output.split() if tok.startswith("mst-"))


def test_serve_starts_the_daemon_loads_the_model_and_prints_how_to_call_it(daemon_env):
    result = runner.invoke(app, ["serve", "tiny"])
    assert result.exit_code == 0, result.output
    assert f"{daemon_env}/v1" in result.output
    assert "curl" in result.output and "OpenAI(" in result.output
    key = key_from(result.output)  # a key is made on first use

    status = httpx.get(f"{daemon_env}/admin/status", headers={"X-Mst-Admin": "1"}).json()
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
    status = httpx.get(f"{daemon_env}/admin/status", headers={"X-Mst-Admin": "1"}).json()
    assert {m["name"]: m["state"] for m in status}["tiny"] == "cold"
    runner.invoke(app, ["serve", "tiny"])
    pids = [int(p.name.split(".")[1]) for p in (mst_home_path() / "run").glob("tiny.*.pid")]
    assert pids
    assert runner.invoke(app, ["stop", "--all"]).exit_code == 0
    with pytest.raises(httpx.ConnectError):
        httpx.get(f"{daemon_env}/health")
    assert not any(Path(f"/proc/{pid}").exists() for pid in pids)  # no max child outlives the daemon
    assert not list((mst_home_path() / "run").glob("tiny.*.pid"))


def test_ls_shows_live_state_when_the_daemon_runs(daemon_env):
    runner.invoke(app, ["serve", "tiny"])
    result = runner.invoke(app, ["ls"])
    assert "ready" in result.output


def test_admin_routes_are_loopback_only_and_not_proxied(daemon_env):
    runner.invoke(app, ["serve", "tiny", "--no-wait"])
    admin = {"X-Mst-Admin": "1"}
    assert httpx.get(f"{daemon_env}/admin/status", headers=admin).status_code == 200
    for extra in ({"X-Forwarded-For": "192.168.1.20"}, {"Forwarded": "for=192.168.1.20"}):
        assert httpx.get(f"{daemon_env}/admin/status", headers={**admin, **extra}).status_code == 403


def test_admin_routes_refuse_browser_pages(daemon_env):
    """A web page on this machine can send simple cross-site POSTs, or rebind DNS to 127.0.0.1."""
    runner.invoke(app, ["serve", "tiny", "--no-wait"])
    # simple request from a page: no custom header possible without a CORS preflight
    r = httpx.post(f"{daemon_env}/admin/shutdown", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    # DNS rebinding: same-origin to the page, so it can add headers, but Host is the attacker's name
    r = httpx.get(f"{daemon_env}/admin/status", headers={"X-Mst-Admin": "1", "Host": "evil.example"})
    assert r.status_code == 403
    assert httpx.get(f"{daemon_env}/health").status_code == 200  # still running


def test_concurrent_serves_print_one_working_key(daemon_env):
    import subprocess  # two real `mst serve` processes racing, as from two terminals
    procs = [subprocess.Popen([sys.executable, "-m", "mst", "serve", "tiny"], stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True) for _ in range(2)]
    outs = [p.communicate(timeout=60)[0] for p in procs]
    found = sorted({tok.strip("'\"") for out in outs for tok in out.split() if tok.startswith("mst-")})
    assert len(found) == 1  # one key, printed by exactly one of the two runs
    assert sum("shown once" in out for out in outs) == 1
    r = httpx.post(f"{daemon_env}/v1/chat/completions", headers={"Authorization": f"Bearer {found[0]}"},
                   json={"model": "tiny", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200


def test_bad_keep_alive_is_a_one_line_error(daemon_env):
    result = runner.invoke(app, ["serve", "tiny", "--keep-alive", "abc", "--no-wait"])
    assert result.exit_code == 1
    assert "keep-alive" in result.output


def test_stop_without_daemon_is_calm(daemon_env):
    assert runner.invoke(app, ["stop", "tiny"]).exit_code == 0
    assert runner.invoke(app, ["stop", "--all"]).exit_code == 0
