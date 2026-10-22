import pytest


@pytest.fixture(autouse=True)
def mxc_home(tmp_path, monkeypatch):
    """Every test gets its own MXC_HOME so nothing touches ~/.mxc, and its own HOME so nothing touches the
    real home folder (a CLI test once removed the real ~/.local/bin/mxc shim)."""
    home = tmp_path / "mxc-home"
    monkeypatch.setenv("MXC_HOME", str(home))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    def no_systemctl(cmd):  # `systemctl --user` reaches the real user manager over D-Bus, whatever HOME says
        raise AssertionError(f"a test tried to run {cmd}; inject a runner")
    monkeypatch.setattr("mxconsole.autostart._default_runner", no_systemctl)
    return home


@pytest.fixture
def inproc_daemon(monkeypatch):
    """`mxc pull` always goes through the daemon: stand one up in this process (the real gateway app)
    and route the CLI's HTTP calls to it. Yields the starlette TestClient."""
    import sys

    from starlette.testclient import TestClient

    from mxconsole.gateway import create_app
    from mxconsole.store import Store
    from mxconsole.supervisor import Supervisor

    store = Store.open()
    app = create_app(store, Supervisor(store, max_command=(sys.executable, "-c", "pass")))
    tc = TestClient(app, base_url="http://127.0.0.1:11435", client=("127.0.0.1", 5000))
    monkeypatch.setattr("httpx.request", lambda method, url, **kw: tc.request(method, url, **kw))
    monkeypatch.setattr("mxconsole.serve.ensure_daemon", lambda *a, **k: False)
    monkeypatch.setattr("mxconsole.serve.daemon_running", lambda: True)
    return tc
