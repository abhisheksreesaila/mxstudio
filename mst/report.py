"""Formatting and data helpers for the read commands (`mst status`, `metrics`, `requests`, `--json`)."""
from __future__ import annotations

import json
import time

from mst.settings import daemon_url
from mst.store import Model, format_duration

PAGES = ("overview", "models", "activity", "metrics", "keys", "network")


def console_link(page: str | None = None) -> str:
    return f"{daemon_url()}/console" + (f"#{page}" if page else "")


def lifecycle(m: Model) -> str:
    return "pinned (never unloads)" if m.pinned else f"keep-alive {format_duration(m.keep_alive)}"


def dump(data) -> str:
    return json.dumps(data, indent=2)


def when(t: float | None) -> str:
    return "never" if t is None else time.strftime("%Y-%m-%d %H:%M", time.localtime(t))


def models_data(models: list[Model], states: dict[str, str]) -> list[dict]:
    """Registered models; `state` is the daemon live state, or null when the daemon is not running."""
    return [{"name": m.name, "repo_id": m.repo_id, "revision": m.revision, "source": m.source,
             "architecture": m.architecture, "pinned": m.pinned, "keep_alive": m.keep_alive,
             "state": states.get(m.name) if states else None} for m in models]


def keys_data(rows) -> list[dict]:
    return [{"app": k.app, "prefix": k.prefix, "created_at": k.created_at, "last_used_at": k.last_used_at,
             "revoked_at": k.revoked_at, "status": "active" if k.revoked_at is None else "revoked"} for k in rows]


RANGES = {"5m": 300, "1h": 3600, "24h": 86400, "7d": 7 * 86400}


def _ms(v) -> str:
    return "-" if v is None else f"{v:.0f} ms"


def metrics_text(s: dict, range_: str, gpus: list[dict]) -> str:
    lines = [f"last {range_}", f"{'requests':<12}{s['requests']}   ({s['per_minute']}/min)",
             f"{'errors':<12}{s['errors']}   ({s['error_rate'] * 100:.1f}%)",
             f"{'TTFT p50':<12}{_ms(s['ttft_p50_ms'])}", f"{'TTFT p95':<12}{_ms(s['ttft_p95_ms'])}",
             f"{'decode p50':<12}" + ("-" if s["decode_tps_p50"] is None else f"{s['decode_tps_p50']:.1f} tok/s")]
    if s["by_model"]:
        lines += ["", f"{'MODEL':<24} {'REQUESTS':>8} {'ERRORS':>6} {'TTFT P50':>9} {'TTFT P95':>9} {'DECODE P50':>11}"]
        for name, m in s["by_model"].items():
            tps = "-" if m["decode_tps_p50"] is None else f"{m['decode_tps_p50']:.1f} tok/s"
            lines.append(f"{name:<24} {m['requests']:>8} {m['errors']:>6} {_ms(m['ttft_p50_ms']):>9} {_ms(m['ttft_p95_ms']):>9} {tps:>11}")
    if gpus:
        lines += ["", f"{'GPU':<4} {'NAME':<28} {'MEMORY':>18} {'UTIL':>5}"]
        for g in gpus:
            lines.append(f"{g['index']:<4} {g['name']:<28} {str(g['used_mb']) + ' / ' + str(g['total_mb']) + ' MB':>18} {g['util']:>4}%")
    return "\n".join(lines)


def requests_text(rows: list[dict]) -> str:
    lines = [f"{'TIME':<9} {'APP':<14} {'MODEL':<22} {'ROUTE':<22} {'STATUS':<6} {'TOKENS IN/OUT':<14} {'TTFT':>8} {'TOTAL':>8}"]
    for r in rows:
        tokens = f"{r['prompt_tokens'] if r['prompt_tokens'] is not None else '-'}/{'~' if r['estimated'] else ''}{r['completion_tokens'] if r['completion_tokens'] is not None else '-'}"
        lines.append(f"{time.strftime('%H:%M:%S', time.localtime(r['ts'])):<9} {r['app'] or '-':<14} {r['model'] or '-':<22} {r['route']:<22} "
                     f"{r['status']:<6} {tokens:<14} {_ms(r['ttft_ms']):>8} {_ms(r['duration_ms']):>8}")
    return "\n".join(lines)
