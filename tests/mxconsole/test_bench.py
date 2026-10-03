"""Benchmarks (F-020): the workload, the maths, the stored results, the admin routes and `mxc bench`."""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from mxconsole import bench
from mxconsole.cli import app as cli_app
from mxconsole.store import Store
from test_console import ADMIN
from test_gateway import client, http, store, sup  # noqa: F401  (shared fixtures)

FAKE = str(Path(__file__).parent / "fake_max.py")
runner = CliRunner()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# -- the workload and the maths --------------------------------------------------------------------------
def test_the_prompt_is_fixed_and_about_512_tokens():
    assert bench.PROMPT == bench.build_prompt()
    assert 480 <= bench.estimate_tokens(bench.PROMPT) <= 530
    assert bench.MAX_TOKENS == 128 and bench.DEFAULT_RUNS == 5


def test_decode_and_prefill_rates_from_fixed_timings():
    s = bench.Sample(ttft_ms=200, duration_ms=1450, prompt_tokens=500, completion_tokens=126)
    assert s.decode_tps == pytest.approx(100.0)  # (126-1) tokens over 1.25 s
    assert s.prefill_tps == pytest.approx(2500.0)  # 500 tokens in 0.2 s
    assert bench.Sample(ttft_ms=100, duration_ms=100, prompt_tokens=5, completion_tokens=9).decode_tps is None
    assert bench.Sample(ttft_ms=100, duration_ms=900, prompt_tokens=5, completion_tokens=1).decode_tps is None
    assert bench.Sample(error="boom").decode_tps is None and bench.Sample(error="boom").prefill_tps is None


def test_summary_takes_p50_and_p95_and_counts_errors():
    runs = [bench.Sample(ttft_ms=t, duration_ms=t + 1000, prompt_tokens=400, completion_tokens=101)
            for t in (100, 110, 120, 130, 200)]  # decode: 100 tok/s each
    runs.append(bench.Sample(error="HTTP 500"))
    out = bench.summarize(runs)
    assert out["runs_ok"] == 5 and out["errors"] == 1
    assert out["ttft_p50_ms"] == 120 and out["ttft_p95_ms"] == 186  # linear interpolation, like the metrics page
    assert out["decode_tps"] == 100.0
    assert out["prefill_tps"] == 3333.3  # p50 of 400 / ttft
    assert out["duration_ms"] == 1120 and out["total_ms"] == 5660
    assert bench.summarize([bench.Sample(error="x")])["decode_tps"] is None


# -- one real streamed request against the fake max -------------------------------------------------------
@pytest.fixture
def fake_max(monkeypatch):
    procs = []

    def start(**env):
        port = free_port()
        procs.append(subprocess.Popen([sys.executable, FAKE, "--port", str(port), "--served-model-name", "m"],
                                      env={**os.environ, **env}))
        for _ in range(100):
            try:
                httpx.get(f"http://127.0.0.1:{port}/health", timeout=0.2)
                break
            except httpx.HTTPError:
                time.sleep(0.05)
        return httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=30)

    yield start
    for p in procs:
        p.terminate()
        p.wait()


async def test_one_request_times_a_stream_and_reads_the_usage_block(fake_max):
    c = fake_max(FAKE_MAX_TOKENS_OUT="40", FAKE_MAX_CHUNK_DELAY="0.01", FAKE_MAX_TTFT_DELAY="0.1", FAKE_MAX_USAGE="1")
    s = await bench.one_request(c, "m", "k")
    assert s.error is None and s.completion_tokens == 40 and not s.estimated
    assert 90 <= s.ttft_ms < 300
    assert 40 <= s.decode_tps <= 105  # one chunk per 10 ms plus scheduling: never above 100
    assert s.prompt_tokens > 400  # from the usage block


async def test_without_a_usage_block_the_tokens_are_estimated(fake_max):
    c = fake_max(FAKE_MAX_TOKENS_OUT="10")
    s = await bench.one_request(c, "m", "k")
    assert s.completion_tokens == 10 and s.estimated
    assert s.prompt_tokens == bench.estimate_tokens(bench.PROMPT)


async def test_max_tokens_caps_the_stream_and_errors_become_a_sample(fake_max):
    c = fake_max(FAKE_MAX_TOKENS_OUT="500")
    assert (await bench.one_request(c, "m", "k")).completion_tokens == 128
    bad = httpx.AsyncClient(base_url="http://127.0.0.1:9", timeout=2)
    assert (await bench.one_request(bad, "m", "k")).error


# -- the admin routes --------------------------------------------------------------------------------------
BENCH_ROUTES = [("POST", "/admin/bench"), ("GET", "/admin/bench"), ("GET", "/admin/bench/history")]


async def test_bench_routes_need_the_admin_header_and_loopback(http, store, sup):
    from mxconsole.gateway import create_app
    for method, path in BENCH_ROUTES:
        assert (await http.request(method, path)).status_code == 403, path
        assert (await http.request(method, path, headers={**ADMIN, "X-Forwarded-For": "10.0.0.2"})).status_code == 403, path
        assert (await http.request(method, path, headers={**ADMIN, "Origin": "https://evil.example"})).status_code == 403, path
    async with client(create_app(store, sup), host="192.168.1.20") as lan:
        for method, path in BENCH_ROUTES:
            assert (await lan.request(method, path, headers=ADMIN)).status_code == 403, path
    assert (await http.get("/admin/bench", headers=ADMIN)).json() == {"running": False, "progress": None, "latest": None}


async def test_start_validates_its_body(http):
    async def post(body):
        return await http.post("/admin/bench", headers=ADMIN, content=body if isinstance(body, bytes) else json.dumps(body))
    assert (await post(b"nope")).status_code == 400
    assert (await post({"runs": 0})).status_code == 400
    assert (await post({"runs": 5.5})).status_code == 400
    assert (await post({"runs": True})).status_code == 400
    assert (await post({"models": []})).status_code == 400
    assert (await post({"models": "tiny"})).status_code == 400
    assert (await post({"extra": 1})).status_code == 400
    r = await post({"models": ["ghost"]})
    assert r.status_code == 404 and "ghost" in r.json()["error"]["message"]


async def test_start_with_no_registered_models_says_so(tmp_path, monkeypatch):
    from mxconsole.gateway import create_app
    from mxconsole.supervisor import Supervisor
    empty = Store(tmp_path / "empty.db")
    async with client(create_app(empty, Supervisor(empty, max_command=(sys.executable, FAKE)))) as c:
        r = await c.post("/admin/bench", headers=ADMIN, content=b"{}")
        assert r.status_code == 400 and "no models" in r.json()["error"]["message"]


async def test_only_one_benchmark_runs_at_a_time(http, store, monkeypatch):
    import asyncio

    release = asyncio.Event()

    async def slow(store_, supervisor, progress, **kw):
        await release.wait()
        store_.db.execute("UPDATE bench_runs SET status = 'done' WHERE id = ?", (progress.run_id,))

    monkeypatch.setattr(bench, "run_all", slow)
    first = await http.post("/admin/bench", headers=ADMIN, content=b'{"models": ["tiny"], "runs": 2}')
    assert first.status_code == 202 and first.json()["models"] == ["tiny"] and first.json()["runs"] == 2
    again = await http.post("/admin/bench", headers=ADMIN, content=b"{}")
    assert again.status_code == 409 and again.json()["error"]["code"] == "bench_running"
    live = (await http.get("/admin/bench", headers=ADMIN)).json()
    assert live["running"] and live["progress"]["id"] == first.json()["id"]
    assert [m["state"] for m in live["progress"]["models"]] == ["queued"]
    release.set()
    for _ in range(50):
        if not (await http.get("/admin/bench", headers=ADMIN)).json()["running"]:
            break
        await asyncio.sleep(0.02)
    assert (await http.post("/admin/bench", headers=ADMIN, content=b'{"models": ["tiny"]}')).status_code == 202


async def test_polling_reads_never_trip_over_the_shared_connection(http, store):
    import asyncio
    p = bench.start_run(store, ["tiny"], 1)
    s = [bench.Sample(ttft_ms=100, duration_ms=1100, prompt_tokens=500, completion_tokens=101)]
    bench._save_result(store, p.run_id, "tiny", "done", None, None, s, bench.summarize(s))
    store.db.execute("UPDATE bench_runs SET status = 'done' WHERE id = ?", (p.run_id,))
    calls = [http.get(path, headers=ADMIN) for path in ("/admin/bench", "/admin/bench/history") * 40]
    assert {r.status_code for r in await asyncio.gather(*calls)} == {200}


def test_a_run_left_running_by_a_dead_daemon_is_marked_interrupted(store):
    bench.start_run(store, ["tiny"], 5)
    from mxconsole import admin_bench
    admin_bench.register(type("A", (), {"post": lambda *a, **k: (lambda f: f), "get": lambda *a, **k: (lambda f: f)})(),
                         store, None, lambda r: True)
    assert [r["status"] for r in bench.history(store)] == ["interrupted"]
    assert bench.latest(store) is None  # nothing finished: nothing to show


# -- stored results ------------------------------------------------------------------------------------------
def test_latest_sorts_by_decode_and_keeps_failures_last(store):
    p = bench.start_run(store, ["tiny", "other"], 3)
    for name, tps in (("tiny", 80.0), ("other", 200.0)):
        s = [bench.Sample(ttft_ms=100, duration_ms=100 + 1000 * 100 / tps, prompt_tokens=500, completion_tokens=101)] * 3
        bench._save_result(store, p.run_id, name, "done", None, None, s, bench.summarize(s))
    store.db.execute("UPDATE bench_runs SET status = 'done' WHERE id = ?", (p.run_id,))
    run = bench.latest(store)
    assert [r["model"] for r in run["results"]] == ["other", "tiny"]
    assert [r["place"] for r in run["results"]] == [1, 2]
    assert run["results"][0]["decode_tps"] == 200.0 and run["runs"] == 3 and run["max_tokens"] == 128
    assert store.db.execute("SELECT COUNT(*) AS n FROM bench_samples").fetchone()["n"] == 6


# -- end to end: a real daemon with the fake max, through the CLI ---------------------------------------------
@pytest.fixture
def daemon(tmp_path, monkeypatch):
    yield from start_daemon(tmp_path, monkeypatch)


def start_daemon(tmp_path, monkeypatch, **extra):
    port = str(free_port())
    s = Store.open()
    for name in ("fast", "slow", "broken"):
        s.add_model(name=name, repo_id=f"org/{name}", revision="abc", source="hf", architecture="X")
    s.db.close()
    monkeypatch.setenv("MXC_PORT", port)
    env = {**os.environ, "MXC_PORT": port, "MXC_MAX_CMD": f"{sys.executable} {FAKE}",
           "FAKE_MAX_TOKENS_OUT": "30", "FAKE_MAX_USAGE": "1", "FAKE_MAX_MODEL_DELAYS": "fast=0.004,slow=0.012,broken=0", **extra}
    log = open(tmp_path / "daemon.log", "wb")
    proc = subprocess.Popen([sys.executable, "-m", "mxconsole", "daemon"], env=env, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(200):
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=0.3).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail((tmp_path / "daemon.log").read_text())
    try:
        yield port
    finally:
        try:
            httpx.post(f"http://127.0.0.1:{port}/admin/shutdown", headers=ADMIN, timeout=20)
            proc.wait(timeout=20)
        except Exception:
            proc.kill()


def test_mxc_bench_runs_every_model_and_prints_a_table_fastest_first(daemon):
    r = runner.invoke(cli_app, ["bench", "fast", "slow", "--runs", "2"])
    assert r.exit_code == 0, r.output
    lines = r.output.splitlines()
    assert any(l.startswith("fast") and "done" in l for l in lines)  # progress per model
    table = lines[next(i for i, l in enumerate(lines) if l.startswith("#")):]
    assert table[1].split()[1] == "fast" and table[2].split()[1] == "slow"  # sorted by decode speed
    assert "2 runs after a warm-up" in r.output and "/console#benchmarks" in r.output
    # the warm-up isn't counted, and the load time is kept apart
    run = bench.latest(Store.open())
    assert [x["runs_ok"] for x in run["results"]] == [2, 2]
    assert run["results"][0]["load_seconds"] is not None and run["results"][0]["decode_tps"] > run["results"][1]["decode_tps"]
    assert Store.open().db.execute("SELECT COUNT(*) AS n FROM bench_samples").fetchone()["n"] == 4


def test_mxc_bench_json_is_json_only_and_all_covers_every_model(daemon):
    r = runner.invoke(cli_app, ["bench", "--all", "--runs", "1", "--json"])
    assert r.exit_code == 0, r.output
    run = json.loads(r.output)
    assert sorted(x["model"] for x in run["results"]) == ["broken", "fast", "slow"]
    assert run["status"] == "done" and run["runs"] == 1
    assert {"decode_tps", "prefill_tps", "ttft_p50_ms", "ttft_p95_ms", "duration_ms", "errors", "load_seconds"} <= set(run["results"][0])
    # the same run is what the admin routes serve
    got = httpx.get(f"http://127.0.0.1:{daemon}/admin/bench", headers=ADMIN).json()
    assert got["running"] is False and got["latest"]["id"] == run["id"]
    hist = httpx.get(f"http://127.0.0.1:{daemon}/admin/bench/history", headers=ADMIN).json()
    assert [h["id"] for h in hist] == [run["id"]]
    # the benchmark's own key is internal: never listed, revoked when done
    assert "_bench" not in [k["app"] for k in httpx.get(f"http://127.0.0.1:{daemon}/admin/keys", headers=ADMIN).json()]


def test_mxc_bench_exits_1_when_every_model_failed(monkeypatch):
    Store.open().add_model(name="fast", repo_id="o/f", revision="a", source="hf", architecture="X")
    from mxconsole import serve
    monkeypatch.setattr(serve, "ensure_daemon", lambda: False)
    monkeypatch.setattr(bench, "start", lambda models, runs: {"id": 1, "models": ["fast"], "runs": runs})
    failed_run = {"id": 1, "status": "failed", "runs": 1, "max_tokens": 128, "prompt_tokens": 512, "started_at": 0,
                  "models": ["fast"], "results": [{"model": "fast", "status": "failed", "error": "boom", "load_seconds": None,
                                                    "runs_ok": 0, "errors": 0, "decode_tps": None, "prefill_tps": None,
                                                    "ttft_p50_ms": None, "ttft_p95_ms": None, "estimated": False}]}
    monkeypatch.setattr(bench, "follow", lambda run_id, on_progress, **kw: failed_run)
    r = runner.invoke(cli_app, ["bench", "fast"])
    assert r.exit_code == 1 and "failed: boom" in r.output
    r = runner.invoke(cli_app, ["bench", "fast", "--json"])
    assert r.exit_code == 1 and json.loads(r.output)["status"] == "failed"


def test_a_model_that_cannot_start_fails_alone_and_all_failed_exits_1(tmp_path, monkeypatch):
    daemon = start_daemon(tmp_path, monkeypatch, FAKE_MAX_FAIL="1")
    next(daemon)
    try:
        r = runner.invoke(cli_app, ["bench", "fast", "slow", "--runs", "1"])
        assert r.exit_code == 1, r.output
        assert "failed: could not load" in r.output
        run = bench.latest(Store.open())
        assert run["status"] == "failed" and [x["status"] for x in run["results"]] == ["failed", "failed"]
    finally:
        daemon.close()


def test_mxc_bench_usage_errors_are_one_line_on_stderr(daemon):
    for args in (["bench"], ["bench", "ghost"], ["bench", "fast", "--all"], ["bench", "fast", "--runs", "0"],
                 ["bench", "fast", "--runs", "99"]):
        r = runner.invoke(cli_app, args)
        assert r.exit_code in (1, 2), args
        assert len((r.stderr or r.output).strip().splitlines()) <= 6
    r = runner.invoke(cli_app, ["bench"])
    assert r.exit_code == 1 and "name models or use --all" in r.output
