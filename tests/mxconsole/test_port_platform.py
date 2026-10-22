"""F-050: the automatic port confines its agent with bubblewrap, which is Linux-only. Off Linux it is refused up
front (CLI, admin API and so the console's Port button), before anything is downloaded or started. The guided
`mxc port <ref>` (a workspace for your own Claude Code) still works everywhere."""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from mxconsole import cli, port_auto
from mxconsole.gateway import create_app
from mxconsole.store import Store
from mxconsole.supervisor import Supervisor

from test_port_auto import make_manager  # noqa: E402  (same folder)


@pytest.fixture
def on_mac(monkeypatch):
    monkeypatch.setattr(port_auto, "platform", lambda: "darwin")


def test_manager_refuses_off_linux_even_unsandboxed(tmp_path, on_mac):
    m = make_manager(tmp_path, "claude")
    for unsandboxed in (False, True):
        with pytest.raises(port_auto.PortUnsupported, match="Linux"):
            m.start("acme/weird", unsandboxed=unsandboxed)
    assert m.list() == []  # no job, nothing fetched


def test_manager_still_ports_on_linux(tmp_path, monkeypatch):
    monkeypatch.setattr(port_auto, "platform", lambda: "linux")
    m = make_manager(tmp_path, "claude")
    assert m.start("acme/weird")["status"] == "running"
    m.cancel(m.list()[0]["id"])


def test_admin_api_answers_400_linux_only(on_mac):
    from starlette.testclient import TestClient
    store = Store.open()
    with TestClient(create_app(store, Supervisor(store)), base_url="http://127.0.0.1:11435",
                    client=("127.0.0.1", 5000)) as tc:
        r = tc.post("/admin/ports", json={"ref": "acme/weird"}, headers={"X-Mst-Admin": "1"})
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["code"] == "linux_only" and "mxc port acme/weird" in err["message"]


def test_cli_auto_refuses_before_starting_the_daemon(on_mac, monkeypatch):
    monkeypatch.setattr("mxconsole.serve.ensure_daemon", lambda *a, **k: pytest.fail("must not start the daemon"))
    out = CliRunner().invoke(cli.app, ["port", "acme/weird", "--auto"])
    assert out.exit_code == 1 and "Linux" in out.output and "mxc port acme/weird" in out.output
