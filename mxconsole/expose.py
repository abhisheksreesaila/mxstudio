"""`mxc expose`: put the daemon on the LAN behind Caddy (HTTPS + key check).

Request path: client → Caddy (tls internal, forward_auth → daemon /auth/check) → daemon
→ max serve. The daemon checks the app key again and routes by `model`. Caddy forwards
only the OpenAI routes, so an app key never reaches the daemon's loopback-only pages.
(Until F-063 LiteLLM sat between Caddy and the daemon; `stop()` still cleans up after it.)
"""
from __future__ import annotations

import os
import re
import shlex
import signal
import socket
import subprocess
import time
from pathlib import Path

from mxconsole import keys
from mxconsole.settings import env, DAEMON_HOST, DAEMON_PORT, EXPOSE_PORT
from mxconsole.store import Store, mxc_home

PROCESSES = ("caddy",)
# Left by installs from before F-063: a LiteLLM process (litellm.pid) and its internal app key.
LEGACY_PROCESSES = ("litellm",)
LEGACY_KEYS = ("_litellm",)
API_PATHS = ("/v1/models", "/v1/chat/completions", "/v1/completions", "/v1/embeddings")
HOSTNAME = re.compile(r"[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?|\[[0-9A-Fa-f:]+\]")
# orig_uri.path is decoded, so '%' is checked on the raw request URI instead.
UNCLEAN = " || ".join([*(f'{{http.request.orig_uri.path}}.contains("{s}")' for s in ("/.", "//", "\\\\")),
                       '{http.request.orig_uri}.contains("%")'])
SETTLE_SECONDS = float(env("EXPOSE_SETTLE", "1.0"))


class ExposeError(Exception):
    pass


def lan_endpoint() -> str | None:
    """https endpoint on the LAN when `mxc expose` is running, else None."""
    if not status()["caddy"]:
        return None
    try:
        return f"https://{(expose_dir() / 'host').read_text().strip()}:{EXPOSE_PORT}/v1"
    except OSError:
        return None


def default_host() -> str:
    return f"{socket.gethostname()}.local"


def ca_root_path() -> Path:
    data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return data / "caddy" / "pki" / "authorities" / "local" / "root.crt"


def render_caddyfile(*, hosts: list[str], port: int = EXPOSE_PORT) -> str:
    for h in hosts:
        if not HOSTNAME.fullmatch(h):
            raise ValueError(f"{h!r} is not a hostname or IP address")
    sites = ", ".join(f"https://{h}:{port}" for h in hosts)
    return f"""{{
	admin 127.0.0.1:2099
	skip_install_trust
	auto_https disable_redirects
}}

{sites} {{
	tls internal
	# reverse_proxy forwards the raw path while `path` matches the cleaned one, so refuse any
	# path that cleaning would change: /anthropic/../v1/models must not reach the daemon.
	@unclean expression `{UNCLEAN}`
	handle @unclean {{
		respond 400
	}}
	@api path {" ".join(API_PATHS)}
	handle @api {{
		forward_auth {DAEMON_HOST}:{DAEMON_PORT} {{
			uri /auth/check
			copy_headers X-Mst-App
		}}
		reverse_proxy {DAEMON_HOST}:{DAEMON_PORT}
	}}
	handle {{
		respond 404
	}}
}}
"""


def expose_dir() -> Path:
    path = mxc_home() / "expose"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_configs(*, host: str) -> dict[str, Path]:
    caddyfile = expose_dir() / "Caddyfile"
    caddyfile.write_text(render_caddyfile(hosts=list(dict.fromkeys([host, "localhost"]))))
    return {"caddyfile": caddyfile}


def _command(name: str, configs: dict[str, Path]) -> list[str]:
    override = env(f"{name.upper()}_CMD")
    if override:
        return shlex.split(override)
    return ["caddy", "run", "--adapter", "caddyfile", "--config", str(configs["caddyfile"])]


def _pid_file(name: str) -> Path:
    return expose_dir() / f"{name}.pid"


def _start_time(pid: int) -> str | None:
    """Kernel start time of a live (non-zombie) process; tells our process from a reused pid."""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    except (FileNotFoundError, IndexError):
        return None
    return None if fields[0] == "Z" else fields[19]


def _running_pid(name: str) -> int | None:
    try:
        pid_text, started = _pid_file(name).read_text().split()
        pid = int(pid_text)
    except (FileNotFoundError, ValueError):
        return None
    return pid if _start_time(pid) == started else None


def status() -> dict[str, bool]:
    return {name: _running_pid(name) is not None for name in PROCESSES}


def start(*, host: str, store: Store | None = None) -> dict[str, Path]:
    """Write the Caddyfile and (re)start Caddy as a user process."""
    store = store or Store.open()
    stop(store=store)
    configs = write_configs(host=host)
    (expose_dir() / "host").write_text(host)
    # Only what Caddy needs: no key in the shell (OPENAI_API_KEY…) belongs in a LAN-facing process.
    env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "TMPDIR")
           if k in os.environ}
    started = []
    for name in PROCESSES:
        log_path = expose_dir() / f"{name}.log"
        with open(log_path, "ab") as log:
            try:
                proc = subprocess.Popen(_command(name, configs), env=env, stdout=log, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL, start_new_session=True)
            except FileNotFoundError as e:
                stop(store=store)
                raise ExposeError(f"{name} not found ({e.filename}). Install it, then run mxc expose again.")
        _pid_file(name).write_text(f"{proc.pid} {_start_time(proc.pid)}")
        started.append((name, proc, log_path))
    deadline = time.monotonic() + SETTLE_SECONDS
    while time.monotonic() < deadline:
        for name, proc, log_path in started:
            if proc.poll() is not None:
                stop(store=store)
                tail = log_path.read_text(errors="replace")[-600:].strip()
                raise ExposeError(f"{name} exited with code {proc.returncode}. Last log lines ({log_path}):\n{tail}")
        time.sleep(0.05)
    return configs


def stop(timeout: float = 10.0, store: Store | None = None) -> None:
    """Stop Caddy (and a LiteLLM left running by an install from before F-063, revoking its key)."""
    for name in PROCESSES + LEGACY_PROCESSES:
        pid = _running_pid(name)
        _pid_file(name).unlink(missing_ok=True)
        if pid is None:
            continue
        try:
            os.killpg(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            continue
        deadline = time.monotonic() + timeout
        while _start_time(pid) is not None and time.monotonic() < deadline:
            _reap(pid)
            time.sleep(0.05)
        if _start_time(pid) is not None:
            os.killpg(pid, signal.SIGKILL)
        _reap(pid)
    store = store or Store.open()
    for app in LEGACY_KEYS:
        keys.revoke_key(store, app)


def _reap(pid: int) -> None:
    """Collect the exit status when the process is our own child (tests, same-process restarts)."""
    try:
        os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        pass
