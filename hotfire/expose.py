"""`mst expose`: put the daemon on the LAN behind Caddy (HTTPS + key check) and LiteLLM.

Request path: client → Caddy (tls internal, forward_auth → daemon /auth/check)
→ LiteLLM (routing, master key) → daemon → max serve. With --no-litellm, Caddy
proxies straight to the daemon, which checks the app key again. Caddy forwards only
the OpenAI routes, so an app key never reaches LiteLLM's admin API or the daemon's
loopback-only pages.
"""
from __future__ import annotations

import os
import re
import secrets
import shlex
import signal
import socket
import subprocess
import time
from pathlib import Path

import yaml

from hotfire import keys
from hotfire.settings import env, DAEMON_HOST, DAEMON_PORT, EXPOSE_PORT, LITELLM_PORT, daemon_url
from hotfire.store import Model, Store, hotfire_home

PROCESSES = ("caddy", "litellm")
UPSTREAM_APP = "_litellm"  # the reserved key LiteLLM uses to call the daemon
API_PATHS = ("/v1/models", "/v1/chat/completions", "/v1/completions", "/v1/embeddings")
HOSTNAME = re.compile(r"[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?|\[[0-9A-Fa-f:]+\]")
# orig_uri.path is decoded, so '%' is checked on the raw request URI instead.
UNCLEAN = " || ".join([*(f'{{http.request.orig_uri.path}}.contains("{s}")' for s in ("/.", "//", "\\\\")),
                       '{http.request.orig_uri}.contains("%")'])
SETTLE_SECONDS = float(env("EXPOSE_SETTLE", "1.0"))
_upstream_key_for_tests: str | None = None


class ExposeError(Exception):
    pass


def lan_endpoint() -> str | None:
    """https endpoint on the LAN when `mst expose` is running, else None."""
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


def render_caddyfile(*, hosts: list[str], port: int = EXPOSE_PORT, litellm: bool = True) -> str:
    for h in hosts:
        if not HOSTNAME.fullmatch(h):
            raise ValueError(f"{h!r} is not a hostname or IP address")
    sites = ", ".join(f"https://{h}:{port}" for h in hosts)
    if litellm:
        upstream = f"""		reverse_proxy 127.0.0.1:{LITELLM_PORT} {{
			header_up Authorization "Bearer {{env.LITELLM_MASTER_KEY}}"
		}}"""
    else:
        upstream = f"		reverse_proxy {DAEMON_HOST}:{DAEMON_PORT}"
    return f"""{{
	admin 127.0.0.1:2099
	skip_install_trust
	auto_https disable_redirects
}}

{sites} {{
	tls internal
	# reverse_proxy forwards the raw path while `path` matches the cleaned one, so refuse any
	# path that cleaning would change: /anthropic/../v1/models must not reach LiteLLM.
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
{upstream}
	}}
	handle {{
		respond 404
	}}
}}
"""


def render_litellm_config(models: list[Model]) -> str:
    def route(name: str) -> dict:
        return {"model_name": name,
                "litellm_params": {"model": f"openai/{name}", "api_base": f"{daemon_url()}/v1",
                                   "api_key": "os.environ/HOTFIRE_UPSTREAM_KEY"}}
    config = {
        # No wildcard route: LiteLLM would expand it into every OpenAI model it knows.
        "model_list": [route(m.name) for m in models],
        "general_settings": {"master_key": "os.environ/LITELLM_MASTER_KEY"},
        "litellm_settings": {"drop_params": True},
    }
    return yaml.safe_dump(config, sort_keys=False)


def refresh_if_running(store: Store | None = None) -> bool:
    """Restart LiteLLM with the current model list, if `mst expose` is running with it."""
    if not status()["litellm"]:
        return False
    host = (expose_dir() / "host").read_text().strip()
    start(host=host, litellm=True, store=store)
    return True


def expose_dir() -> Path:
    path = hotfire_home() / "expose"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_configs(*, host: str, litellm: bool, store: Store | None = None) -> dict[str, Path]:
    store = store or Store.open()
    d = expose_dir()
    caddyfile, litellm_yaml = d / "Caddyfile", d / "litellm.yaml"
    caddyfile.write_text(render_caddyfile(hosts=list(dict.fromkeys([host, "localhost"])), litellm=litellm))
    litellm_yaml.write_text(render_litellm_config(store.models()))
    return {"caddyfile": caddyfile, "litellm": litellm_yaml}


def _master_key() -> str:
    path = expose_dir() / "litellm_master_key"
    try:
        key = path.read_text().strip()
    except FileNotFoundError:
        key = ""
    if len(key) >= 32:
        path.chmod(0o600)
        return key
    # Never fall back to an empty key: LiteLLM would run without auth.
    key = "sk-" + secrets.token_urlsafe(32)
    tmp = path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key)
    os.replace(tmp, path)
    return key


def _command(name: str, configs: dict[str, Path]) -> list[str]:
    override = env(f"{name.upper()}_CMD")
    if override:
        return shlex.split(override)
    if name == "caddy":
        return ["caddy", "run", "--adapter", "caddyfile", "--config", str(configs["caddyfile"])]
    pixi_bin = Path(__file__).resolve().parents[1] / ".pixi" / "envs" / "gateway" / "bin" / "litellm"
    exe = str(pixi_bin) if pixi_bin.exists() else "litellm"
    return [exe, "--config", str(configs["litellm"]), "--host", "127.0.0.1", "--port", str(LITELLM_PORT)]


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


def start(*, host: str, litellm: bool = True, store: Store | None = None) -> dict[str, Path]:
    """Write configs and (re)start Caddy and, unless disabled, LiteLLM as user processes."""
    global _upstream_key_for_tests
    store = store or Store.open()
    stop(store=store)
    configs = write_configs(host=host, litellm=litellm, store=store)
    (expose_dir() / "host").write_text(host)
    # Only what the children need: a provider key in the shell (OPENAI_API_KEY…) must never
    # become spendable through LiteLLM by any app key.
    env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "TMPDIR")
           if k in os.environ}
    env["LITELLM_MASTER_KEY"] = _master_key()
    litellm_env = dict(env)
    if litellm:
        if not _port_free(LITELLM_PORT):
            raise ExposeError(f"port {LITELLM_PORT} is already in use, so LiteLLM can't start there "
                              "(set HOTFIRE_LITELLM_PORT to another port)")
        litellm_env["HOTFIRE_UPSTREAM_KEY"] = keys.create_key(store, UPSTREAM_APP, rotate=True, internal=True)
        _upstream_key_for_tests = litellm_env["HOTFIRE_UPSTREAM_KEY"]
    started = []
    for name in PROCESSES:
        if name == "litellm" and not litellm:
            continue
        log_path = expose_dir() / f"{name}.log"
        with open(log_path, "ab") as log:
            try:
                proc = subprocess.Popen(_command(name, configs), env=litellm_env if name == "litellm" else env, stdout=log, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL, start_new_session=True)
            except FileNotFoundError as e:
                stop(store=store)
                raise ExposeError(f"{name} not found ({e.filename}). Install it, then run mst expose again.")
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
    if litellm:
        _wait_for_port(LITELLM_PORT, next(p for n, p, _ in started if n == "litellm"),
                       expose_dir() / "litellm.log", store)
    return configs


def _port_free(port: int) -> bool:
    """Only a live listener makes a port busy; TIME_WAIT leftovers don't stop LiteLLM (uvicorn reuses them)."""
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            s.listen()
        except OSError:
            return False
    return True


def _wait_for_port(port: int, proc: subprocess.Popen, log_path: Path, store: Store) -> None:
    """LiteLLM takes seconds to import; report a later failure (e.g. port taken) instead of claiming success."""
    deadline = time.monotonic() + float(env("LITELLM_BOOT_SECONDS", "60"))
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            stop(store=store)
            tail = log_path.read_text(errors="replace")[-600:].strip()
            raise ExposeError(f"litellm exited with code {proc.returncode}. Last log lines ({log_path}):\n{tail}")
        with socket.socket() as s:
            s.settimeout(0.2)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.2)
    stop(store=store)
    raise ExposeError(f"litellm did not open port {port}. See {log_path}")


def stop(timeout: float = 10.0, store: Store | None = None) -> None:
    for name in PROCESSES:
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
    keys.revoke_key(store or Store.open(), UPSTREAM_APP)


def _reap(pid: int) -> None:
    """Collect the exit status when the process is our own child (tests, same-process restarts)."""
    try:
        os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        pass
