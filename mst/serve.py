"""Client side of the daemon: start it in the background, load and stop models, show how to call them."""
from __future__ import annotations

import os
import subprocess
import sys
import time

import httpx

from mst import keys
from mst.settings import daemon_url
from mst.store import Store, mst_home

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
    """Start `mst daemon` in the background unless it already answers. True if it was started now."""
    if daemon_running():
        return False
    logs = mst_home() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    with open(logs / "daemon.log", "ab") as log:
        proc = subprocess.Popen([sys.executable, "-m", "mst", "daemon"], stdout=log, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True, env=os.environ.copy())
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if daemon_running():
            return True
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    if daemon_running():  # lost a start race to another `mst serve`; its daemon is fine
        return False
    tail = (logs / "daemon.log").read_text(errors="replace")[-600:]
    raise DaemonError(f"the mst daemon did not start. Last log lines ({logs / 'daemon.log'}):\n{tail}")


def _admin(path: str, timeout: float | None) -> httpx.Response:
    try:
        r = httpx.post(f"{daemon_url()}/admin/{path}", headers=ADMIN, timeout=timeout)
    except httpx.HTTPError as e:
        raise DaemonError(f"could not reach the mst daemon at {daemon_url()}: {e}")
    if r.status_code != 200:
        try:
            message = r.json()["error"]["message"]
        except (ValueError, KeyError, TypeError):
            message = r.text or f"HTTP {r.status_code}"
        raise DaemonError(message)
    return r


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


def first_key(store: Store) -> str | None:
    """Make a key for the 'default' app on first use; None when the user already has one."""
    active = [k for k in keys.list_keys(store) if k.revoked_at is None and not k.app.startswith("_")]
    if active:
        return None
    try:
        return keys.create_key(store, DEFAULT_APP)
    except keys.KeyExists:  # another `mst serve` made it a moment ago and printed it
        return None


def usage(name: str, key: str | None) -> str:
    base = f"{daemon_url()}/v1"
    shown = key or "$MST_API_KEY"
    lines = [f"endpoint  {base}   (API reference: {daemon_url()}/docs)"]
    if key:
        lines.append(f"key       {key}   (app '{DEFAULT_APP}'; shown once, keep it)")
    else:
        lines.append("key       use your app's key (mst keys ls), or make one: mst keys new <app>")
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
