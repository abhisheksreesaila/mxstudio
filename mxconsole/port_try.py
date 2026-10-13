"""try-port: the porting agent compiles, serves and compares its own package, the way mxconsole's final check will.

Without it the agent only ever ran check_port.py (does it import, does every weight map) and never saw the model
run: compile errors, wrong numbers and broken output only showed up after it had finished, as one summary line
(port study, docs/port-study.md). One fixed command, allowed to the agent and nothing broader:

    python -m mxconsole.port_try --config <job>/work/try/config.json [--wait 540]

starts `max serve` on the package (the same flags as the final check), sends one prompt, runs the skill's
compare_layers probe and the verify gates against it, prints a short report and shuts the server down. A cold
compile can outlast the agent's 10-minute command limit, so the run is a detached worker: a call waits up to
`--wait` seconds, says "still running" if it isn't done, and the next call picks up the same run. One run at a
time, `max_tries` per attempt (the harness resets the state for each attempt). It runs inside the agent's
sandbox, so nothing here widens what the agent can reach; the harness also calls stop() when the agent exits.

The harness writes the config (commands as argv lists; "{port}" is replaced by the server's port). Exit codes:
0 pass, 1 fail, 2 still running, 3 refused (no tries left, bad config).
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

DEFAULT_WAIT = 540.0      # under Claude Code's 10-minute Bash limit
KEEP_LINES = 80           # of each step's output, after noise is dropped
NOISE = re.compile(r"Warning: cache directory .* is not writable|INFO: Still compiling|^\s*$|"
                   r"Ignored error while caching|Could not cache non-existence|Loading weights:|Parse safetensors")


# ---- state ------------------------------------------------------------------------------------

def write_config(state_dir: Path, cfg: dict) -> Path:
    """Write a fresh config and clear the previous attempt's state. Returns the config path."""
    stop(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    for f in ("state.json", "result.txt", "serve.log"):
        (state_dir / f).unlink(missing_ok=True)
    path = state_dir / "config.json"
    path.write_text(json.dumps(cfg, indent=1))
    return path


def read_state(state_dir: Path) -> dict:
    try:
        return json.loads((state_dir / "state.json").read_text())
    except (OSError, ValueError):
        return {"status": "idle", "tries": 0}


def _write_state(state_dir: Path, state: dict) -> None:
    tmp = state_dir / "state.json.tmp"
    tmp.write_text(json.dumps(state))
    tmp.replace(state_dir / "state.json")


@contextlib.contextmanager
def _locked(state_dir: Path):
    with open(state_dir / "lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def package_hash(port_dir: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(p for p in port_dir.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
        h.update(str(f.relative_to(port_dir)).encode() + b"\0" + f.read_bytes() + b"\0")
    return h.hexdigest()[:12]


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _kill_group(pid: int | None) -> None:
    """SIGTERM the process group led by `pid`, then SIGKILL whatever is left."""
    if not pid:
        return
    for sig, grace in ((signal.SIGTERM, 5.0), (signal.SIGKILL, 0)):
        try:
            os.killpg(pid, sig)
        except OSError:
            return
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline and _alive(pid):
            time.sleep(0.1)


def stop(state_dir: Path) -> None:
    """Stop a running try (its worker and server). Safe to call when nothing runs."""
    state = read_state(state_dir)
    _kill_group(state.get("server_pid"))
    _kill_group(state.get("worker_pid"))
    if state.get("status") == "running" and (state_dir / "state.json").exists():
        state.update(status="stopped")
        _write_state(state_dir, state)


# ---- the client the agent calls ----------------------------------------------------------------

def main(argv: list[str] | None = None, out: Callable[[str], None] = print) -> int:
    ap = argparse.ArgumentParser(prog="try-port", description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--wait", type=float, default=DEFAULT_WAIT)
    ap.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    try:
        cfg = json.loads(a.config.read_text())
    except (OSError, ValueError) as e:
        out(f"try-port: cannot read {a.config}: {e}")
        return 3
    state_dir = a.config.parent
    if a.worker:
        return _work(cfg, state_dir)
    with _locked(state_dir):
        state = read_state(state_dir)
        running = state.get("status") == "running" and _alive(state.get("worker_pid"))
        if not running and state.get("status") == "done" and not state.get("delivered"):
            pass  # a finished run nobody has read yet: report it below, don't start another
        elif not running:
            if state.get("tries", 0) >= cfg.get("max_tries", 4):
                out(f"try-port: you have used all {cfg.get('max_tries', 4)} tries for this attempt. Finish with what "
                    f"you have; mxconsole runs the final check when you stop.")
                return 3
            state = _start(cfg, a.config, state_dir, state)
            out(f"try-port: started try {state['tries']} of {cfg.get('max_tries', 4)} "
                f"(package {state['package']}); compiling and serving…")
    deadline = time.monotonic() + a.wait
    while True:
        state = read_state(state_dir)
        if state.get("status") != "running":
            break
        if not _alive(state.get("worker_pid")):
            state.update(status="done", code=1)
            (state_dir / "result.txt").write_text("try-port: the run stopped before it finished.\nRESULT: FAIL\n")
            break
        if time.monotonic() > deadline:
            elapsed = time.time() - state.get("started", time.time())
            out(f"try-port: still running ({elapsed:.0f}s; {state.get('step', 'starting')}). A cold compile can take "
                f"5 to 25 minutes: run the same command again to keep waiting. Don't edit the package meanwhile.")
            return 2
        time.sleep(0.5)
    with _locked(state_dir):
        state = read_state(state_dir)
        try:
            out((state_dir / "result.txt").read_text().rstrip("\n"))
        except OSError:
            out("try-port: no result was recorded.\nRESULT: FAIL")
        if state.get("package") and package_hash(Path(cfg["port_dir"])) != state["package"]:
            out("try-port: note: your package changed while this ran; the result is for the earlier version.")
        state["delivered"] = True
        _write_state(state_dir, state)
    return 0 if state.get("code") == 0 else 1


def _start(cfg: dict, config: Path, state_dir: Path, state: dict) -> dict:
    state = {"status": "running", "tries": state.get("tries", 0) + 1, "started": time.time(),
             "package": package_hash(Path(cfg["port_dir"])), "step": "starting", "delivered": False}
    (state_dir / "result.txt").unlink(missing_ok=True)
    _write_state(state_dir, state)
    proc = subprocess.Popen([sys.executable, "-m", "mxconsole.port_try", "--config", str(config), "--worker"],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True, cwd=cfg.get("cwd") or None)
    state["worker_pid"] = proc.pid
    _write_state(state_dir, state)
    return state


# ---- the detached worker ----------------------------------------------------------------------

def clean(lines: list[str]) -> list[str]:
    """Drop the compile and cache chatter that would bury the real error."""
    return [ln for ln in lines if not NOISE.search(ln)]


def _tail(text: str, n: int = KEEP_LINES) -> str:
    lines = clean(text.splitlines())
    return "\n".join((["…"] if len(lines) > n else []) + lines[-n:])


def _step(state_dir: Path, step: str, **extra) -> None:
    with _locked(state_dir):
        state = read_state(state_dir)
        state.update(step=step, **extra)
        _write_state(state_dir, state)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _work(cfg: dict, state_dir: Path) -> int:
    report: list[str] = []
    code, server = 1, None
    try:
        port = _free_port()
        _step(state_dir, f"compiling and serving on :{port}")
        with open(state_dir / "serve.log", "wb") as log:
            server = subprocess.Popen([*cfg["serve"], "--port", str(port)], stdout=log, stderr=subprocess.STDOUT,
                                      stdin=subprocess.DEVNULL, start_new_session=True,
                                      env={**os.environ, "MAX_SERVE_HOST": "127.0.0.1"})
        _step(state_dir, f"compiling and serving on :{port}", server_pid=server.pid)
        problem = _wait_healthy(server, port, cfg.get("health_timeout", 1500))
        if problem:
            report += [f"serve      FAILED: {problem}", _tail((state_dir / "serve.log").read_text(errors="replace"))]
            return 1
        report.append(f"serve      ok on :{port}")
        _step(state_dir, "sending a prompt")
        text = _complete(port, cfg)
        report.append(f"smoke      {cfg['smoke_prompt']!r} -> {text!r}")
        ok = bool(text.strip())
        for name in ("probe", "gates"):
            if not cfg.get(name):
                continue
            _step(state_dir, f"running the {name}")
            argv = [a.replace("{port}", str(port)) for a in cfg[name]]
            try:
                r = subprocess.run(argv, cwd=cfg.get("cwd") or None, capture_output=True, text=True, check=False,
                                   errors="replace", timeout=cfg.get("step_timeout", 900))
                report += [f"{name:<10} exit {r.returncode}", _tail(r.stdout + r.stderr)]
                ok = ok and (r.returncode == 0 or name == "probe")  # the probe informs; the gates decide
            except subprocess.TimeoutExpired:
                report.append(f"{name:<10} timed out after {cfg.get('step_timeout', 900)}s")
                ok = False
        code = 0 if ok else 1
        return code
    except Exception as e:  # noqa: BLE001 - the agent must always get a report, never a silent hang
        report.append(f"try-port: {type(e).__name__}: {e}")
        return 1
    finally:
        if server is not None:
            _kill_group(server.pid)
        report.append(f"RESULT: {'PASS' if code == 0 else 'FAIL'}")
        with _locked(state_dir):
            (state_dir / "result.txt").write_text("\n".join(report) + "\n")
            state = read_state(state_dir)
            if state.get("status") == "running":
                state.update(status="done", code=code, step="done", server_pid=None)
                _write_state(state_dir, state)


def _wait_healthy(server: subprocess.Popen, port: int, timeout: float) -> str | None:
    import httpx
    deadline = time.monotonic() + timeout
    while True:
        if server.poll() is not None:
            return f"max serve exited with code {server.returncode} while loading the package"
        if time.monotonic() > deadline:
            return f"max serve was not healthy after {timeout:g}s"
        with contextlib.suppress(httpx.HTTPError):
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=5.0).status_code == 200:
                return None
        time.sleep(0.5)


def _complete(port: int, cfg: dict) -> str:
    import httpx
    try:
        r = httpx.post(f"http://127.0.0.1:{port}/v1/completions", timeout=300.0, json={
            "model": cfg["repo_id"], "prompt": cfg["smoke_prompt"], "max_tokens": 16, "temperature": 0.0})
        return ((r.json().get("choices") or [{}])[0].get("text") or "") if r.status_code == 200 else ""
    except (httpx.HTTPError, ValueError):
        return ""


if __name__ == "__main__":
    sys.exit(main())
