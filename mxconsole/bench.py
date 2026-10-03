"""Benchmarks: the same streamed workload for every model, through the daemon's real /v1 path.

Workload: a fixed prompt of about 512 tokens, 128 tokens out, batch 1, N timed runs after one warm-up that
isn't counted. A cold model is loaded first and the load time is kept apart (it is never part of TTFT).
Per model we keep decode tok/s (p50 of (n-1)/(duration-ttft)), prefill tok/s (prompt tokens / ttft),
TTFT p50 and p95, run duration and errors, in SQLite. `mxc bench` and the console both read them.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from typing import Callable

import httpx

from mxconsole import keys
from mxconsole.metrics import StreamMeter, _percentile
from mxconsole.settings import daemon_url
from mxconsole.store import Store
from mxconsole.supervisor import StartFailed, Supervisor, UnknownModel

BENCH_APP = "_bench"  # the reserved key the benchmark calls the gateway with
DEFAULT_RUNS = 5
MAX_RUNS = 50
MAX_TOKENS = 128
PROMPT_TARGET = 512  # tokens, estimated at four characters each when MAX returns no usage block

SCHEMA = """
CREATE TABLE IF NOT EXISTS bench_runs (
    id INTEGER PRIMARY KEY,
    started_at REAL NOT NULL,
    finished_at REAL,
    status TEXT NOT NULL,
    runs INTEGER NOT NULL,
    max_tokens INTEGER NOT NULL,
    prompt_tokens INTEGER NOT NULL,
    models TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bench_results (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL,
    model TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    load_seconds REAL,
    runs_ok INTEGER NOT NULL,
    errors INTEGER NOT NULL,
    decode_tps REAL,
    prefill_tps REAL,
    ttft_p50_ms REAL,
    ttft_p95_ms REAL,
    duration_ms REAL,
    total_ms REAL,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    estimated INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS bench_results_run ON bench_results (run_id);
CREATE TABLE IF NOT EXISTS bench_samples (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL,
    model TEXT NOT NULL,
    idx INTEGER NOT NULL,
    ttft_ms REAL,
    duration_ms REAL,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    decode_tps REAL,
    prefill_tps REAL,
    error TEXT
);
CREATE INDEX IF NOT EXISTS bench_samples_run ON bench_samples (run_id);
"""


def ensure_schema(store: Store) -> None:
    store.db.executescript(SCHEMA)


# -- the workload ---------------------------------------------------------------------------------------
_SENTENCES = (
    "A workshop keeps its tools on a pegboard so that every wrench has an outline and a place to return to.",
    "On the first morning of the survey the team measured the river at three bridges and wrote each reading in a ledger.",
    "The recipe calls for flour, water and salt, and the only secret is to let the dough rest until it doubles.",
    "A lighthouse keeper climbs one hundred and twelve steps each evening to trim the wick and polish the lens.",
    "Trains leave the northern station every twenty minutes, and the timetable is printed on a card that fits a pocket.",
    "In the orchard the apples ripen from the south side first, which is why the pickers start at the low fence.",
    "The library moved its catalogue from paper cards to a database, and the old drawers became planters on the porch.",
    "A good benchmark repeats the same small task many times so that noise averages out and the signal remains.",
    "The ferry crosses the bay in forty minutes when the wind is calm and in an hour when it is not.",
    "Every spring the town repaints the bridge a slightly different shade of green, and nobody agrees which was best.",
)


def build_prompt(target_tokens: int = PROMPT_TARGET) -> str:
    """A deterministic passage of about `target_tokens` tokens (four characters each), then the task."""
    task = "Continue this passage in the same style, for as long as you can."
    budget = target_tokens * 4 - len(task) - 1
    parts: list[str] = []
    size, i = 0, 0
    while True:
        sentence = _SENTENCES[i % len(_SENTENCES)]
        if size + len(sentence) + 1 > budget:
            break
        parts.append(sentence)
        size += len(sentence) + 1
        i += 1
    return " ".join(parts) + " " + task


PROMPT = build_prompt()


def estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4))


# -- the maths ------------------------------------------------------------------------------------------
@dataclass
class Sample:
    ttft_ms: float | None = None
    duration_ms: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    estimated: bool = False
    error: str | None = None

    @property
    def decode_tps(self) -> float | None:
        """Tokens per second after the first one: the first token is in the TTFT."""
        if self.error or self.ttft_ms is None or self.duration_ms is None:
            return None
        if not self.completion_tokens or self.completion_tokens < 2:
            return None
        seconds = (self.duration_ms - self.ttft_ms) / 1000
        return (self.completion_tokens - 1) / seconds if seconds > 0 else None

    @property
    def prefill_tps(self) -> float | None:
        """Prompt tokens per second of time to first token."""
        if self.error or not self.ttft_ms or not self.prompt_tokens:
            return None
        return self.prompt_tokens / (self.ttft_ms / 1000)


def summarize(samples: list[Sample]) -> dict:
    """One model's counted runs, reduced to the numbers the table shows."""
    ok = [s for s in samples if not s.error]
    def p(values, q):
        values = [v for v in values if v is not None]
        return _percentile(values, q)
    return {
        "runs_ok": len(ok), "errors": len(samples) - len(ok),
        "decode_tps": p([s.decode_tps for s in ok], 0.5), "prefill_tps": p([s.prefill_tps for s in ok], 0.5),
        "ttft_p50_ms": p([s.ttft_ms for s in ok], 0.5), "ttft_p95_ms": p([s.ttft_ms for s in ok], 0.95),
        "duration_ms": p([s.duration_ms for s in ok], 0.5),
        "total_ms": round(sum(s.duration_ms for s in ok), 1) if ok else None,
        "prompt_tokens": ok[0].prompt_tokens if ok else None,
        "completion_tokens": ok[0].completion_tokens if ok else None,
        "estimated": any(s.estimated for s in ok),
    }


# -- one streamed request -------------------------------------------------------------------------------
def _error_message(status: int, body: bytes) -> str:
    try:
        return json.loads(body)["error"]["message"]
    except (ValueError, KeyError, TypeError):
        return f"HTTP {status}"


async def one_request(http: httpx.AsyncClient, model: str, key: str, *, prompt: str = PROMPT,
                      max_tokens: int = MAX_TOKENS, usage: bool = True,
                      clock: Callable[[], float] = time.monotonic) -> Sample:
    """Send the workload once and time it. Never raises: a failure is a Sample with `error`."""
    body = {"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens,
            "stream": True, "temperature": 0, "n": 1}
    if usage:
        body["stream_options"] = {"include_usage": True}
    meter = StreamMeter()
    started = clock()
    first: float | None = None
    try:
        async with http.stream("POST", "/v1/chat/completions", json=body,
                               headers={"Authorization": f"Bearer {key}"}) as r:
            if r.status_code != 200:
                message = _error_message(r.status_code, await r.aread())
                if usage and r.status_code in (400, 422):  # a server that rejects stream_options: ask without
                    return await one_request(http, model, key, prompt=prompt, max_tokens=max_tokens,
                                             usage=False, clock=clock)
                return Sample(error=message)
            async for chunk in r.aiter_raw():
                meter.feed(chunk)
                if first is None and meter.chunks > 0:
                    first = clock()
            ended = clock()
    except httpx.HTTPError as e:
        return Sample(error=f"{type(e).__name__}: {e}" if str(e) else type(e).__name__)
    if first is None:
        return Sample(error="the model streamed no tokens")
    estimated_prompt = meter.prompt_tokens is None
    return Sample(ttft_ms=(first - started) * 1000, duration_ms=(ended - started) * 1000,
                  prompt_tokens=meter.prompt_tokens if not estimated_prompt else estimate_tokens(prompt),
                  completion_tokens=meter.completion_tokens, estimated=meter.estimated or estimated_prompt)


# -- a whole benchmark ----------------------------------------------------------------------------------
@dataclass
class Progress:
    """What GET /admin/bench shows while a run is going (kept in memory: it only matters while running)."""
    run_id: int
    runs: int
    started_at: float
    models: list[dict] = field(default_factory=list)

    def set(self, name: str, **fields) -> None:
        next(m for m in self.models if m["name"] == name).update(fields)

    def as_dict(self) -> dict:
        return {"id": self.run_id, "runs": self.runs, "started_at": self.started_at,
                "models": [dict(m) for m in self.models]}


def start_run(store: Store, names: list[str], runs: int) -> Progress:
    ensure_schema(store)
    started = time.time()
    cur = store.db.execute(
        "INSERT INTO bench_runs (started_at, status, runs, max_tokens, prompt_tokens, models) VALUES (?, 'running', ?, ?, ?, ?)",
        (started, runs, MAX_TOKENS, estimate_tokens(PROMPT), json.dumps(names)))
    progress = Progress(cur.lastrowid, runs, started)
    progress.models = [{"name": n, "state": "queued", "done": 0, "error": None} for n in names]
    return progress


def mark_interrupted(store: Store) -> None:
    """A run still marked running belongs to a daemon that died."""
    ensure_schema(store)
    store.db.execute("UPDATE bench_runs SET status = 'interrupted', finished_at = ? WHERE status = 'running'", (time.time(),))


def _save_result(store: Store, run_id: int, name: str, status: str, error: str | None, load_seconds: float | None,
                 samples: list[Sample], summary: dict) -> None:
    store.db.execute(
        """INSERT INTO bench_results (run_id, model, status, error, load_seconds, runs_ok, errors, decode_tps,
             prefill_tps, ttft_p50_ms, ttft_p95_ms, duration_ms, total_ms, prompt_tokens, completion_tokens, estimated)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (run_id, name, status, error, load_seconds, summary["runs_ok"], summary["errors"], summary["decode_tps"],
         summary["prefill_tps"], summary["ttft_p50_ms"], summary["ttft_p95_ms"], summary["duration_ms"],
         summary["total_ms"], summary["prompt_tokens"], summary["completion_tokens"], int(summary["estimated"])))
    for i, s in enumerate(samples):
        store.db.execute(
            """INSERT INTO bench_samples (run_id, model, idx, ttft_ms, duration_ms, prompt_tokens, completion_tokens,
                 decode_tps, prefill_tps, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, name, i + 1, s.ttft_ms, s.duration_ms, s.prompt_tokens, s.completion_tokens, s.decode_tps,
             s.prefill_tps, s.error))


async def run_model(store: Store, supervisor: Supervisor, http: httpx.AsyncClient, key: str,
                    progress: Progress, name: str) -> None:
    """Load (if cold), warm up once, then time `progress.runs` runs, and store the result."""
    states = {s["name"]: s["state"] for s in supervisor.status()}
    load_seconds = None
    if states.get(name) != "ready":
        progress.set(name, state="loading")
        t0 = time.monotonic()
        try:
            await supervisor.ensure_running(name)
        except (StartFailed, UnknownModel) as e:
            _fail_model(store, progress, name, f"could not load: {e}", None)
            return
        load_seconds = round(time.monotonic() - t0, 2)
    progress.set(name, state="warming")
    warm = await one_request(http, name, key)  # not counted: the first request pays for caches and kernels
    if warm.error:
        _fail_model(store, progress, name, warm.error, load_seconds)
        return
    samples: list[Sample] = []
    progress.set(name, state="running", done=0)
    for i in range(progress.runs):
        samples.append(await one_request(http, name, key))
        progress.set(name, done=i + 1)
    summary = summarize(samples)
    failed = summary["runs_ok"] == 0
    error = next((s.error for s in samples if s.error), None)
    _save_result(store, progress.run_id, name, "failed" if failed else "done", error, load_seconds, samples, summary)
    progress.set(name, state="failed" if failed else "done", error=error,
                 decode_tps=summary["decode_tps"], load_seconds=load_seconds)


def _fail_model(store: Store, progress: Progress, name: str, error: str, load_seconds: float | None) -> None:
    _save_result(store, progress.run_id, name, "failed", error, load_seconds, [], summarize([]))
    progress.set(name, state="failed", error=error, load_seconds=load_seconds)


async def run_all(store: Store, supervisor: Supervisor, progress: Progress, *,
                  http_factory: Callable[[], httpx.AsyncClient] | None = None) -> None:
    """The whole benchmark, one model after another (they share the GPU: never in parallel)."""
    key = keys.create_key(store, BENCH_APP, rotate=True, internal=True)
    http = http_factory() if http_factory else httpx.AsyncClient(
        base_url=daemon_url(), timeout=httpx.Timeout(None, connect=10.0))
    status = "failed"
    try:
        for m in list(progress.models):
            await run_model(store, supervisor, http, key, progress, m["name"])
        ok = any(m["state"] == "done" for m in progress.models)
        status = "done" if ok else "failed"
    except BaseException:
        status = "interrupted"
        raise
    finally:
        await http.aclose()
        store.db.execute("UPDATE bench_runs SET status = ?, finished_at = ? WHERE id = ?",
                         (status, time.time(), progress.run_id))
        keys.revoke_key(store, BENCH_APP)


# -- reading results back -------------------------------------------------------------------------------
def _run_dict(store: Store, row) -> dict:
    results = [dict(r) | {"estimated": bool(r["estimated"])} for r in store.db.execute(
        "SELECT * FROM bench_results WHERE run_id = ?", (row["id"],))]
    results.sort(key=lambda r: (r["decode_tps"] is None, -(r["decode_tps"] or 0), r["model"]))
    for place, r in enumerate((r for r in results if r["decode_tps"] is not None), 1):
        r["place"] = place
    return {**dict(row), "models": json.loads(row["models"]), "results": results}


def latest(store: Store) -> dict | None:
    """The newest finished run, with every model's result fastest first."""
    ensure_schema(store)
    row = store.db.execute(
        "SELECT * FROM bench_runs WHERE status IN ('done', 'failed') ORDER BY started_at DESC, id DESC LIMIT 1").fetchone()
    return _run_dict(store, row) if row else None


def history(store: Store, limit: int = 20) -> list[dict]:
    ensure_schema(store)
    rows = store.db.execute("SELECT * FROM bench_runs ORDER BY started_at DESC, id DESC LIMIT ?", (limit,)).fetchall()
    return [_run_dict(store, r) for r in rows]


# -- the client side: what `mxc bench` does ---------------------------------------------------------------
ADMIN = {"X-Mst-Admin": "1"}


class BenchError(Exception):
    pass


def _call(method: str, path: str, **kw) -> dict:
    try:
        r = httpx.request(method, f"{daemon_url()}{path}", headers=ADMIN, timeout=10.0, **kw)
    except httpx.HTTPError as e:
        raise BenchError(f"could not reach the MX Console daemon at {daemon_url()}: {e}")
    if r.status_code >= 400:
        try:
            raise BenchError(r.json()["error"]["message"])
        except (ValueError, KeyError, TypeError):
            raise BenchError(r.text or f"HTTP {r.status_code}")
    return r.json()


def start(models: list[str] | None, runs: int) -> dict:
    return _call("POST", "/admin/bench", json={"models": models, "runs": runs})


def current() -> dict:
    return _call("GET", "/admin/bench")


def follow(run_id: int, on_progress: Callable[[dict], None], *, interval: float = 0.5,
           sleep: Callable[[float], None] = time.sleep) -> dict:
    """Poll the daemon until run `run_id` finishes; call `on_progress` with each progress snapshot."""
    while True:
        state = current()
        if state["running"] and state["progress"]:
            on_progress(state["progress"])
        else:
            run = state["latest"]
            if run is None or run["id"] != run_id:
                history_rows = _call("GET", "/admin/bench/history")
                run = next((r for r in history_rows if r["id"] == run_id), None)
            if run is None:
                raise BenchError("the benchmark stopped before it finished (did the daemon restart?)")
            if run["status"] == "interrupted":
                raise BenchError("the benchmark was interrupted before it finished")
            return run
        sleep(interval)


def _num(v, digits: int = 0) -> str:
    return "-" if v is None else f"{v:,.{digits}f}"


def results_text(run: dict) -> str:
    """The results table, sorted by decode speed (`latest`/`history` already sort it)."""
    lines = [f"{'#':<3}{'MODEL':<26} {'DECODE tok/s':>13} {'PREFILL tok/s':>14} {'TTFT p50':>9} {'TTFT p95':>9} {'LOAD':>7}  RUNS"]
    for r in run["results"]:
        if r["status"] == "failed":
            lines.append(f"{'-':<3}{r['model']:<26} failed: {r['error']}")
            continue
        load = "warm" if r["load_seconds"] is None else f"{r['load_seconds']:.1f}s"
        tail = f"{r['runs_ok']}/{r['runs_ok'] + r['errors']}" + (f" ({r['errors']} failed)" if r["errors"] else "")
        lines.append(f"{r.get('place', '-'):<3}{r['model']:<26} {_num(r['decode_tps'], 1):>13} {_num(r['prefill_tps']):>14} "
                     f"{_num(r['ttft_p50_ms']):>6} ms {_num(r['ttft_p95_ms']):>6} ms {load:>7}  {tail}")
    est = " · tokens estimated from streamed chunks" if any(r.get("estimated") for r in run["results"]) else ""
    lines += ["", f"workload  ~{run['prompt_tokens']} tokens in, up to {run['max_tokens']} out, batch 1, "
                  f"{run['runs']} runs after a warm-up{est}"]
    return "\n".join(lines)


def progress_line(model: dict, runs: int) -> str:
    state = model["state"]
    detail = {"queued": "waiting", "loading": "loading (the first load compiles the model)…", "warming": "warm-up run…",
              "running": f"run {model['done']}/{runs}",
              "done": f"done, {_num(model.get('decode_tps'), 1)} tok/s decode",
              "failed": f"failed: {model.get('error')}"}[state]
    return f"{model['name']:<26} {detail}"
