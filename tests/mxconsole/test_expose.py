"""`mxc expose`: Caddy (HTTPS + key check) in front of the daemon, nothing in between (F-007, F-063)."""
import shutil
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from typer.testing import CliRunner

from mxconsole import expose, keys
from mxconsole.cli import app
from mxconsole.store import Store

runner = CliRunner()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_caddyfile_checks_app_keys_then_proxies_straight_to_the_daemon():
    text = expose.render_caddyfile(hosts=["box.local", "localhost"], port=8443)
    assert "https://box.local:8443, https://localhost:8443" in text
    assert "tls internal" in text
    assert "forward_auth 127.0.0.1:11435" in text
    assert "uri /auth/check" in text
    assert "reverse_proxy 127.0.0.1:11435" in text
    assert "header_up Authorization" not in text  # the app key goes on to the daemon, which checks it again
    assert "litellm" not in text.lower()


@pytest.mark.skipif(not shutil.which("caddy"), reason="caddy not installed")
def test_caddyfile_is_valid_caddy_config(tmp_path):
    path = tmp_path / "Caddyfile"
    path.write_text(expose.render_caddyfile(hosts=["box.local"], port=8443))
    result = subprocess.run(["caddy", "adapt", "--config", str(path), "--adapter", "caddyfile"],
                            capture_output=True, text=True, env={"PATH": "/usr/bin"})
    assert result.returncode == 0, result.stderr
    assert '"/v1/models"' in result.stdout and '"/model/info"' not in result.stdout


def test_expose_dry_run_writes_only_the_caddyfile_and_prints_the_url(mxc_home):
    result = runner.invoke(app, ["expose", "--dry-run", "--host", "box.local"])
    assert result.exit_code == 0, result.output
    assert "https://box.local:8443/v1" in result.output
    assert sorted(p.name for p in (mxc_home / "expose").iterdir()) == ["Caddyfile"]
    assert "root.crt" in result.output  # how to trust the CA on another device
    assert "litellm" not in result.output.lower()


def test_expose_starts_and_stops_caddy_only(mxc_home, monkeypatch):
    monkeypatch.setenv("MXC_CADDY_CMD", "sleep 60")
    result = runner.invoke(app, ["expose", "--host", "box.local"])
    assert result.exit_code == 0, result.output
    assert expose.status() == {"caddy": True}
    assert expose.lan_endpoint() == "https://box.local:8443/v1"

    result = runner.invoke(app, ["expose", "--stop"])
    assert result.exit_code == 0, result.output
    assert "stopped Caddy" in result.output and "litellm" not in result.output.lower()
    assert expose.status() == {"caddy": False}


def test_no_litellm_is_a_deprecated_no_op_and_litellm_is_gone(mxc_home, monkeypatch):
    monkeypatch.setenv("MXC_CADDY_CMD", "sleep 60")
    result = runner.invoke(app, ["expose", "--no-litellm", "--host", "box.local"])
    assert result.exit_code == 0, result.output
    assert "--no-litellm is no longer needed" in result.output
    assert expose.status() == {"caddy": True}
    runner.invoke(app, ["expose", "--stop"])
    assert runner.invoke(app, ["expose", "--litellm", "--dry-run"]).exit_code != 0
    assert "litellm" not in runner.invoke(app, ["expose", "--help"]).output.lower()


def test_a_litellm_left_running_from_before_is_stopped_and_its_key_revoked(mxc_home, monkeypatch):
    """An install upgraded while `mxc expose` ran still has LiteLLM's process and its `_litellm` key."""
    store = Store.open()
    old_key = keys.create_key(store, "_litellm", internal=True)
    proc = subprocess.Popen(["sleep", "60"], start_new_session=True)
    (expose.expose_dir() / "litellm.pid").write_text(f"{proc.pid} {expose._start_time(proc.pid)}")
    expose.stop(store=store)
    assert proc.wait(timeout=10) is not None
    assert keys.verify_key(store, old_key) is None
    assert not (expose.expose_dir() / "litellm.pid").exists()


def test_caddy_gets_only_the_env_it_needs(mxc_home, monkeypatch):
    monkeypatch.setenv("MXC_CADDY_CMD", f"sh -c 'env > \"{mxc_home}/caddy.env\"; exec sleep 60'")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    result = runner.invoke(app, ["expose", "--host", "box.local"])
    assert result.exit_code == 0, result.output
    env = (mxc_home / "caddy.env").read_text()
    assert "PATH=" in env and "sk-should-not-leak" not in env
    assert "LITELLM" not in env and "MXC_UPSTREAM_KEY" not in env
    runner.invoke(app, ["expose", "--stop"])


def test_caddy_only_forwards_the_openai_routes():
    text = expose.render_caddyfile(hosts=["box.local"], port=8443)
    assert "@api path /v1/models /v1/chat/completions /v1/completions /v1/embeddings" in text
    assert "handle @api" in text
    assert "respond 404" in text


def test_bad_hostnames_are_refused():
    for bad in ["box local", "box{.local", "", "a;b"]:
        with pytest.raises(ValueError):
            expose.render_caddyfile(hosts=[bad], port=8443)


def test_a_reused_pid_is_not_mistaken_for_our_process(mxc_home):
    import os
    d = mxc_home / "expose"
    d.mkdir(parents=True)
    (d / "caddy.pid").write_text(f"{os.getpid()} 1")  # some other live process, wrong start time
    assert expose.status()["caddy"] is False
    expose.stop()  # must not signal it
    assert os.getpid()  # still alive


def test_a_process_that_dies_at_once_is_reported_with_its_log(mxc_home, monkeypatch):
    monkeypatch.setenv("MXC_CADDY_CMD", "sh -c 'echo address already in use; exit 1'")
    result = runner.invoke(app, ["expose", "--host", "box.local"])
    assert result.exit_code == 1
    assert "caddy exited" in result.output
    assert "address already in use" in result.output


def test_a_missing_binary_is_a_one_line_error(mxc_home, monkeypatch):
    monkeypatch.setenv("MXC_CADDY_CMD", "/nonexistent/caddy")
    result = runner.invoke(app, ["expose", "--host", "box.local"])
    assert result.exit_code == 1
    assert "not found" in result.output


def test_percent_encoded_paths_are_refused():
    text = expose.render_caddyfile(hosts=["box.local"], port=8443)
    assert '{http.request.orig_uri}.contains("%")' in text


def test_hosts_are_deduplicated(mxc_home):
    expose.write_configs(host="localhost")
    assert "https://localhost:8443, https://localhost:8443" not in (mxc_home / "expose" / "Caddyfile").read_text()


def test_reserved_key_names_are_a_one_line_error():
    result = runner.invoke(app, ["keys", "new", "_anything"])
    assert result.exit_code == 1
    assert "reserved" in result.output


class FakeDaemon(BaseHTTPRequestHandler):
    """The daemon's two jobs behind Caddy: answer forward_auth, then serve /v1 (and see the app key)."""
    seen: list[tuple[str, str | None]] = []

    def do_GET(self):
        auth = self.headers.get("Authorization")
        FakeDaemon.seen.append((self.path, auth))
        ok = auth == "Bearer mst-good"
        self.send_response(200 if ok else 401)
        self.send_header("Content-Type", "application/json")
        if self.path == "/auth/check" and ok:
            self.send_header("X-Mst-App", "myapp")
        self.end_headers()
        self.wfile.write(b'{"object": "list", "data": [{"id": "tiny"}]}' if ok and self.path == "/v1/models" else b"{}")

    def log_message(self, *a):
        pass


@pytest.mark.skipif(not shutil.which("caddy"), reason="caddy not installed")
def test_live_caddy_proxies_straight_to_the_daemon_and_refuses_the_rest(tmp_path, monkeypatch):
    """LAN sharing end to end on a real Caddy (plain HTTP here: no CA to trust), with a fake daemon on a free port."""
    import http.client
    import time
    daemon = ThreadingHTTPServer(("127.0.0.1", 0), FakeDaemon)
    threading.Thread(target=daemon.serve_forever, daemon=True).start()
    monkeypatch.setattr(expose, "DAEMON_PORT", daemon.server_address[1])  # never the owner's real daemon
    port = free_port()
    text = expose.render_caddyfile(hosts=["127.0.0.1"], port=port)
    text = text.replace(f"https://127.0.0.1:{port}", f"http://127.0.0.1:{port}").replace("tls internal", "")
    text = text.replace("admin 127.0.0.1:2099", "admin off")
    path = tmp_path / "Caddyfile"
    path.write_text(text)
    proc = subprocess.Popen(["caddy", "run", "--adapter", "caddyfile", "--config", str(path)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env={"PATH": "/usr/bin", "HOME": str(tmp_path)})

    def get(p, key=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("GET", p, headers={"Authorization": f"Bearer {key}"} if key else {})
        r = c.getresponse()
        return r.status, r.read()

    try:
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.1).close()
                break
            except OSError:
                time.sleep(0.1)
        FakeDaemon.seen.clear()
        status, body = get("/v1/models", "mst-good")
        assert status == 200 and b'"tiny"' in body
        assert FakeDaemon.seen == [("/auth/check", "Bearer mst-good"), ("/v1/models", "Bearer mst-good")]
        assert get("/v1/models", "mst-bad")[0] == 401
        for bad in ["/anthropic/../v1/models", "/v1/./models", "//v1/models", "/x/%2e%2e/v1/models"]:
            assert get(bad, "mst-good")[0] == 400, bad
        assert get("/admin/info", "mst-good")[0] == 404  # loopback-only pages never reach the LAN
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        daemon.shutdown()
