"""What you should care about: plain rules over the requests table. No model, nothing guessed."""
from __future__ import annotations

import time

from mxconsole import metrics
from mxconsole.store import Store

HOUR = 3600
DAY = 86400
TTFT_RISE = 0.30  # p50 first-token time up by more than this against the hour before
ERROR_RATE = 0.02
MIN_SAMPLES = 5  # a p50 of fewer streamed replies than this isn't a trend
MIN_REQUESTS = 20  # nor is an error rate over fewer requests


def compute(store: Store, models: list[dict], now: float | None = None) -> list[dict]:
    """`models` are rows with name, state, pinned and (optionally) idle_seconds. Returns only what applies."""
    now = time.time() if now is None else now
    rows = store.db.execute("SELECT * FROM requests WHERE ts >= ?", (now - 2 * HOUR,)).fetchall()
    out: list[dict] = []
    out += _idle(store, models, now)
    out += _ttft(rows, now)
    out += _no_key(store, now)
    out += _errors(rows, now)
    return out


def _idle(store: Store, models: list[dict], now: float) -> list[dict]:
    out = []
    for m in models:
        if m["state"] != "ready" or m.get("in_flight"):
            continue  # not in memory costs nothing; starting or serving a request is not idle
        idle = m.get("idle_seconds")
        if idle is not None and idle < DAY:
            continue  # loaded, but only recently: the table may not know about it yet
        last = store.db.execute("SELECT MAX(ts) AS ts FROM requests WHERE model = ?", (m["name"],)).fetchone()["ts"]
        if last is not None and now - last < DAY:
            continue
        what = "It is pinned, so it stays in memory." if m.get("pinned") else "It is still loaded."
        out.append({"id": f"idle:{m['name']}", "severity": "warn", "icon": "timer",
                    "title": f"{m['name']} has been idle for a day",
                    "detail": f"No requests in 24 h. {what} Unload it to free memory?",
                    "cli": f"mxc stop {m['name']}", "action": {"label": "Unload", "stop": m["name"]}})
    return out


def _ttft(rows, now: float) -> list[dict]:
    out = []
    names = sorted({r["model"] for r in rows if r["model"]})
    for name in names:
        ttfts = [(r["ts"], r["ttft_ms"]) for r in rows if r["model"] == name and r["status"] < 400 and r["ttft_ms"] is not None]
        recent = [t for ts, t in ttfts if ts >= now - HOUR]
        before = [t for ts, t in ttfts if ts < now - HOUR]
        if len(recent) < MIN_SAMPLES or len(before) < MIN_SAMPLES:
            continue
        p_now, p_before = metrics._percentile(recent, 0.5), metrics._percentile(before, 0.5)
        if p_before and p_now > p_before * (1 + TTFT_RISE):
            out.append({"id": f"ttft:{name}", "severity": "bad", "icon": "triangle-alert",
                        "title": f"{name} got slower to start",
                        "detail": f"First token went from {p_before:.0f} ms to {p_now:.0f} ms (p50, last hour against the hour before). "
                                  "Longer prompts or a busier machine are the usual causes.",
                        "cli": f"mxc metrics --range 1h"})
    return out


def _no_key(store: Store, now: float) -> list[dict]:
    n = store.db.execute("SELECT COUNT(*) AS n FROM requests WHERE status = 401 AND app IS NULL AND ts >= ?",
                         (now - DAY,)).fetchone()["n"]
    if not n:
        return []
    return [{"id": "nokey", "severity": "warn", "icon": "shield-check",
             "title": f"{n} request{'s' if n != 1 else ''} without a valid key in 24 h",
             "detail": "Refused with 401: no key, or a revoked or mistyped one. If an app of yours is behind this, give it a key.",
             "cli": "mxc keys new <app>"}]


def _errors(rows, now: float) -> list[dict]:
    recent = [r for r in rows if r["ts"] >= now - HOUR and r["status"] != 401]  # 401s have their own rule
    if len(recent) < MIN_REQUESTS:
        return []
    failed = sum(1 for r in recent if r["status"] >= 400)
    rate = failed / len(recent)
    if rate <= ERROR_RATE:
        return []
    return [{"id": "errors", "severity": "bad", "icon": "triangle-alert",
             "title": f"{rate * 100:.1f}% of requests failed in the last hour",
             "detail": f"{failed} of {len(recent)} returned an error (not counting 401s). A model may be failing to start: read its log.",
             "cli": "mxc requests --limit 50"}]
