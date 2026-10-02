import shutil
import sys
import subprocess

import pytest
import yaml
from typer.testing import CliRunner

from hotfire import expose
from hotfire.cli import app
from hotfire.store import Store

runner = CliRunner()
# A stand-in LiteLLM: records its environment and opens the LiteLLM port like the real one.
FAKE_LITELLM_TEMPLATE = FAKE_LITELLM = (f"sh -c 'env > \"$HOTFIRE_HOME/litellm.env\"; exec {sys.executable} -m http.server "
                f"$HOTFIRE_LITELLM_PORT --bind 127.0.0.1'")


@pytest.fixture(autouse=True)
def litellm_port(monkeypatch):
    """Each test gets a free LiteLLM port, so tests never collide with a real LiteLLM on 4000."""
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    monkeypatch.setattr(expose, "LITELLM_PORT", port)
    # LiteLLM gets a minimal env, so the fake can't read its port from it: bake it in per test.
    monkeypatch.setattr(sys.modules[__name__], "FAKE_LITELLM", FAKE_LITELLM_TEMPLATE.replace("$HOTFIRE_LITELLM_PORT", str(port)))
    return port


def test_caddyfile_checks_app_keys_then_swaps_in_the_litellm_master_key():
    text = expose.render_caddyfile(hosts=["box.local", "localhost"], port=8443, litellm=True)
    assert "https://box.local:8443, https://localhost:8443" in text
    assert "tls internal" in text
    assert "forward_auth 127.0.0.1:11435" in text
    assert "uri /auth/check" in text
    assert f"reverse_proxy 127.0.0.1:{expose.LITELLM_PORT}" in text
    assert 'header_up Authorization "Bearer {env.LITELLM_MASTER_KEY}"' in text


def test_caddyfile_without_litellm_points_at_the_daemon_and_keeps_the_app_key():
    text = expose.render_caddyfile(hosts=["box.local"], port=8443, litellm=False)
    assert "reverse_proxy 127.0.0.1:11435" in text
    assert "header_up Authorization" not in text


@pytest.mark.skipif(not shutil.which("caddy"), reason="caddy not installed")
def test_caddyfile_is_valid_caddy_config(tmp_path):
    path = tmp_path / "Caddyfile"
    path.write_text(expose.render_caddyfile(hosts=["box.local"], port=8443, litellm=True))
    result = subprocess.run(["caddy", "adapt", "--config", str(path), "--adapter", "caddyfile"],
                            capture_output=True, text=True, env={"LITELLM_MASTER_KEY": "x", "PATH": "/usr/bin"})
    assert result.returncode == 0, result.stderr


def test_litellm_config_lists_each_registered_model_and_routes_it_to_the_daemon():
    store = Store.open()
    store.add_model(name="qwen2.5-0.5b", repo_id="Qwen/Qwen2.5-0.5B-Instruct", revision="r",
                    source="max-catalog", architecture="Qwen2ForCausalLM")
    config = yaml.safe_load(expose.render_litellm_config(store.models()))
    names = [m["model_name"] for m in config["model_list"]]
    assert names == ["qwen2.5-0.5b"]
    first = config["model_list"][0]["litellm_params"]
    assert first["model"] == "openai/qwen2.5-0.5b"
    assert first["api_base"] == "http://127.0.0.1:11435/v1"
    assert first["api_key"] == "os.environ/HOTFIRE_UPSTREAM_KEY"
    assert config["general_settings"]["master_key"] == "os.environ/LITELLM_MASTER_KEY"


def test_expose_dry_run_writes_configs_and_prints_the_url(hotfire_home):
    result = runner.invoke(app, ["expose", "--dry-run", "--host", "box.local"])
    assert result.exit_code == 0, result.output
    assert "https://box.local:8443/v1" in result.output
    assert (hotfire_home / "expose" / "Caddyfile").exists()
    assert (hotfire_home / "expose" / "litellm.yaml").exists()
    assert "root.crt" in result.output  # how to trust the CA on another device


def test_expose_starts_and_stops_both_processes(hotfire_home, monkeypatch):
    monkeypatch.setenv("HOTFIRE_CADDY_CMD", "sleep 60")
    monkeypatch.setenv("HOTFIRE_LITELLM_CMD", FAKE_LITELLM)
    result = runner.invoke(app, ["expose", "--host", "box.local"])
    assert result.exit_code == 0, result.output
    running = expose.status()
    assert running == {"caddy": True, "litellm": True}

    result = runner.invoke(app, ["expose", "--stop"])
    assert result.exit_code == 0, result.output
    assert expose.status() == {"caddy": False, "litellm": False}


def test_expose_no_litellm_starts_only_caddy(hotfire_home, monkeypatch):
    monkeypatch.setenv("HOTFIRE_CADDY_CMD", "sleep 60")
    monkeypatch.setenv("HOTFIRE_LITELLM_CMD", FAKE_LITELLM)
    result = runner.invoke(app, ["expose", "--no-litellm", "--host", "box.local"])
    assert result.exit_code == 0, result.output
    assert expose.status() == {"caddy": True, "litellm": False}
    runner.invoke(app, ["expose", "--stop"])


def test_refresh_restarts_litellm_with_new_models_only_when_exposed(hotfire_home, monkeypatch):
    monkeypatch.setenv("HOTFIRE_CADDY_CMD", "sleep 60")
    monkeypatch.setenv("HOTFIRE_LITELLM_CMD", FAKE_LITELLM)
    assert expose.refresh_if_running() is False
    runner.invoke(app, ["expose", "--host", "box.local"])
    Store.open().add_model(name="smollm2-1.7b", repo_id="HuggingFaceTB/SmolLM2-1.7B-Instruct", revision="r",
                           source="hf-supported", architecture="LlamaForCausalLM")
    assert expose.refresh_if_running() is True
    config = yaml.safe_load((hotfire_home / "expose" / "litellm.yaml").read_text())
    assert [m["model_name"] for m in config["model_list"]] == ["smollm2-1.7b"]
    assert "https://box.local:8443" in (hotfire_home / "expose" / "Caddyfile").read_text()
    runner.invoke(app, ["expose", "--stop"])


def test_caddy_only_forwards_the_openai_routes_so_keys_never_reach_litellm_admin():
    for litellm in (True, False):
        text = expose.render_caddyfile(hosts=["box.local"], port=8443, litellm=litellm)
        assert "@api path /v1/models /v1/chat/completions /v1/completions /v1/embeddings" in text
        assert "handle @api" in text
        assert "respond 404" in text


@pytest.mark.skipif(not shutil.which("caddy"), reason="caddy not installed")
def test_caddy_answers_404_for_litellm_admin_paths(tmp_path):
    path = tmp_path / "Caddyfile"
    path.write_text(expose.render_caddyfile(hosts=["box.local"], port=8443, litellm=True))
    adapted = subprocess.run(["caddy", "adapt", "--config", str(path), "--adapter", "caddyfile"],
                             capture_output=True, text=True, env={"LITELLM_MASTER_KEY": "x", "PATH": "/usr/bin"})
    assert adapted.returncode == 0, adapted.stderr
    assert '"/v1/models"' in adapted.stdout and '"/model/info"' not in adapted.stdout


def test_bad_hostnames_are_refused():
    for bad in ["box local", "box{.local", "", "a;b"]:
        with pytest.raises(ValueError):
            expose.render_caddyfile(hosts=[bad], port=8443, litellm=True)


def test_master_key_is_private_never_empty_and_never_printed(hotfire_home, monkeypatch):
    monkeypatch.setenv("HOTFIRE_CADDY_CMD", "sleep 60")
    monkeypatch.setenv("HOTFIRE_LITELLM_CMD", FAKE_LITELLM)
    d = hotfire_home / "expose"
    d.mkdir(parents=True)
    (d / "litellm_master_key").write_text("")  # left behind by a crash
    result = runner.invoke(app, ["expose", "--host", "box.local"])
    key = (d / "litellm_master_key").read_text().strip()
    assert len(key) >= 32
    assert ((d / "litellm_master_key").stat().st_mode & 0o777) == 0o600
    assert key not in result.output
    runner.invoke(app, ["expose", "--stop"])


def test_upstream_key_is_reserved_rotated_and_revoked_on_stop(hotfire_home, monkeypatch):
    from hotfire import keys
    monkeypatch.setenv("HOTFIRE_CADDY_CMD", "sleep 60")
    monkeypatch.setenv("HOTFIRE_LITELLM_CMD", FAKE_LITELLM)
    with pytest.raises(ValueError):
        keys.create_key(Store.open(), expose.UPSTREAM_APP)  # users can't take the reserved name
    runner.invoke(app, ["expose", "--host", "box.local"])
    first = expose._upstream_key_for_tests
    runner.invoke(app, ["expose", "--host", "box.local"])
    second = expose._upstream_key_for_tests
    store = Store.open()
    assert keys.verify_key(store, first) is None
    assert keys.verify_key(store, second) == expose.UPSTREAM_APP
    runner.invoke(app, ["expose", "--stop"])
    assert keys.verify_key(store, second) is None


def test_a_reused_pid_is_not_mistaken_for_our_process(hotfire_home):
    import os
    d = hotfire_home / "expose"
    d.mkdir(parents=True)
    (d / "caddy.pid").write_text(f"{os.getpid()} 1")  # some other live process, wrong start time
    assert expose.status()["caddy"] is False
    expose.stop()  # must not signal it
    assert os.getpid()  # still alive


def test_a_process_that_dies_at_once_is_reported_with_its_log(hotfire_home, monkeypatch):
    monkeypatch.setenv("HOTFIRE_CADDY_CMD", "sh -c 'echo address already in use; exit 1'")
    result = runner.invoke(app, ["expose", "--no-litellm", "--host", "box.local"])
    assert result.exit_code == 1
    assert "caddy exited" in result.output
    assert "address already in use" in result.output


def test_a_missing_binary_is_a_one_line_error(hotfire_home, monkeypatch):
    monkeypatch.setenv("HOTFIRE_CADDY_CMD", "/nonexistent/caddy")
    result = runner.invoke(app, ["expose", "--no-litellm", "--host", "box.local"])
    assert result.exit_code == 1
    assert "not found" in result.output


def test_litellm_gets_only_the_env_it_needs(hotfire_home, monkeypatch):
    monkeypatch.setenv("HOTFIRE_CADDY_CMD", "sleep 60")
    monkeypatch.setenv("HOTFIRE_LITELLM_CMD", FAKE_LITELLM.replace('"$HOTFIRE_HOME', f'"{hotfire_home}'))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    result = runner.invoke(app, ["expose", "--host", "box.local"])
    assert result.exit_code == 0, result.output
    env = (hotfire_home / "litellm.env").read_text()
    assert "LITELLM_MASTER_KEY=" in env and "HOTFIRE_UPSTREAM_KEY=" in env
    assert "sk-should-not-leak" not in env
    runner.invoke(app, ["expose", "--stop"])


def test_litellm_that_never_opens_its_port_is_an_error(hotfire_home, monkeypatch):
    monkeypatch.setenv("HOTFIRE_CADDY_CMD", "sleep 60")
    monkeypatch.setenv("HOTFIRE_LITELLM_CMD", "sleep 60")
    monkeypatch.setenv("HOTFIRE_LITELLM_BOOT_SECONDS", "1")
    result = runner.invoke(app, ["expose", "--host", "box.local"])
    assert result.exit_code == 1
    assert "did not open port" in result.output
    assert expose.status() == {"caddy": False, "litellm": False}


@pytest.mark.skipif(not shutil.which("caddy"), reason="caddy not installed")
def test_live_caddy_refuses_dot_segment_paths(tmp_path):
    import socket
    import time
    import urllib.request
    import http.client
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    text = expose.render_caddyfile(hosts=["127.0.0.1"], port=port, litellm=True)
    text = text.replace(f"https://127.0.0.1:{port}", f"http://127.0.0.1:{port}").replace("tls internal", "")
    text = text.replace("admin 127.0.0.1:2099", "admin off")
    path = tmp_path / "Caddyfile"
    path.write_text(text)
    proc = subprocess.Popen(["caddy", "run", "--adapter", "caddyfile", "--config", str(path)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env={"PATH": "/usr/bin", "HOME": str(tmp_path)})
    try:
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.1).close()
                break
            except OSError:
                time.sleep(0.1)
        for bad in ["/anthropic/../v1/models", "/v1/./models", "//v1/models", "/x/%2e%2e/v1/models"]:
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            c.request("GET", bad)
            assert c.getresponse().status == 400, bad
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("GET", "/model/info")
        assert c.getresponse().status == 404
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_a_busy_litellm_port_is_refused_before_anything_starts(hotfire_home, monkeypatch, litellm_port):
    import socket
    monkeypatch.setenv("HOTFIRE_CADDY_CMD", "sleep 60")
    monkeypatch.setenv("HOTFIRE_LITELLM_CMD", FAKE_LITELLM)
    with socket.socket() as squatter:
        squatter.bind(("127.0.0.1", litellm_port))
        squatter.listen()
        result = runner.invoke(app, ["expose", "--host", "box.local"])
    assert result.exit_code == 1
    assert f"port {litellm_port} is already in use" in result.output
    assert expose.status() == {"caddy": False, "litellm": False}


def test_percent_encoded_paths_are_refused():
    text = expose.render_caddyfile(hosts=["box.local"], port=8443)
    assert '{http.request.orig_uri}.contains("%")' in text


def test_hosts_are_deduplicated(hotfire_home):
    expose.write_configs(host="localhost", litellm=True)
    assert "https://localhost:8443, https://localhost:8443" not in (hotfire_home / "expose" / "Caddyfile").read_text()


def test_reserved_key_names_are_a_one_line_error():
    result = runner.invoke(app, ["keys", "new", "_litellm"])
    assert result.exit_code == 1
    assert "reserved" in result.output


def test_a_port_with_only_time_wait_sockets_counts_as_free(litellm_port):
    """After LiteLLM served Caddy and was stopped, its port sits in TIME_WAIT; a restart must still work."""
    import socket
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", litellm_port))
    server.listen()
    client = socket.create_connection(("127.0.0.1", litellm_port))
    conn, _ = server.accept()
    conn.close()  # the server side closes first, leaving TIME_WAIT on the listening port
    client.close()
    server.close()
    assert expose._port_free(litellm_port)


def test_lan_endpoint_is_none_until_caddy_runs_then_uses_the_saved_host(monkeypatch):
    monkeypatch.setattr(expose, "status", lambda: {"caddy": False})
    assert expose.lan_endpoint() is None
    monkeypatch.setattr(expose, "status", lambda: {"caddy": True})
    assert expose.lan_endpoint() is None  # no host file yet
    expose.expose_dir().mkdir(parents=True, exist_ok=True)
    (expose.expose_dir() / "host").write_text("box.local\n")
    assert expose.lan_endpoint() == f"https://box.local:{expose.EXPOSE_PORT}/v1"
