"""Daemon lock, grandchildren, and pid-file ownership."""
import asyncio
import os

import pytest

from mxconsole.supervisor import DaemonAlreadyRunning
from test_supervisor_lifecycle import alive, make, st, wait_dead, store, clock, sup  # noqa: F401


async def test_second_daemon_cannot_take_the_lock_or_sweep(store, clock):  # noqa: F811
    first = make(store, clock)
    first.acquire_lock()
    first.acquire_lock()  # idempotent
    await first.ensure_running("tiny")
    pid = st(first)["pid"]
    second = make(store, clock)
    with pytest.raises(DaemonAlreadyRunning):
        second.acquire_lock()
    assert alive(pid)  # nothing was touched
    await first.stop_all()


async def test_lock_is_free_again_after_holder_is_gone(store, clock):  # noqa: F811
    a = make(store, clock)
    a.acquire_lock()
    a._lock_file.close()  # what process exit does
    make(store, clock).acquire_lock()


async def test_stop_kills_grandchildren(sup, monkeypatch, tmp_path):  # noqa: F811
    f = tmp_path / "grandchild.pid"
    monkeypatch.setenv("FAKE_MAX_SPAWN_CHILD", str(f))
    await sup.ensure_running("tiny")
    gpid = int(f.read_text())
    assert alive(gpid)
    await sup.stop("tiny")
    assert await wait_dead(gpid)


async def test_crashed_leader_group_is_killed_on_stop(sup, monkeypatch, tmp_path):  # noqa: F811
    f = tmp_path / "grandchild.pid"
    monkeypatch.setenv("FAKE_MAX_SPAWN_CHILD", str(f))
    await sup.ensure_running("tiny")
    gpid = int(f.read_text())
    os.kill(st(sup)["pid"], 9)  # leader dies, grandchild lives on
    for _ in range(50):
        if st(sup)["state"] == "crashed":
            break
        await asyncio.sleep(0.1)
    assert st(sup)["state"] == "crashed" and alive(gpid)
    await sup.stop("tiny")
    assert await wait_dead(gpid)


async def test_pid_files_are_per_child(sup, mxc_home):  # noqa: F811
    await sup.ensure_running("tiny")
    old = st(sup)["pid"]
    await sup.stop("tiny")
    await sup.ensure_running("tiny")
    new = st(sup)["pid"]
    assert old != new
    assert {p.name for p in (mxc_home / "run").glob("*.pid")} == {f"tiny.{new}.pid"}
