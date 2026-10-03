import json
import time

from typer.testing import CliRunner

from mxconsole import metrics
from mxconsole.cli import app
from mxconsole.store import Store

runner = CliRunner()


def rec(model="tiny", status=200, ago=10, ttft=100.0, dur=1100.0, comp=11):
    metrics.record(Store.open(), app="notes", model=model, route="/v1/chat/completions", status=status,
                   prompt_tokens=5, completion_tokens=comp, ttft_ms=ttft, duration_ms=dur, estimated=False,
                   ts=time.time() - ago)


def test_metrics_table_and_json_match_the_store(monkeypatch):
    monkeypatch.setattr(metrics, "gpus", lambda: [{"index": 0, "name": "RTX", "used_mb": 100, "total_mb": 1000, "util": 7}])
    rec(); rec(status=500, ttft=None); rec(ago=4000)  # the last is outside 5m
    out = runner.invoke(app, ["metrics"]).output
    assert "requests" in out and "TTFT p50" in out and "tiny" in out and "RTX" in out and "/console#metrics" in out
    data = json.loads(runner.invoke(app, ["metrics", "--json"]).output)
    assert data["requests"] == 2 and data["errors"] == 1 and data["range"] == "5m"
    assert data["by_model"]["tiny"]["requests"] == 2 and data["gpus"][0]["name"] == "RTX"
    assert json.loads(runner.invoke(app, ["metrics", "--range", "24h", "--json"]).output)["requests"] == 3


def test_metrics_bad_range_is_a_one_line_error():
    r = runner.invoke(app, ["metrics", "--range", "3d"])
    assert r.exit_code == 1 and len(r.output.strip().splitlines()) == 1


def test_metrics_with_no_traffic_and_no_gpu(monkeypatch):
    monkeypatch.setattr(metrics, "gpus", lambda: [])
    r = runner.invoke(app, ["metrics"])
    assert r.exit_code == 0 and "GPU" not in r.output and "-" in r.output


def test_requests_table_limit_and_json():
    for i in range(3):
        rec(ago=i + 1)
    out = runner.invoke(app, ["requests", "--limit", "2"]).output
    assert out.count("/v1/chat/completions") == 2 and "/console#activity" in out
    rows = json.loads(runner.invoke(app, ["requests", "--limit", "2", "--json"]).output)
    assert len(rows) == 2 and rows[0]["model"] == "tiny"


def test_requests_empty():
    assert "no requests yet" in runner.invoke(app, ["requests"]).output
    assert json.loads(runner.invoke(app, ["requests", "--json"]).output) == []
