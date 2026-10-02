"""`mst expose`: put the daemon on the LAN behind Caddy (HTTPS + key check) and LiteLLM.

Request path: client → Caddy (tls internal, forward_auth → daemon /auth/check)
→ LiteLLM (routing, master key) → daemon → max serve. With --no-litellm, Caddy
proxies straight to the daemon, which checks the app key again.
"""
from __future__ import annotations

import os
import secrets
import shlex
import signal
import socket
import subprocess
import time
from pathlib import Path

import yaml

from mst import keys
from mst.settings import DAEMON_HOST, DAEMON_PORT, EXPOSE_PORT, LITELLM_PORT, daemon_url
from mst.store import Model, Store, mst_home

PROCESSES = ("caddy", "litellm")
UPSTREAM_APP = "litellm"  # the key LiteLLM uses to call the daemon


def default_host() -> str:
    return f"{socket.gethostname()}.local"


def ca_root_path() -> Path:
    data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return data / "caddy" / "pki" / "authorities" / "local" / "root.crt"


def render_caddyfile(*, hosts: list[str], port: int = EXPOSE_PORT, litellm: bool = True) -> str:
    sites = ", ".join(f"https://{h}:{port}" for h in hosts)
    if litellm:
        upstream = f"""	reverse_proxy 127.0.0.1:{LITELLM_PORT} {{
		header_up Authorization "Bearer {{env.LITELLM_MASTER_KEY}}"
	}}"""
    else:
        upstream = f"	reverse_proxy {DAEMON_HOST}:{DAEMON_PORT}"
    return f"""{{
	admin 127.0.0.1:2099
	skip_install_trust
	auto_https disable_redirects
}}

{sites} {{
	tls internal
	forward_auth {DAEMON_HOST}:{DAEMON_PORT} {{
		uri /auth/check
		copy_headers X-Mst-App
	}}
{upstream}
}}
"""


def render_litellm_config(models: list[Model]) -> str:
    def route(name: str) -> dict:
        return {"model_name": name,
                "litellm_params": {"model": f"openai/{name}", "api_base": f"{daemon_url()}/v1",
                                   "api_key": "os.environ/MST_UPSTREAM_KEY"}}
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
    path = mst_home() / "expose"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_configs(*, host: str, litellm: bool, store: Store | None = None) -> dict[str, Path]:
    store = store or Store.open()
    d = expose_dir()
    caddyfile, litellm_yaml = d / "Caddyfile", d / "litellm.yaml"
    caddyfile.write_text(render_caddyfile(hosts=[host, "localhost"], litellm=litellm))
    litellm_yaml.write_text(render_litellm_config(store.models()))
    return {"caddyfile": caddyfile, "litellm": litellm_yaml}


def _master_key() -> str:
    path = expose_dir() / "litellm_master_key"
    if not path.exists():
        path.touch(mode=0o600)
        path.write_text("sk-" + secrets.token_urlsafe(32))
    return path.read_text().strip()


def _command(name: str, configs: dict[str, Path]) -> list[str]:
    override = os.environ.get(f"MST_{name.upper()}_CMD")
    if override:
        return shlex.split(override)
    if name == "caddy":
        return ["caddy", "run", "--adapter", "caddyfile", "--config", str(configs["caddyfile"])]
    pixi_bin = Path(__file__).resolve().parents[1] / ".pixi" / "envs" / "gateway" / "bin" / "litellm"
    exe = str(pixi_bin) if pixi_bin.exists() else "litellm"
    return [exe, "--config", str(configs["litellm"]), "--host", "127.0.0.1", "--port", str(LITELLM_PORT)]


def _pid_file(name: str) -> Path:
    return expose_dir() / f"{name}.pid"


def _alive(pid: int) -> bool:
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, IndexError):
        return False
    return state != "Z"


def _running_pid(name: str) -> int | None:
    try:
        pid = int(_pid_file(name).read_text())
    except (FileNotFoundError, ValueError):
        return None
    return pid if _alive(pid) else None


def status() -> dict[str, bool]:
    return {name: _running_pid(name) is not None for name in PROCESSES}


def start(*, host: str, litellm: bool = True, store: Store | None = None) -> dict[str, Path]:
    """Write configs and (re)start Caddy and, unless disabled, LiteLLM as user processes."""
    store = store or Store.open()
    stop()
    configs = write_configs(host=host, litellm=litellm, store=store)
    (expose_dir() / "host").write_text(host)
    env = {**os.environ, "LITELLM_MASTER_KEY": _master_key()}
    if litellm:
        env["MST_UPSTREAM_KEY"] = keys.create_key(store, UPSTREAM_APP, rotate=True)
    for name in PROCESSES:
        if name == "litellm" and not litellm:
            continue
        log = open(expose_dir() / f"{name}.log", "ab")
        proc = subprocess.Popen(_command(name, configs), env=env, stdout=log, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)
        log.close()
        _pid_file(name).write_text(str(proc.pid))
    return configs


def stop(timeout: float = 10.0) -> None:
    for name in PROCESSES:
        pid = _running_pid(name)
        _pid_file(name).unlink(missing_ok=True)
        if pid is None:
            continue
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        deadline = time.monotonic() + timeout
        while _alive(pid) and time.monotonic() < deadline:
            _reap(pid)
            time.sleep(0.05)
        if _alive(pid):
            os.killpg(pid, signal.SIGKILL)
        _reap(pid)


def _reap(pid: int) -> None:
    """Collect the exit status when the process is our own child (tests, same-process restarts)."""
    try:
        os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        pass
