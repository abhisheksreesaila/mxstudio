"""Per-request metrics for the console: recorded by the gateway, summarised for the Overview gauges."""
from __future__ import annotations

import json
import subprocess
import time

from mxconsole.store import Store

RETENTION_SECONDS = 7 * 24 * 3600
MAX_JSON_BYTES = 1_000_000  # a non-streamed reply larger than this isn't parsed for usage
MAX_EVENT_BYTES = 256_000  # one SSE event larger than this is dropped, not buffered


def record(store: Store, *, app: str | None, model: str | None, route: str, status: int,
           prompt_tokens: int | None, completion_tokens: int | None, ttft_ms: float | None,
           duration_ms: float, estimated: bool, ts: float | None = None) -> None:
    store.db.execute(
        """INSERT INTO requests (ts, app, model, route, status, prompt_tokens, completion_tokens,
                                 ttft_ms, duration_ms, estimated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (time.time() if ts is None else ts, app, model, route, status, prompt_tokens, completion_tokens,
         ttft_ms, duration_ms, int(estimated)))


def recent(store: Store, limit: int = 50, model: str | None = None) -> list[dict]:
    where, args = ("WHERE model = ?", (model,)) if model else ("", ())
    rows = store.db.execute(f"SELECT * FROM requests {where} ORDER BY ts DESC, id DESC LIMIT ?", (*args, limit))
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
    if not r["completion_tokens"] or r["completion_tokens"] < 2 or r["ttft_ms"] is None:
        return None
    seconds = (r["duration_ms"] - r["ttft_ms"]) / 1000
    return (r["completion_tokens"] - 1) / seconds if seconds > 0 else None  # the first token is in the TTFT


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


RANGES = {"5m": (300, 10), "1h": (3600, 60), "24h": (86400, 1800), "7d": (7 * 86400, 3 * 3600)}


def range_seconds(name: str) -> int:
    if name not in RANGES:
        raise ValueError(f"range must be one of {', '.join(RANGES)}")
    return RANGES[name][0]


def series(store: Store, range_name: str, now: float | None = None) -> dict:
    """Requests bucketed in time, per model: the data behind the console's traces and charts."""
    span = range_seconds(range_name)
    step = RANGES[range_name][1]
    now = time.time() if now is None else now
    count = span // step
    first = (int(now) // step - count + 1) * step  # buckets sit on the clock; the last one holds `now`
    rows = store.db.execute("SELECT * FROM requests WHERE ts >= ? AND model IS NOT NULL", (first,)).fetchall()
    grouped: dict[str, list[list]] = {}
    for r in rows:
        i = int(r["ts"] - first) // step
        if 0 <= i < count:
            grouped.setdefault(r["model"], [[] for _ in range(count)])[i].append(r)
    models = {}
    for name, buckets in sorted(grouped.items()):
        out = []
        for i, group in enumerate(buckets):
            st = _stats(group)
            out.append({"t": first + i * step, "requests": st["requests"], "errors": st["errors"],
                        "ok": st["requests"] - st["errors"], "ttft_p50_ms": st["ttft_p50_ms"],
                        "decode_tps_p50": st["decode_tps_p50"]})
        models[name] = out
    return {"range": range_name, "bucket_seconds": step, "start": first, "end": first + count * step,
            "buckets": [first + i * step for i in range(count)], "models": models}


def count_status(store: Store, status: int, since: float) -> int:
    row = store.db.execute("SELECT COUNT(*) AS n FROM requests WHERE status = ? AND ts >= ?",
                           (status, since)).fetchone()
    return row["n"]


class StreamMeter:
    """Reads an SSE stream as it passes through: generated chunks (≈ tokens) and any usage block.
    It never raises: a metrics problem must not break the reply it is watching."""

    def __init__(self):
        self.buffer = b""
        self.chunks = 0
        self.prompt_tokens: int | None = None
        self.usage_completion: int | None = None

    def feed(self, data: bytes) -> None:
        try:
            self.buffer += data
            *events, self.buffer = self.buffer.split(b"\n\n")
            if len(self.buffer) > MAX_EVENT_BYTES:  # no separator in sight: stop holding it
                self.buffer = b""
            for event in events:
                for line in event.split(b"\n"):
                    if line.startswith(b"data:") and line[5:].strip() != b"[DONE]":
                        self._event(line[5:])
        except Exception:
            self.buffer = b""

    def _event(self, payload: bytes) -> None:
        try:
            chunk = json.loads(payload)
        except ValueError:
            return
        if not isinstance(chunk, dict):
            return
        usage = chunk.get("usage")
        if isinstance(usage, dict):
            self.prompt_tokens = usage.get("prompt_tokens")
            self.usage_completion = usage.get("completion_tokens")
        generated = False
        for choice in chunk.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            parts = [choice.get("text")]
            if isinstance(delta, dict):  # content, reasoning (MAX: reasoning_content / reasoning), tool calls
                parts += [delta.get(k) for k in ("content", "reasoning_content", "reasoning", "tool_calls")]
            generated = generated or any(parts)
        if generated:
            count = chunk.get("token_count")  # MAX can put several tokens in one chunk
            self.chunks += count if isinstance(count, int) and count > 0 else 1

    @property
    def completion_tokens(self) -> int:
        return self.usage_completion if self.usage_completion is not None else self.chunks

    @property
    def estimated(self) -> bool:  # without a usage block, chunks are counted as tokens
        return self.usage_completion is None


class JsonMeter:
    """Reads `usage` from a non-streamed JSON reply. Never raises; skips replies over the cap."""

    def __init__(self):
        self.body = b""
        self.too_big = False
        self.prompt_tokens: int | None = None
        self.completion_tokens: int | None = None
        self.estimated = False

    def feed(self, data: bytes) -> None:
        if len(self.body) + len(data) > MAX_JSON_BYTES:
            self.too_big, self.body = True, b""
        elif not self.too_big:
            self.body += data

    def finish(self) -> None:
        if self.too_big:
            return
        try:
            usage = json.loads(self.body).get("usage")
        except (ValueError, AttributeError):
            return
        if isinstance(usage, dict):
            self.prompt_tokens = usage.get("prompt_tokens")
            self.completion_tokens = usage.get("completion_tokens")


def _nvidia_smi() -> str:
    return subprocess.run(
        ["nvidia-smi", "--query-gpu=index,name,memory.used,memory.total,utilization.gpu",
         "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5, check=True).stdout


def _nvidia_smi_apps() -> str:
    return subprocess.run(["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"],
                          capture_output=True, text=True, timeout=5, check=True).stdout


def gpu_processes(runner=_nvidia_smi_apps) -> dict[int, int]:
    """GPU memory in MB per process id, summed over GPUs; {} when nvidia-smi is missing or lists none."""
    try:
        out = runner()
    except (OSError, subprocess.SubprocessError):
        return {}
    result: dict[int, int] = {}
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        try:
            pid, mb = int(parts[0]), int(parts[1])
        except (ValueError, IndexError):  # "[N/A]" where the driver hides a process's memory
            continue
        result[pid] = result.get(pid, 0) + mb
    return result


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
