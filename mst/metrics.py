"""Per-request metrics for the console: recorded by the gateway, summarised for the Overview gauges."""
from __future__ import annotations

import json
import subprocess
import time

from mst.store import Store

RETENTION_SECONDS = 7 * 24 * 3600
MAX_JSON_BYTES = 1_000_000  # a non-streamed reply larger than this isn't parsed for usage


def record(store: Store, *, app: str | None, model: str | None, route: str, status: int,
           prompt_tokens: int | None, completion_tokens: int | None, ttft_ms: float | None,
           duration_ms: float, estimated: bool, ts: float | None = None) -> None:
    store.db.execute(
        """INSERT INTO requests (ts, app, model, route, status, prompt_tokens, completion_tokens,
                                 ttft_ms, duration_ms, estimated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (time.time() if ts is None else ts, app, model, route, status, prompt_tokens, completion_tokens,
         ttft_ms, duration_ms, int(estimated)))


def recent(store: Store, limit: int = 50) -> list[dict]:
    rows = store.db.execute("SELECT * FROM requests ORDER BY ts DESC, id DESC LIMIT ?", (limit,))
    return [{**dict(r), "estimated": bool(r["estimated"])} for r in rows]


def prune(store: Store, now: float | None = None) -> None:
    store.db.execute("DELETE FROM requests WHERE ts < ?", ((now or time.time()) - RETENTION_SECONDS,))


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    k = (len(values) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return round(values[lo] + (values[hi] - values[lo]) * (k - lo), 1)


def _decode_tps(r) -> float | None:
    """Completion tokens per second after the first token: the steady-state decode speed."""
    if not r["completion_tokens"] or r["ttft_ms"] is None:
        return None
    seconds = (r["duration_ms"] - r["ttft_ms"]) / 1000
    return r["completion_tokens"] / seconds if seconds > 0 else None


def _stats(rows) -> dict:
    ok = [r for r in rows if r["status"] < 400]
    tps = [t for t in map(_decode_tps, ok) if t is not None]
    return {"requests": len(rows), "errors": len(rows) - len(ok),
            "ttft_p50_ms": _percentile([r["ttft_ms"] for r in ok if r["ttft_ms"] is not None], 0.5),
            "ttft_p95_ms": _percentile([r["ttft_ms"] for r in ok if r["ttft_ms"] is not None], 0.95),
            "decode_tps_p50": _percentile(tps, 0.5)}


def summary(store: Store, window: float = 300, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    rows = store.db.execute("SELECT * FROM requests WHERE ts >= ?", (now - window,)).fetchall()
    out = _stats(rows)
    out["window_seconds"] = window
    out["per_minute"] = round(len(rows) / (window / 60), 2)
    out["error_rate"] = round(out["errors"] / len(rows), 3) if rows else 0.0
    models = sorted({r["model"] for r in rows if r["model"]})
    out["by_model"] = {m: _stats([r for r in rows if r["model"] == m]) for m in models}
    return out


class StreamMeter:
    """Reads an SSE stream as it passes through: content chunks (≈ tokens) and any usage block."""

    def __init__(self):
        self.buffer = b""
        self.chunks = 0
        self.prompt_tokens: int | None = None
        self.usage_completion: int | None = None

    def feed(self, data: bytes) -> None:
        self.buffer += data
        *events, self.buffer = self.buffer.split(b"\n\n")
        for event in events:
            for line in event.split(b"\n"):
                if not line.startswith(b"data:") or line[5:].strip() == b"[DONE]":
                    continue
                try:
                    chunk = json.loads(line[5:])
                except ValueError:
                    continue
                usage = chunk.get("usage") if isinstance(chunk, dict) else None
                if isinstance(usage, dict):
                    self.prompt_tokens = usage.get("prompt_tokens")
                    self.usage_completion = usage.get("completion_tokens")
                for choice in (chunk.get("choices") or []) if isinstance(chunk, dict) else []:
                    text = (choice.get("delta") or {}).get("content") or choice.get("text")
                    if text:
                        self.chunks += 1

    @property
    def completion_tokens(self) -> int:
        return self.usage_completion if self.usage_completion is not None else self.chunks

    @property
    def estimated(self) -> bool:  # without a usage block, one content chunk counts as one token
        return self.usage_completion is None


class JsonMeter:
    """Reads `usage` from a non-streamed JSON reply."""

    def __init__(self):
        self.body = b""
        self.prompt_tokens: int | None = None
        self.completion_tokens: int | None = None
        self.estimated = False

    def feed(self, data: bytes) -> None:
        if len(self.body) < MAX_JSON_BYTES:
            self.body += data

    def finish(self) -> None:
        try:
            usage = json.loads(self.body).get("usage") or {}
        except (ValueError, AttributeError):
            return
        self.prompt_tokens = usage.get("prompt_tokens")
        self.completion_tokens = usage.get("completion_tokens")


def _nvidia_smi() -> str:
    return subprocess.run(
        ["nvidia-smi", "--query-gpu=index,name,memory.used,memory.total,utilization.gpu",
         "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5, check=True).stdout


def gpus(runner=_nvidia_smi) -> list[dict]:
    """Memory and utilisation per NVIDIA GPU; [] when there is no nvidia-smi."""
    try:
        out = runner()
    except (OSError, subprocess.SubprocessError):
        return []
    result = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 5:
            continue
        try:
            result.append({"index": int(parts[0]), "name": parts[1], "used_mb": int(parts[2]),
                           "total_mb": int(parts[3]), "util": int(parts[4])})
        except ValueError:
            continue
    return result
