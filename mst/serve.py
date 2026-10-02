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


class DaemonError(Exception):
    pass


def daemon_running() -> bool:
    try:
        return httpx.get(f"{daemon_url()}/health", timeout=1.0).status_code == 200
    except httpx.HTTPError:
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
    tail = (logs / "daemon.log").read_text(errors="replace")[-600:]
    raise DaemonError(f"the mst daemon did not start. Last log lines ({logs / 'daemon.log'}):\n{tail}")


def load(name: str) -> None:
    r = httpx.post(f"{daemon_url()}/admin/models/{name}/load", timeout=None)
    if r.status_code != 200:
        raise DaemonError(r.json().get("error", {}).get("message", r.text))


def stop(name: str) -> None:
    httpx.post(f"{daemon_url()}/admin/models/{name}/stop", timeout=60.0).raise_for_status()


def shutdown(timeout: float = 30.0) -> bool:
    """Stop every model and the daemon. False if it wasn't running."""
    if not daemon_running():
        return False
    httpx.post(f"{daemon_url()}/admin/shutdown", timeout=timeout)
    deadline = time.monotonic() + timeout
    while daemon_running() and time.monotonic() < deadline:
        time.sleep(0.1)
    return True


def live_states() -> dict[str, str]:
    """name → state from the running daemon, or {} when it isn't running."""
    try:
        return {m["name"]: m["state"] for m in httpx.get(f"{daemon_url()}/admin/status", timeout=1.0).json()}
    except (httpx.HTTPError, ValueError):
        return {}


def first_key(store: Store) -> str | None:
    """Make a key for the 'default' app on first use; None when the user already has one."""
    active = [k for k in keys.list_keys(store) if k.revoked_at is None and not k.app.startswith("_")]
    if active:
        return None
    return keys.create_key(store, DEFAULT_APP)


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
