"""The pull job lifecycle, against a fake Hugging Face that downloads small files slowly."""
import threading
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
