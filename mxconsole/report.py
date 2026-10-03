"""Formatting and data helpers for the read commands (`mxc status`, `metrics`, `requests`, `--json`)."""
from __future__ import annotations

import json
import time

from mxconsole.settings import daemon_url
from mxconsole.store import Model, format_duration

PAGES = ("overview", "models", "activity", "metrics", "keys", "network", "api", "ports", "logs", "gateway", "daemon", "benchmarks", "pull")


def console_link(page: str | None = None) -> str:
    return f"{daemon_url()}/console" + (f"#{page}" if page else "")


def lifecycle(m: Model) -> str:
    return "pinned (never unloads)" if m.pinned else f"keep-alive {format_duration(m.keep_alive)}"


def dump(data) -> str:
    return json.dumps(data, indent=2)


def when(t: float | None) -> str:
    return "never" if t is None else time.strftime("%Y-%m-%d %H:%M", time.localtime(t))


def lan_endpoint() -> str | None:
    """https endpoint on the LAN when `mxc expose` is running, else None."""
    from mxconsole import expose
    return expose.lan_endpoint()


def format_uptime(seconds: int) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m" if h else f"{m}m {s}s" if m else f"{s}s"


def status_data(store, info: dict | None, states: dict[str, str]) -> dict:
    """Everything `mxc status` shows. Models are 'cold' when the daemon isn't running."""
    from mxconsole import expose, keys
    from mxconsole.port import mst_max_version
    models = store.models()
    counts: dict[str, int] = {}
    for m in models:
        state = states.get(m.name, "cold") if info else "cold"
        counts[state] = counts.get(state, 0) + 1
    active = sum(1 for k in keys.list_keys(store) if k.revoked_at is None and not k.app.startswith("_"))
    lan = lan_endpoint()
    return {"daemon": {"running": info is not None, "uptime_seconds": info["uptime_seconds"] if info else None},
            "endpoint": f"{daemon_url()}/v1", "console": console_link(),
            "lan": {"exposed": lan is not None, "endpoint": lan, "processes": expose.status()},
            "models": {"total": len(models), "by_state": counts},
            "active_keys": active, "max_version": mst_max_version()}


def status_text(d: dict) -> str:
    daemon = d["daemon"]
    up = (f"running, up {format_uptime(daemon['uptime_seconds'])}" if daemon["running"]
          else "not running (mxc serve <model> starts it)")
    lan = d["lan"]["endpoint"] or "not exposed (mxc expose)"
    by = ", ".join(f"{n} {s}" for s, n in sorted(d["models"]["by_state"].items())) or "none"
    return "\n".join([f"{'daemon':<10}{up}", f"{'endpoint':<10}{d['endpoint']}", f"{'console':<10}{d['console']}",
                      f"{'lan':<10}{lan}", f"{'models':<10}{d['models']['total']} ({by})",
                      f"{'keys':<10}{d['active_keys']} active", f"{'max':<10}{d['max_version'] or 'not installed'}"])


def model_row(m: Model, states: dict[str, str], daemon_running: bool) -> dict:
    """One model as `ls --json` and `set --json` print it. `state` is 'cold' unless the daemon says otherwise."""
    return {"name": m.name, "repo_id": m.repo_id, "revision": m.revision, "source": m.source,
            "architecture": m.architecture, "pinned": m.pinned, "keep_alive": m.keep_alive,
            "state": states.get(m.name, "cold") if daemon_running else "cold", "daemon_running": daemon_running}


def models_data(models: list[Model], states: dict[str, str], daemon_running: bool = False) -> list[dict]:
    return [model_row(m, states, daemon_running) for m in models]


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
