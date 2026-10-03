import asyncio
import json
import os
import signal
import sys
from pathlib import Path

import httpx
import pytest

from mxconsole.store import Store
from mxconsole.supervisor import StartFailed, Supervisor, UnknownModel

FAKE = str(Path(__file__).parent / "fake_max.py")


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "mst.db")
    s.add_model(name="tiny", repo_id="org/tiny", revision="abc123", source="hf", architecture="X")
    s.add_model(name="other", repo_id="org/other", revision="main", source="hf", architecture="X")
    return s


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
async def sup(store, clock):
    s = Supervisor(store, max_command=(sys.executable, FAKE), health_timeout=20, clock=clock)
    yield s
    await s.stop_all()


def st(sup, name):
    return next(s for s in sup.status() if s["name"] == name)


async def test_ensure_running_starts_child_and_returns_base_url(sup):
    url = await sup.ensure_running("tiny")
    assert url.startswith("http://127.0.0.1:") and url.endswith("/v1")
    assert httpx.get(url + "/models").status_code == 200
    s = st(sup, "tiny")
    assert s["state"] == "ready" and s["pid"]
    assert url == f"http://127.0.0.1:{s['port']}/v1"


async def test_status_lists_registered_models_as_cold(sup):
    assert {s["name"]: s["state"] for s in sup.status()} == {"other": "cold", "tiny": "cold"}


async def test_models_get_distinct_ports(sup):
    a, b = await sup.ensure_running("tiny"), await sup.ensure_running("other")
    assert a != b


async def test_passes_model_path_port_and_revision(sup, monkeypatch, tmp_path):
    f = tmp_path / "args.json"
    monkeypatch.setenv("FAKE_MAX_ARGS_FILE", str(f))
    await sup.ensure_running("tiny")
    args = json.loads(f.read_text())
    assert args[0] == "serve"
    assert args[args.index("--model-path") + 1] == "org/tiny"
    assert args[args.index("--huggingface-model-revision") + 1] == "abc123"
    assert args[args.index("--port") + 1] == str(st(sup, "tiny")["port"])
    assert args[args.index("--served-model-name") + 1] == "tiny"  # clients ask for the mxc name


async def test_downloaded_model_loads_from_its_local_snapshot(sup, monkeypatch, tmp_path):
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    sup.store.add_model(name="tiny", repo_id="org/tiny", revision="abc123", source="hf-supported",
                        architecture="Qwen2ForCausalLM", weights_path=str(snapshot))
    f = tmp_path / "args.json"
    monkeypatch.setenv("FAKE_MAX_ARGS_FILE", str(f))
    await sup.ensure_running("tiny")
    args = json.loads(f.read_text())
    assert args[args.index("--model-path") + 1] == str(snapshot)
    assert "--huggingface-model-revision" not in args
    assert "--weight-path" not in args


async def test_unknown_model(sup):
    with pytest.raises(UnknownModel):
        await sup.ensure_running("nope")


async def test_concurrent_cold_callers_share_one_start(sup, monkeypatch, tmp_path):
    starts = tmp_path / "starts"
    monkeypatch.setenv("FAKE_MAX_STARTS_FILE", str(starts))
    monkeypatch.setenv("FAKE_MAX_DELAY", "0.5")
    urls = await asyncio.gather(*[sup.ensure_running("tiny") for _ in range(5)])
    assert len(set(urls)) == 1
    assert starts.read_text().count("start") == 1


async def test_status_is_starting_while_waiting_for_health(sup, monkeypatch):
    monkeypatch.setenv("FAKE_MAX_DELAY", "1")
    task = asyncio.create_task(sup.ensure_running("tiny"))
    await asyncio.sleep(0.4)
    assert st(sup, "tiny")["state"] == "starting"
    await task


async def test_start_failure_includes_log_tail(sup, monkeypatch, mxc_home):
    monkeypatch.setenv("FAKE_MAX_FAIL", "1")
    with pytest.raises(StartFailed, match="cannot load model"):
        await sup.ensure_running("tiny")
    assert "cannot load model" in (mxc_home / "logs" / "tiny.log").read_text()
    assert st(sup, "tiny")["state"] == "cold"


async def test_health_timeout_kills_child(store, clock, monkeypatch):
    monkeypatch.setenv("FAKE_MAX_DELAY", "30")
    s = Supervisor(store, max_command=(sys.executable, FAKE), health_timeout=1, clock=clock)
    with pytest.raises(StartFailed, match="healthy"):
        await s.ensure_running("tiny")
    assert st(s, "tiny")["state"] == "cold"


async def test_idle_model_is_reaped_after_keep_alive(sup, store, clock):
    store.set_lifecycle("tiny", keep_alive=60)
    await sup.ensure_running("tiny")
    clock.t += 59
    await sup.reap_idle()
    assert st(sup, "tiny")["state"] == "ready"
    clock.t += 2
    await sup.reap_idle()
    assert st(sup, "tiny")["state"] == "cold"


async def test_touch_resets_idle_clock(sup, store, clock):
    store.set_lifecycle("tiny", keep_alive=60)
    await sup.ensure_running("tiny")
    clock.t += 50
    sup.touch("tiny")
    clock.t += 50
    await sup.reap_idle()
    assert st(sup, "tiny")["state"] == "ready"
    assert st(sup, "tiny")["idle_seconds"] == 50


async def test_pinned_model_is_never_reaped(sup, store, clock):
    store.set_lifecycle("tiny", pinned=True, keep_alive=1)
    await sup.ensure_running("tiny")
    clock.t += 10_000
    await sup.reap_idle()
    s = st(sup, "tiny")
    assert s["state"] == "ready" and s["pinned"] is True


async def test_stop_and_restart(sup):
    url = await sup.ensure_running("tiny")
    await sup.stop("tiny")
    assert st(sup, "tiny")["state"] == "cold"
    with pytest.raises(httpx.TransportError):
        httpx.get(url + "/models", timeout=1)
    assert await sup.ensure_running("tiny")


async def test_crashed_child_is_reported_and_restarted(sup):
    await sup.ensure_running("tiny")
    pid = st(sup, "tiny")["pid"]
    os.kill(pid, signal.SIGKILL)
    for _ in range(50):
        if st(sup, "tiny")["state"] == "crashed":
            break
        await asyncio.sleep(0.1)
    assert st(sup, "tiny")["state"] == "crashed"
    url = await sup.ensure_running("tiny")
    assert httpx.get(url + "/models").status_code == 200
    assert st(sup, "tiny")["pid"] != pid


async def test_stop_all(sup):
    await sup.ensure_running("tiny")
    await sup.ensure_running("other")
    await sup.stop_all()
    assert {s["state"] for s in sup.status()} == {"cold"}


async def test_a_starting_model_reports_how_long_it_has_been_starting(sup, monkeypatch):
    monkeypatch.setenv("FAKE_MAX_DELAY", "1.5")
    task = asyncio.create_task(sup.ensure_running("tiny"))
    for _ in range(50):
        if st(sup, "tiny")["state"] == "starting":
            break
        await asyncio.sleep(0.02)
    row = st(sup, "tiny")
    assert row["state"] == "starting" and row["starting_seconds"] is not None and row["starting_seconds"] >= 0
    await task
    assert st(sup, "tiny")["starting_seconds"] is None


async def test_graph_capture_is_off_by_default(sup, monkeypatch, tmp_path):
    """Real MAX 26.5 on this machine crashes in warmup_graph_capture (CUDA_ERROR_INVALID_DEVICE); serving works without it."""
    monkeypatch.delenv("MXC_GRAPH_CAPTURE", raising=False)
    f = tmp_path / "args.json"
    monkeypatch.setenv("FAKE_MAX_ARGS_FILE", str(f))
    await sup.ensure_running("tiny")
    assert "--no-device-graph-capture" in json.loads(f.read_text())


async def test_graph_capture_can_be_turned_back_on(sup, monkeypatch, tmp_path):
    monkeypatch.setenv("MXC_GRAPH_CAPTURE", "1")
    f = tmp_path / "args.json"
    monkeypatch.setenv("FAKE_MAX_ARGS_FILE", str(f))
    await sup.ensure_running("tiny")
    assert "--no-device-graph-capture" not in json.loads(f.read_text())


async def test_each_model_goes_on_the_gpu_with_most_free_memory_with_a_capped_share(store, clock, monkeypatch, tmp_path):
    """MAX takes ~90% of free GPU memory by default, so a 0.5B model ate 20 GB live. Cap and spread instead."""
    monkeypatch.delenv("MXC_GPU_MEMORY_FRACTION", raising=False)
    gpus = lambda: [{"index": 0, "used_mb": 20000, "total_mb": 24576}, {"index": 1, "used_mb": 1000, "total_mb": 24576}]
    s = Supervisor(store, max_command=(sys.executable, FAKE), health_timeout=20, clock=clock, gpus=gpus)
    f = tmp_path / "args.json"
    monkeypatch.setenv("FAKE_MAX_ARGS_FILE", str(f))
    try:
        await s.ensure_running("tiny")
        args = json.loads(f.read_text())
        assert args[args.index("--devices") + 1] == "gpu:1"
        assert args[args.index("--device-memory-utilization") + 1] == "0.35"
    finally:
        await s.stop_all()


async def test_models_started_together_spread_across_gpus(store, clock, monkeypatch, tmp_path):
    """Live: three models started at once all read the same free memory, all went to GPU0 and the third ran out.
    A GPU this daemon already put a model on counts as fuller, even before nvidia-smi shows it."""
    gpus = lambda: [{"index": 0, "used_mb": 80, "total_mb": 24576}, {"index": 1, "used_mb": 1300, "total_mb": 24576}]
    s = Supervisor(store, max_command=(sys.executable, FAKE), health_timeout=20, clock=clock, gpus=gpus)
    f = tmp_path / "args.log"
    monkeypatch.setenv("FAKE_MAX_ARGS_LOG", str(f))
    monkeypatch.setenv("FAKE_MAX_DELAY", "0.3")
    try:
        await asyncio.gather(s.ensure_running("tiny"), s.ensure_running("other"))
        devices = sorted(a[a.index("--devices") + 1] for a in map(json.loads, f.read_text().splitlines()))
        assert devices == ["gpu:0", "gpu:1"]
    finally:
        await s.stop_all()


async def test_memory_share_is_configurable_and_no_gpu_means_no_device_flags(store, clock, monkeypatch, tmp_path):
    monkeypatch.setenv("MXC_GPU_MEMORY_FRACTION", "0.5")
    s = Supervisor(store, max_command=(sys.executable, FAKE), health_timeout=20, clock=clock, gpus=lambda: [])
    f = tmp_path / "args.json"
    monkeypatch.setenv("FAKE_MAX_ARGS_FILE", str(f))
    try:
        await s.ensure_running("tiny")
        args = json.loads(f.read_text())
        assert "--devices" not in args
        assert args[args.index("--device-memory-utilization") + 1] == "0.5"
    finally:
        await s.stop_all()
