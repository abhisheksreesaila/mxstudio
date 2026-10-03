"""Per-request metrics: what the Overview gauges and the activity log are built from."""
import json

from mxconsole import metrics
from mxconsole.store import Store


def rec(store, **kw):
    row = dict(app="a", model="m", route="chat", status=200, prompt_tokens=10, completion_tokens=100,
               ttft_ms=50.0, duration_ms=1050.0, estimated=False)
    row.update(kw)
    metrics.record(store, **row)


def test_recent_is_newest_first_and_limited(tmp_path):
    store = Store(tmp_path / "mst.db")
    for i in range(5):
        rec(store, app=f"app{i}", ts=1000 + i)
    rows = metrics.recent(store, limit=3)
    assert [r["app"] for r in rows] == ["app4", "app3", "app2"]


def test_summary_over_a_window(tmp_path):
    store = Store(tmp_path / "mst.db")
    now = 10_000.0
    # 4 ok requests: TTFT 40/50/60/200 ms; decode = 100 tokens over (duration - ttft) seconds
    for ttft in (40, 50, 60, 200):
        rec(store, ts=now - 30, ttft_ms=ttft, duration_ms=ttft + 500)  # 99 tok after the first / 0.5 s = 198 tok/s
    rec(store, ts=now - 20, status=401, app=None, model=None, prompt_tokens=None, completion_tokens=None,
        ttft_ms=None, duration_ms=1.0)
    rec(store, ts=now - 4000, ttft_ms=9999)  # outside the window
    s = metrics.summary(store, window=300, now=now)
    assert s["requests"] == 5
    assert s["per_minute"] == 1.0
    assert s["errors"] == 1 and s["error_rate"] == 0.2
    assert s["ttft_p50_ms"] == 55.0 and s["ttft_p95_ms"] == 179.0  # linear: 60 + (200 - 60) * 0.85
    assert s["decode_tps_p50"] == 198.0
    assert s["by_model"]["m"]["requests"] == 4


def test_summary_of_nothing_is_empty_not_an_error(tmp_path):
    s = metrics.summary(Store(tmp_path / "mst.db"), window=300, now=1.0)
    assert s["requests"] == 0 and s["ttft_p50_ms"] is None and s["decode_tps_p50"] is None


def test_old_rows_are_pruned(tmp_path):
    store = Store(tmp_path / "mst.db")
    rec(store, ts=1.0)
    rec(store, ts=metrics.RETENTION_SECONDS + 100.0)
    metrics.prune(store, now=metrics.RETENTION_SECONDS + 100.0)
    assert len(metrics.recent(store, limit=10)) == 1


def test_stream_meter_counts_tokens_and_reads_usage():
    m = metrics.StreamMeter()
    chunks = [{"choices": [{"delta": {"content": w}}]} for w in ("hel", "lo", " there")]
    body = b"".join(f"data: {json.dumps(c)}\n\n".encode() for c in chunks) + b"data: [DONE]\n\n"
    for i in range(0, len(body), 7):  # arbitrary splits across reads
        m.feed(body[i:i + 7])
    assert m.completion_tokens == 3 and m.estimated is True
    m2 = metrics.StreamMeter()
    m2.feed(b'data: {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 9}}\n\ndata: [DONE]\n\n')
    assert (m2.prompt_tokens, m2.completion_tokens, m2.estimated) == (5, 9, False)


def test_json_meter_reads_usage():
    m = metrics.JsonMeter()
    m.feed(json.dumps({"usage": {"prompt_tokens": 7, "completion_tokens": 21}}).encode())
    m.finish()
    assert (m.prompt_tokens, m.completion_tokens, m.estimated) == (7, 21, False)


def test_gpus_from_nvidia_smi_and_none_without_it():
    out = "0, NVIDIA GeForce RTX 3090, 58, 24576, 0\n1, NVIDIA GeForce RTX 3090, 1666, 24576, 10\n"
    gpus = metrics.gpus(runner=lambda: out)
    assert gpus[1] == {"index": 1, "name": "NVIDIA GeForce RTX 3090", "used_mb": 1666, "total_mb": 24576, "util": 10}

    def missing():
        raise FileNotFoundError("nvidia-smi")
    assert metrics.gpus(runner=missing) == []


def test_meters_never_raise_on_odd_upstream_data():
    m = metrics.StreamMeter()
    m.feed(b'data: {"choices":[{"delta":"x"}]}\n\ndata: [1,2]\n\ndata: {"usage": 5}\n\n')
    j = metrics.JsonMeter()
    j.feed(b'{"usage": 5}')
    j.finish()
    assert j.completion_tokens is None


def test_stream_meter_counts_reasoning_and_tool_call_deltas_and_token_counts():
    m = metrics.StreamMeter()
    events = [{"choices": [{"delta": {"reasoning_content": "think"}}]},
              {"choices": [{"delta": {"reasoning": "more"}}]},
              {"choices": [{"delta": {"tool_calls": [{"function": {"arguments": "{"}}]}}]},
              {"choices": [{"delta": {"content": "ok"}}], "token_count": 3}]
    m.feed(b"".join(f"data: {json.dumps(e)}\n\n".encode() for e in events))
    assert m.completion_tokens == 6  # 1 + 1 + 1 + 3 (MAX's per-chunk token_count wins when present)


def test_stream_meter_buffer_is_capped():
    m = metrics.StreamMeter()
    m.feed(b"x" * (metrics.MAX_EVENT_BYTES * 3))  # a stream that never separates events
    assert len(m.buffer) <= metrics.MAX_EVENT_BYTES


def test_json_meter_ignores_replies_over_the_cap():
    j = metrics.JsonMeter()
    j.feed(b'{"usage": {"completion_tokens": 3}, "pad": "' + b"x" * metrics.MAX_JSON_BYTES + b'"}')
    j.finish()
    assert len(j.body) <= metrics.MAX_JSON_BYTES and j.completion_tokens is None


def test_decode_speed_excludes_the_first_token():
    row = {"completion_tokens": 11, "ttft_ms": 100.0, "duration_ms": 1100.0}
    assert metrics._decode_tps(row) == 10.0  # (11 - 1) tokens in 1.0 s after the first
