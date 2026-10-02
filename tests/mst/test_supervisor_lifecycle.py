"""Supervisor robustness: orphans, shared starts, stop during start, in-flight, port clashes."""
import asyncio
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx
import pytest

import mst.supervisor as mod
from mst.store import Store
from mst.supervisor import StartFailed, Supervisor

FAKE = str(Path(__file__).parent / "fake_max.py")


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "mst.db")
    s.add_model(name="tiny", repo_id="org/tiny", revision="abc123", source="hf", architecture="X")
    return s


@pytest.fixture
def clock():
    return Clock()


def make(store, clock, timeout=20):
    return Supervisor(store, max_command=(sys.executable, FAKE), health_timeout=timeout, clock=clock)


@pytest.fixture
async def sup(store, clock):
    s = make(store, clock)
    yield s
    await s.stop_all()


def st(sup, name="tiny"):
    return next(s for s in sup.status() if s["name"] == name)


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:  # the process can vanish between the kill(0) and this read
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except FileNotFoundError:
        return False


async def wait_dead(pid):
    for _ in range(100):
        if not alive(pid):
            return True
        await asyncio.sleep(0.05)
    return False


async def test_sweep_orphans_kills_children_of_a_dead_daemon(store, clock):
    a = make(store, clock)
    await a.ensure_running("tiny")
    pid = st(a)["pid"]
    del a  # the daemon "dies" without stop(); the child is orphaned
    assert alive(pid)
    make(store, clock).sweep_orphans()
    assert await wait_dead(pid)


async def test_sweep_never_kills_a_reused_pid(store, clock, mst_home):
    (mst_home / "run").mkdir(parents=True)
    victim = await asyncio.create_subprocess_exec("sleep", "30", start_new_session=True)
    (mst_home / "run" / "tiny.pid").write_text(f"{victim.pid} {victim.pid}")
    make(store, clock).sweep_orphans()
    assert alive(victim.pid)
    victim.kill()
    await victim.wait()


async def test_sweep_leaves_live_children_of_this_supervisor(sup):
    await sup.ensure_running("tiny")
    sup.sweep_orphans()
    assert st(sup)["state"] == "ready"


async def test_health_timeout_leaves_no_process_or_pid_file(store, clock, monkeypatch, tmp_path, mst_home):
    pidfile = tmp_path / "child.pid"
    monkeypatch.setenv("FAKE_MAX_PID_FILE", str(pidfile))
    monkeypatch.setenv("FAKE_MAX_DELAY", "30")
    s = make(store, clock, timeout=1)
    with pytest.raises(StartFailed):
        await s.ensure_running("tiny")
    assert await wait_dead(int(pidfile.read_text()))
    assert list((mst_home / "run").glob("*.pid")) == []


async def test_waiters_share_one_failure_and_next_call_retries(sup, monkeypatch):
    monkeypatch.setenv("FAKE_MAX_FAIL", "1")
    results = await asyncio.gather(*[sup.ensure_running("tiny") for _ in range(3)], return_exceptions=True)
    assert all(isinstance(r, StartFailed) for r in results)
    assert len({str(r) for r in results}) == 1
    monkeypatch.delenv("FAKE_MAX_FAIL")
    assert await sup.ensure_running("tiny")


async def test_cancelled_caller_does_not_kill_the_start(sup, monkeypatch):
    monkeypatch.setenv("FAKE_MAX_DELAY", "0.5")
    first = asyncio.create_task(sup.ensure_running("tiny"))
    second = asyncio.create_task(sup.ensure_running("tiny"))
    await asyncio.sleep(0.2)
    first.cancel()
    url = await second
    assert httpx.get(url + "/models").status_code == 200


async def test_stop_during_start_kills_child_and_fails_waiters(sup, monkeypatch):
    monkeypatch.setenv("FAKE_MAX_DELAY", "5")
    waiter = asyncio.create_task(sup.ensure_running("tiny"))
    await asyncio.sleep(0.4)
    pid = st(sup)["pid"]
    await sup.stop("tiny")
    with pytest.raises(StartFailed):
        await waiter
    assert await wait_dead(pid)
    assert st(sup)["state"] == "cold"


async def test_reap_idle_leaves_starting_model_alone(sup, store, clock, monkeypatch):
    store.set_lifecycle("tiny", keep_alive=1)
    monkeypatch.setenv("FAKE_MAX_DELAY", "1")
    task = asyncio.create_task(sup.ensure_running("tiny"))
    await asyncio.sleep(0.4)
    clock.t += 1000
    assert await sup.reap_idle() == []
    await task
    assert st(sup)["state"] == "ready"


async def test_in_flight_request_blocks_reaping_and_touches(sup, store, clock):
    store.set_lifecycle("tiny", keep_alive=60)
    await sup.ensure_running("tiny")
    async with sup.request("tiny"):
        clock.t += 1000
        assert await sup.reap_idle() == []
        assert st(sup)["in_flight"] == 1
    assert st(sup)["in_flight"] == 0
    assert st(sup)["idle_seconds"] == 0  # touched on exit
    clock.t += 61
    assert await sup.reap_idle() == ["tiny"]


async def test_port_clash_retries_on_a_new_port(sup, monkeypatch):
    class Imposter(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"data": [{"id": "someone-else"}]}')

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Imposter)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    taken = srv.server_address[1]
    real = mod._free_port
    ports = iter([taken])
    monkeypatch.setattr(mod, "_free_port", lambda: next(ports, None) or real())
    try:
        url = await sup.ensure_running("tiny")
    finally:
        srv.shutdown()
    assert url != f"http://127.0.0.1:{taken}/v1"
    assert httpx.get(url + "/models").json()["data"][0]["id"] == "tiny"
