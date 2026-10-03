"""The pull job lifecycle, against a fake Hugging Face that downloads small files slowly."""
import threading
from pathlib import Path
import time

import pytest
from fake_hub import CHUNK, SlowHub
from fakes import CATALOG, make_hf

from mxconsole import resolve
from mxconsole.pulls import BadPull, PullBusy, PullManager
from mxconsole.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "mst.db")


def manager(store, hub, **kw):
    return PullManager(store, hub_factory=lambda: hub, catalog_loader=lambda: CATALOG, **kw)


def wait_for(fn, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.01)
    raise AssertionError("timed out")


def test_a_pull_runs_through_every_step_and_registers_the_model(store):
    hub = SlowHub()
    m = manager(store, hub)
    job = m.start("qwen2.5-0.5b")
    assert job["status"] in ("running", "done") and job["ref"] == "qwen2.5-0.5b"
    done = m.wait(job["id"])
    assert done["status"] == "done", done
    assert [s["state"] for s in done["steps"]] == ["done"] * 5
    assert [s["id"] for s in done["steps"]] == ["resolve", "architecture", "download", "verify", "register"]
    assert done["percent"] == 100.0 and done["done_bytes"] == done["total_bytes"] > 0
    assert done["model"] == "qwen2.5-0.5b" and done["repo_id"] == "Qwen/Qwen2.5-0.5B-Instruct"
    model = store.get_model("qwen2.5-0.5b")
    assert model.source == "max-catalog" and model.weights_path.startswith(str(hub.root))
    files = {f["name"]: f for f in done["files"]}
    assert files["model.safetensors"]["size"] == 2 * CHUNK and files["model.safetensors"]["state"] == "done"
    assert "→" in done["steps"][0]["detail"] and "Qwen2ForCausalLM" in done["steps"][1]["detail"]


def test_progress_reports_bytes_per_file_percent_and_speed_while_downloading(store):
    hub = SlowHub(weights_bytes=8 * CHUNK, chunk_delay=0.03)
    m = manager(store, hub)
    job = m.start("qwen2.5-0.5b")
    seen = wait_for(lambda: (lambda v: v if v["step"] == "download" and 0 < v["done_bytes"] < v["total_bytes"] and v["speed"] > 0 else None)(m.get(job["id"])))
    assert 0 < seen["percent"] < 100
    big = next(f for f in seen["files"] if f["name"] == "model.safetensors")
    assert big["size"] == 8 * CHUNK and 0 < big["done"] <= big["size"]
    assert [s["state"] for s in seen["steps"]][:3] == ["done", "done", "now"]
    assert seen["steps"][3]["state"] == "next" and seen["eta_seconds"] is not None
    m.wait(job["id"])


def test_one_job_per_ref_even_through_an_alias(store):
    hub = SlowHub(weights_bytes=8 * CHUNK, chunk_delay=0.02)
    m = manager(store, hub)
    first = m.start("qwen2.5-0.5b")
    with pytest.raises(PullBusy) as busy:
        m.start("Qwen/Qwen2.5-0.5B-Instruct")
    assert busy.value.job["id"] == first["id"]
    m.wait(first["id"])
    assert m.start("qwen2.5-0.5b")["id"] != first["id"]  # finished: a new pull is fine
    m.cancel_all()


def test_cancel_stops_the_download_and_registers_nothing(store):
    hub = SlowHub(weights_bytes=40 * CHUNK, chunk_delay=0.02)
    m = manager(store, hub)
    job = m.start("qwen2.5-0.5b")
    wait_for(lambda: m.get(job["id"])["done_bytes"] > 0)
    m.cancel(job["id"])
    final = m.wait(job["id"])
    assert final["status"] == "cancelled" and "resume" in final["message"]
    assert final["done_bytes"] < final["total_bytes"]
    assert [s["state"] for s in final["steps"]][2] == "cancelled"
    assert store.get_model("qwen2.5-0.5b") is None


def test_unsupported_architecture_ends_unsupported_with_the_port_hint(store):
    hub = SlowHub()
    m = manager(store, hub)
    job = m.wait(m.start("acme/weird")["id"])
    assert job["status"] == "unsupported" and job["hint"] == "mxc port acme/weird"
    assert "WeirdForCausalLM" in job["message"] and "mxc port acme/weird" in job["message"]
    assert hub.downloads == [] and job["step"] == "architecture"
    assert job["steps"][1]["state"] == "failed" and job["steps"][2]["state"] == "next"


def test_gated_and_missing_repos_fail_with_a_clear_message(store):
    hub = SlowHub(make_hf(gated=["meta-llama/Llama-3.2-1B-Instruct"]))
    m = manager(store, hub)
    gated = m.wait(m.start("llama3.2-1b")["id"])
    assert gated["status"] == "failed" and gated["error_kind"] == "gated"
    assert "HF_TOKEN" in gated["message"] and gated["steps"][0]["state"] == "failed"
    missing = m.wait(m.start("nobody/nothing")["id"])
    assert missing["status"] == "failed" and missing["error_kind"] == "not-found" and "check the name" in missing["message"]


def test_a_network_error_mid_download_fails_and_keeps_going_state_honest(store):
    hub = SlowHub(weights_bytes=4 * CHUNK, fail_after=CHUNK)
    m = manager(store, hub)
    job = m.wait(m.start("qwen2.5-0.5b")["id"])
    assert job["status"] == "failed" and "retry to resume" in job["message"]
    assert job["steps"][2]["state"] == "failed" and job["speed"] == 0
    assert store.get_model("qwen2.5-0.5b") is None


def test_a_hub_that_cannot_be_reached_fails_the_job_not_the_thread(store):
    def broken():
        raise resolve.ResolveError("could not reach Hugging Face (ConnectError); check your network")
    m = PullManager(store, hub_factory=broken, catalog_loader=lambda: CATALOG)
    job = m.wait(m.start("qwen2.5-0.5b")["id"])
    assert job["status"] == "failed" and "check your network" in job["message"]


def test_an_unexpected_exception_still_ends_the_job(store):
    class Boom(SlowHub):
        def inspect(self, repo_id):
            raise RuntimeError("kaboom")
    job = (lambda m: m.wait(m.start("qwen2.5-0.5b")["id"]))(manager(store, Boom()))
    assert job["status"] == "failed" and "kaboom" in job["message"]


def test_a_name_taken_by_another_repo_is_refused_before_downloading(store):
    hub = SlowHub()
    m = manager(store, hub)
    assert m.wait(m.start("alice/foo")["id"])["status"] == "done"
    clash = m.wait(m.start("bob/foo")["id"])
    assert clash["status"] == "failed" and clash["error_kind"] == "name-taken" and "--name" in clash["message"]
    ok = m.wait(m.start("bob/foo", name="bobfoo")["id"])
    assert ok["status"] == "done" and store.get_model("bobfoo") is not None


def test_short_files_are_caught_by_verify(store):
    class Short(SlowHub):
        def download(self, *a, **kw):
            path = super().download(*a, **kw)
            (self.root / "Qwen--Qwen2.5-0.5B-Instruct" / "abc123" / "model.safetensors").write_bytes(b"x")
            return path
    job = (lambda m: m.wait(m.start("qwen2.5-0.5b")["id"]))(manager(store, Short()))
    assert job["status"] == "failed" and job["step"] == "verify" and "model.safetensors" in job["message"]


def test_bad_refs_and_names_are_refused(store):
    m = manager(store, SlowHub())
    for bad in ("", "../etc/passwd", "a/b/c", "x y", "-rf", "a" * 200):
        with pytest.raises(BadPull):
            m.start(bad)
    with pytest.raises(BadPull):
        m.start("qwen2.5-0.5b", name="../evil")


def test_jobs_and_their_final_state_are_kept_in_sqlite(store, tmp_path):
    m = manager(store, SlowHub())
    ok = m.wait(m.start("qwen2.5-0.5b")["id"])
    bad = m.wait(m.start("acme/weird")["id"])
    again = manager(Store(tmp_path / "mst.db"), SlowHub())  # a restarted daemon
    rows = again.list()
    assert [r["id"] for r in rows] == [bad["id"], ok["id"]]  # newest first
    assert rows[0]["status"] == "unsupported" and rows[0]["hint"] == "mxc port acme/weird"
    assert rows[1]["status"] == "done" and rows[1]["model"] == "qwen2.5-0.5b" and rows[1]["percent"] == 100.0
    assert any(f["name"] == "model.safetensors" for f in rows[1]["files"])
    assert again.get(ok["id"])["steps"][4]["detail"].startswith("served as qwen2.5-0.5b")
    assert again.get(9999) is None


def test_a_pull_the_daemon_died_in_is_marked_interrupted_on_restart(store, tmp_path):
    hub = SlowHub(weights_bytes=80 * CHUNK, chunk_delay=0.05)
    m = manager(store, hub)
    job = m.start("qwen2.5-0.5b")
    wait_for(lambda: m.get(job["id"])["done_bytes"] > 0)
    restarted = manager(Store(tmp_path / "mst.db"), SlowHub())
    row = restarted.get(job["id"])
    assert row["status"] == "failed" and row["error_kind"] == "interrupted"
    m.cancel_all()


def test_old_finished_pulls_are_pruned(store, monkeypatch):
    monkeypatch.setattr("mxconsole.pulls.KEEP_ROWS", 3)
    m = manager(store, SlowHub())
    for _ in range(6):
        m.wait(m.start("acme/weird")["id"])
    assert len(m.list(100)) == 3


def test_progress_bar_class_reports_bytes_and_cancels():
    """The tqdm class given to huggingface_hub: reports (file, done, total) and raises on cancel."""
    seen, stop = [], threading.Event()
    Bar = resolve.progress_bar("model.safetensors", lambda f, d, t: seen.append((f, d, t)), stop)
    bar = Bar(total=100, initial=10, unit="B", name="huggingface_hub.http_get")
    bar.update(30)
    assert seen[-1] == ("model.safetensors", 40, 100) and seen[0] == ("model.safetensors", 10, 100)
    stop.set()
    with pytest.raises(resolve.PullCancelled):
        bar.update(1)
    bar.close()


# -- the real download path, with huggingface_hub's hf_hub_download stubbed ------------------------------------

def _client():
    """A HubClient with no network: file sizes unknown, hf_hub_download replaced by the caller."""
    client = resolve.HubClient(token="x")
    client.api = type("Api", (), {"get_paths_info": staticmethod(lambda *a, **kw: [])})()
    return client


def test_a_cancel_mid_file_propagates_and_the_retry_finishes_the_file(monkeypatch, tmp_path):
    """Partial bytes stay on disk; the retry resumes from them (as hf_hub_download does with .incomplete files)."""
    cache = tmp_path / "snap"
    cache.mkdir()
    calls = []

    def fake_download(repo_id, filename, revision=None, token=None, tqdm_class=None):
        part = cache / (filename + ".incomplete")
        have = part.stat().st_size if part.exists() else 0
        calls.append((filename, have))
        bar = tqdm_class(total=100, initial=have, unit="B", name="huggingface_hub.http_get")
        with bar, open(part, "ab") as out:
            while have < 100:
                out.write(b"x" * 20)
                have += 20
                bar.update(20)  # raises PullCancelled when the cancel event is set
        part.rename(cache / filename)
        return str(cache / filename)

    monkeypatch.setattr("huggingface_hub.hf_hub_download", fake_download)
    client, cancel = _client(), threading.Event()

    def progress(f, d, t):
        if d >= 40:
            cancel.set()

    with pytest.raises(resolve.PullCancelled):
        client.download("o/r", "abc", ["model.safetensors"], progress=progress, cancel=cancel)
    part = cache / "model.safetensors.incomplete"
    assert 40 <= part.stat().st_size < 100 and not (cache / "model.safetensors").exists()

    path = client.download("o/r", "abc", ["model.safetensors"], progress=lambda *a: None, cancel=threading.Event())
    assert (cache / "model.safetensors").stat().st_size == 100 and Path(path) == cache
    assert calls[1][1] >= 40  # the retry started from the partial file, not from zero


def test_files_download_in_parallel_with_per_file_progress(monkeypatch, tmp_path):
    inflight, peak, lock, release = [0], [0], threading.Lock(), threading.Event()

    def fake_download(repo_id, filename, revision=None, token=None, tqdm_class=None):
        with lock:
            inflight[0] += 1
            peak[0] = max(peak[0], inflight[0])
            if inflight[0] >= 4:
                release.set()
        release.wait(5)  # all four must be in flight together for the test to pass
        bar = tqdm_class(total=10, initial=0, unit="B", name="huggingface_hub.http_get")
        bar.update(10)
        bar.close()
        with lock:
            inflight[0] -= 1
        p = tmp_path / filename
        p.write_bytes(b"x" * 10)
        return str(p)

    monkeypatch.setattr("huggingface_hub.hf_hub_download", fake_download)
    seen = {}
    _client().download("o/r", "abc", [f"f{i}.bin" for i in range(6)],
                       progress=lambda f, d, t: seen.__setitem__(f, (d, t)), cancel=threading.Event())
    assert peak[0] == resolve.DOWNLOAD_WORKERS == 4
    assert seen == {f"f{i}.bin": (10, 10) for i in range(6)}


def test_a_cancel_stops_the_other_files_too(monkeypatch, tmp_path):
    started = []

    def fake_download(repo_id, filename, revision=None, token=None, tqdm_class=None):
        started.append(filename)
        bar = tqdm_class(total=100, initial=0, unit="B", name="huggingface_hub.http_get")
        for _ in range(100):
            bar.update(1)
            time.sleep(0.005)
        return str(tmp_path / filename)

    monkeypatch.setattr("huggingface_hub.hf_hub_download", fake_download)
    cancel = threading.Event()
    threading.Timer(0.05, cancel.set).start()
    with pytest.raises(resolve.PullCancelled):
        _client().download("o/r", "abc", [f"f{i}" for i in range(12)], progress=lambda *a: None, cancel=cancel)
    assert len(started) < 12  # queued files never started


def test_the_progress_bar_closes_its_devnull_sink():
    Bar = resolve.progress_bar("f", lambda *a: None, None)
    bar = Bar(total=10, unit="B", name="huggingface_hub.http_get")
    sink = bar._sink
    assert not sink.closed
    bar.close()
    assert sink.closed
