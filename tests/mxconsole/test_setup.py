"""F-051: the first run from nothing (mxconsole/setup). The page's state, its server, and install.sh driven end to
end with a fake pixi on a clean PATH and a local tarball: no network, nothing outside tmp_path."""
from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import sys
import tarfile
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

SETUP = Path(__file__).resolve().parents[2] / "mxconsole" / "setup"
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(SETUP))
import setup_server  # noqa: E402


# --- the page's state ------------------------------------------------------------------------------

def test_state_follows_the_log():
    log = "100 pixi running\n105 pixi done pixi 0.80.0\n105 source running https://x\n"
    st = setup_server.read_state(log, now=130)
    by = {s["id"]: s for s in st["steps"]}
    assert st["state"] == "running" and st["elapsed"] == 30
    assert by["pixi"]["state"] == "done" and by["pixi"]["seconds"] == 5 and by["pixi"]["message"] == "pixi 0.80.0"
    assert by["source"]["state"] == "running" and by["source"]["seconds"] == 25
    assert by["env"]["state"] == "next" and by["env"]["seconds"] is None
    assert [s["id"] for s in st["steps"]] == ["pixi", "source", "env", "app", "login", "start"]


def test_state_done_carries_the_console_url_and_skipped_counts_as_finished():
    log = "".join(f"1 {sid} done\n" for sid in ("pixi", "source", "env", "app", "start"))
    log += "2 login skipped left alone\n3 console - http://127.0.0.1:11435/console\n"
    st = setup_server.read_state(log, now=3)
    assert st["state"] == "done" and st["console"] == "http://127.0.0.1:11435/console"


def test_state_failed_and_garbage_ignored():
    st = setup_server.read_state("junk\nx y z\n1 nope running\n1 env running\n9 env failed pixi install failed\n", 10)
    env = next(s for s in st["steps"] if s["id"] == "env")
    assert st["state"] == "failed" and env["message"] == "pixi install failed" and env["seconds"] == 8


# --- the page's server -----------------------------------------------------------------------------

def test_server_serves_the_page_and_state_and_nothing_else(tmp_path):
    log = tmp_path / "setup.log"
    log.write_text("1 pixi running\n")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), setup_server.make_handler(log))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        page = urllib.request.urlopen(base + "/").read().decode()
        assert "Setting up omxconsole" in page and "/state" in page
        state = json.load(urllib.request.urlopen(base + "/state"))
        assert state["steps"][0]["state"] == "running"
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + "/../setup.log")
        assert e.value.code == 404
    finally:
        srv.shutdown()


# --- install.sh, end to end ------------------------------------------------------------------------

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def tarball(path: Path, with_manifest: bool = True) -> Path:
    with tarfile.open(path, "w:gz") as tar:
        for name, body in ({"mxstudio/pixi.toml": b"[workspace]\n"} if with_manifest else {}).items() | \
                {"mxstudio/README.md": b"hi\n"}.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    return path


def launch(tmp_path, source: Path, **env):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    pixi = bin_dir / "pixi"
    if not pixi.exists():
        pixi.write_text(f"#!/bin/sh\nexec {sys.executable} {HERE / 'fake_pixi.py'} \"$@\"\n")
        pixi.chmod(0o755)
    calls = tmp_path / "calls.jsonl"
    full = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path / "home"), "MXC_RUNTIME": str(tmp_path / "rt"),
            "MXC_SOURCE": str(source), "MXC_PORT": str(free_port()), "MXC_SETUP_PORT": str(free_port()),
            "MXC_SETUP_NO_BROWSER": "1", "MXC_SETUP_LINGER": "0", "FAKE_PIXI_CALLS": str(calls),
            "FAKE_TRAY_SECONDS": "8", **env}
    r = subprocess.run(["sh", str(SETUP / "install.sh")], env=full, capture_output=True, text=True, timeout=120)
    seen = [json.loads(l) for l in calls.read_text().splitlines()] if calls.exists() else []
    return r, seen, tmp_path / "rt"


def test_install_sh_is_valid_posix_sh():
    assert subprocess.run(["sh", "-n", str(SETUP / "install.sh")]).returncode == 0


def test_first_run_installs_everything_and_ends_on_the_console(tmp_path):
    r, calls, rt = launch(tmp_path, tarball(tmp_path / "src.tar.gz"))
    assert r.returncode == 0, r.stderr
    st = setup_server.read_state((rt / "setup.log").read_text(), now=0)
    assert st["state"] == "done", (rt / "setup.log").read_text()
    assert st["console"].endswith("/console")
    manifest = str(rt / "app" / "pixi.toml")
    assert ["install", "--manifest-path", manifest] in calls
    assert ["install", "--manifest-path", manifest, "-e", "app"] in calls
    assert ["run", "--manifest-path", manifest, "python", "-m", "mxconsole.autostart"] in calls
    assert ["run", "--manifest-path", manifest, "mxc", "tray"] in calls
    assert any(c[:1] == ["exec"] for c in calls)  # the page's server ran on pixi's Python
    assert (rt / "app" / ".mxc-ready").exists() and (rt / "app" / "README.md").exists()


def test_a_later_launch_only_starts_the_tray_and_opens_the_window(tmp_path):
    src = tarball(tmp_path / "src.tar.gz")
    launch(tmp_path, src)
    (tmp_path / "calls.jsonl").unlink()
    r, calls, rt = launch(tmp_path, src)
    assert r.returncode == 0, r.stderr
    manifest = str(rt / "app" / "pixi.toml")
    assert sorted(calls) == sorted([["run", "--manifest-path", manifest, "mxc", "tray"],
                                    ["run", "--manifest-path", manifest, "mxc", "app"]])


def test_no_login_leaves_start_at_login_alone(tmp_path):
    r, calls, rt = launch(tmp_path, tarball(tmp_path / "src.tar.gz"), MXC_SETUP_NO_LOGIN="1")
    assert r.returncode == 0
    assert not any("mxconsole.autostart" in c for c in calls)
    login = next(s for s in setup_server.read_state((rt / "setup.log").read_text(), 0)["steps"] if s["id"] == "login")
    assert login["state"] == "skipped"


def test_a_bad_download_stops_with_a_reason_and_nothing_marked_ready(tmp_path):
    r, calls, rt = launch(tmp_path, tarball(tmp_path / "src.tar.gz", with_manifest=False))
    assert r.returncode == 1 and "didn't contain omxconsole" in r.stderr
    st = setup_server.read_state((rt / "setup.log").read_text(), 0)
    assert st["state"] == "failed" and not (rt / "app" / ".mxc-ready").exists()
    assert not any(c[:1] == ["install"] for c in calls)


def test_a_failed_env_install_says_where_the_details_are(tmp_path):
    r, _, rt = launch(tmp_path, tarball(tmp_path / "src.tar.gz"), FAKE_PIXI_FAIL="default")
    assert r.returncode == 1 and "setup-output.log" in r.stderr
    env = next(s for s in setup_server.read_state((rt / "setup.log").read_text(), 0)["steps"] if s["id"] == "env")
    assert env["state"] == "failed"


def test_a_finished_setup_reports_its_real_length_not_time_since():
    log = "100 pixi running\n" + "".join(f"110 {sid} done\n" for sid in ("pixi", "source", "env", "app", "login")) + \
          "133 start done\n"
    assert setup_server.read_state(log, now=500)["elapsed"] == 33
