"""Client side of the daemon: start it in the background, load and stop models, show how to call them."""
from __future__ import annotations

import os
import subprocess
import sys
import time

import httpx

from mxconsole import keys
from mxconsole.settings import daemon_url
from mxconsole.store import Store, mxc_home

DEFAULT_APP = "default"
ADMIN = {"X-Mst-Admin": "1"}


class DaemonError(Exception):
    pass


def daemon_running() -> bool:
    try:
        r = httpx.get(f"{daemon_url()}/health", timeout=1.0)
        body = r.json()
        return r.status_code == 200 and isinstance(body, dict) and body.get("service") == "mst"
    except (httpx.HTTPError, ValueError):
        return False


def ensure_daemon(timeout: float = 30.0) -> bool:
    """Start `mxc daemon` in the background unless it already answers. True if it was started now."""
    if daemon_running():
        return False
    logs = mxc_home() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    with open(logs / "daemon.log", "ab") as log:
        proc = subprocess.Popen([sys.executable, "-m", "mxconsole", "daemon"], stdout=log, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True, env=os.environ.copy())
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if daemon_running():
            return True
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    if daemon_running():  # lost a start race to another `mxc serve`; its daemon is fine
        return False
    tail = (logs / "daemon.log").read_text(errors="replace")[-600:]
    raise DaemonError(f"the omxconsole daemon did not start. Last log lines ({logs / 'daemon.log'}):\n{tail}")


def _admin(path: str, timeout: float | None) -> httpx.Response:
    try:
        r = httpx.post(f"{daemon_url()}/admin/{path}", headers=ADMIN, timeout=timeout)
    except httpx.HTTPError as e:
        raise DaemonError(f"could not reach the omxconsole daemon at {daemon_url()}: {e}")
    if r.status_code != 200:
        try:
            message = r.json()["error"]["message"]
        except (ValueError, KeyError, TypeError):
            message = r.text or f"HTTP {r.status_code}"
        raise DaemonError(message)
    return r


def admin_call(method: str, path: str, *, timeout: float | None = 30.0, **kw) -> tuple[int, dict | list | str]:
    """One admin request to the running daemon: (status, parsed JSON or text). DaemonError if it can't be reached."""
    try:
        r = httpx.request(method, f"{daemon_url()}/admin/{path}", headers=ADMIN, timeout=timeout, **kw)
    except httpx.HTTPError as e:
        raise DaemonError(f"could not reach the omxconsole daemon at {daemon_url()}: {e}")
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, r.text


def error_message(status: int, body) -> str:
    """The one line to show for a failed admin call."""
    if isinstance(body, dict) and isinstance(body.get("error"), dict) and body["error"].get("message"):
        return body["error"]["message"]
    return body if isinstance(body, str) and body else f"the daemon answered HTTP {status}"


def load(name: str) -> None:
    _admin(f"models/{name}/load", timeout=None)


def stop(name: str) -> None:
    _admin(f"models/{name}/stop", timeout=None)


def shutdown(timeout: float = 120.0) -> bool:
    """Stop every model and the daemon. False if it wasn't running."""
    if not daemon_running():
        return False
    _admin("shutdown", timeout=None)  # stop_all can take ~10s per model
    deadline = time.monotonic() + timeout
    while daemon_running() and time.monotonic() < deadline:
        time.sleep(0.1)
    return True


def live_states() -> dict[str, str]:
    """name → state from the running daemon, or {} when it isn't running."""
    try:
        r = httpx.get(f"{daemon_url()}/admin/status", headers=ADMIN, timeout=1.0)
        return {m["name"]: m["state"] for m in r.json()}
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        return {}


def daemon_info() -> dict | None:
    """The daemon's /admin/info (uptime, endpoints, MAX version), or None when it isn't running."""
    try:
        r = httpx.get(f"{daemon_url()}/admin/info", headers=ADMIN, timeout=1.0)
        body = r.json()
        return body if r.status_code == 200 and isinstance(body, dict) else None
    except (httpx.HTTPError, ValueError):
        return None


def first_key(store: Store) -> str | None:
    """Make a key for the 'default' app on first use; None when the user already has one."""
    active = [k for k in keys.list_keys(store) if k.revoked_at is None and not k.app.startswith("_")]
    if active:
        return None
    try:
        return keys.create_key(store, DEFAULT_APP)
    except keys.KeyExists:  # another `mxc serve` made it a moment ago and printed it
        return None


def usage(name: str, key: str | None) -> str:
    base = f"{daemon_url()}/v1"
    shown = key or "$MXC_API_KEY"
    lines = [f"endpoint  {base}   (API reference: {daemon_url()}/docs)"]
    if key:
        lines.append(f"key       {key}   (app '{DEFAULT_APP}'; shown once, keep it)")
    else:
        lines.append("key       use your app's key (mxc keys ls), or make one: mxc keys new <app>")
    lines += [
        "",
        f"curl {base}/chat/completions -H 'Authorization: Bearer {shown}' \\",
        f"  -H 'Content-Type: application/json' -d '{{\"model\": \"{name}\", \"messages\": [{{\"role\": \"user\", \"content\": \"Hello\"}}]}}'",
        "",
        "from openai import OpenAI",
        f"client = OpenAI(base_url=\"{base}\", api_key=\"{shown}\")",
        f"print(client.chat.completions.create(model=\"{name}\", messages=[{{\"role\": \"user\", \"content\": \"Hello\"}}]).choices[0].message.content)",
    ]
    return "\n".join(lines)
