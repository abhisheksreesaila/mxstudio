"""Console data: per-bucket series for traces and charts, and the deterministic insight rules."""
from mxconsole import insights, metrics
from mxconsole.store import Store
from test_metrics import rec

NOW = 100_000.0
NOKEY = dict(app=None, model=None, status=401, ttft_ms=None, completion_tokens=None, prompt_tokens=None)


def test_series_buckets_per_model(tmp_path):
    store = Store(tmp_path / "mst.db")
    for _ in range(3):
        rec(store, ts=NOW - 25, model="a", ttft_ms=40, duration_ms=540)  # decode 99 tok / 0.5 s = 198
    rec(store, ts=NOW - 25, model="a", status=500)
    rec(store, ts=NOW - 100, model="b", ttft_ms=80, duration_ms=1080)
    rec(store, ts=NOW - 400, model="old")  # outside 5m
    s = metrics.series(store, "5m", now=NOW)
    assert s["bucket_seconds"] == 10 and len(s["buckets"]) == 30
    a = s["models"]["a"]
    assert len(a) == 30 and a[-1]["t"] <= NOW <= a[-1]["t"] + 10
    busy = [b for b in a if b["requests"]][0]
    assert busy["requests"] == 4 and busy["errors"] == 1 and busy["ok"] == 3
    assert busy["decode_tps_p50"] == 198.0 and busy["ttft_p50_ms"] == 40.0
    assert a[0]["requests"] == 0 and a[0]["decode_tps_p50"] is None
    assert sum(b["requests"] for b in s["models"]["b"]) == 1
    assert "old" not in s["models"]


def test_series_ranges_and_unknown_range(tmp_path):
    store = Store(tmp_path / "mst.db")
    assert metrics.series(store, "1h", now=NOW)["bucket_seconds"] == 60
    assert len(metrics.series(store, "24h", now=NOW)["buckets"]) == 48
    assert metrics.series(store, "7d", now=NOW)["bucket_seconds"] == 3 * 3600
    try:
        metrics.series(store, "9y", now=NOW)
    except ValueError:
        pass
    else:
        raise AssertionError("an unknown range must raise")


def test_count_status_since(tmp_path):
    store = Store(tmp_path / "mst.db")
    rec(store, ts=NOW - 10)
    rec(store, ts=NOW - 10, **NOKEY)
    assert metrics.count_status(store, 401, since=NOW - 3600) == 1
    assert metrics.count_status(store, 401, since=NOW - 5) == 0


def models(**states):
    return [{"name": n, "state": s[0], "pinned": s[1]} for n, s in states.items()]


def test_nothing_to_report_when_traffic_is_healthy(tmp_path):
    store = Store(tmp_path / "mst.db")
    for i in range(30):
        rec(store, ts=NOW - 60 * i - 5, model="a")
    assert insights.compute(store, models(a=("ready", False)), now=NOW) == []


def test_loaded_model_idle_for_a_day_suggests_unloading(tmp_path):
    store = Store(tmp_path / "mst.db")
    rec(store, ts=NOW - 30 * 3600, model="big")  # last call was 30 h ago
    rec(store, ts=NOW - 60, model="used")
    out = insights.compute(store, models(big=("ready", True), used=("ready", False), cold=("cold", False)), now=NOW)
    assert [i["id"] for i in out] == ["idle:big"]
    assert "unload" in (out[0]["title"] + out[0]["detail"]).lower()
    assert out[0]["cli"] == "mxc stop big"


def test_ttft_rise_over_thirty_percent(tmp_path):
    store = Store(tmp_path / "mst.db")
    for i in range(6):
        rec(store, ts=NOW - 5000 - i * 60, model="a", ttft_ms=50)  # the hour before
        rec(store, ts=NOW - 100 - i * 60, model="a", ttft_ms=80)  # this hour: +60%
    out = insights.compute(store, models(a=("ready", False)), now=NOW)
    assert any(i["id"] == "ttft:a" for i in out)
    mild = Store(tmp_path / "other.db")  # +20% does not trigger
    for i in range(6):
        rec(mild, ts=NOW - 5000 - i * 60, model="a", ttft_ms=50)
        rec(mild, ts=NOW - 100 - i * 60, model="a", ttft_ms=60)
    assert insights.compute(mild, models(a=("ready", False)), now=NOW) == []


def test_requests_without_a_key(tmp_path):
    store = Store(tmp_path / "mst.db")
    for _ in range(3):
        rec(store, ts=NOW - 100, **NOKEY)
    out = insights.compute(store, [], now=NOW)
    assert [i["id"] for i in out] == ["nokey"] and "3" in out[0]["title"]


def test_error_rate_over_two_percent_ignores_401(tmp_path):
    store = Store(tmp_path / "mst.db")
    for i in range(40):
        rec(store, ts=NOW - 30 - i, model="a", status=503 if i < 3 else 200)  # 7.5%
    for _ in range(20):
        rec(store, ts=NOW - 40, **NOKEY)
    out = {i["id"]: i for i in insights.compute(store, models(a=("ready", False)), now=NOW)}
    assert "errors" in out and "7.5%" in out["errors"]["title"]
    low = Store(tmp_path / "other.db")  # one failure in 100 is under the threshold
    for i in range(100):
        rec(low, ts=NOW - 30 - i, model="a", status=500 if i == 0 else 200)
    assert insights.compute(low, models(a=("ready", False)), now=NOW) == []


def test_idle_rule_skips_models_that_are_starting_or_busy(tmp_path):
    store = Store(tmp_path / "mst.db")
    rows = [{"name": "boot", "state": "starting", "pinned": False},
            {"name": "busy", "state": "ready", "pinned": False, "idle_seconds": None, "in_flight": 1},
            {"name": "quiet", "state": "ready", "pinned": False, "idle_seconds": None, "in_flight": 0}]
    assert [i["id"] for i in insights.compute(store, rows, now=NOW)] == ["idle:quiet"]
