"""Durable model-to-endpoint runs over the existing jobs and MAX lifecycle."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import secrets
import signal
import sqlite3
import subprocess
import sys
import time
import uuid

import httpx

from notebooks.mxstudio import jobs_db, state
from notebooks.mxstudio.servers import MANAGER as SERVERS

ROOT = Path(__file__).resolve().parents[2]
LOCAL_URL = "http://127.0.0.1:8080/v1"
ACTIVE_STATES = {"queued", "running", "verifying"}
TERMINAL_STATES = {"downloaded", "local_ready", "needs_setup", "awaiting_client_verification", "ready", "failed", "blocked", "stopped", "interrupted"}
SAFE_ERRORS = {
    "another deployment operation is busy; retry later",
    "MAX exited during preparation; inspect local MAX logs",
    "MAX preparation exceeded 600 seconds",
    "running model revision does not match deployment",
    "existing controller uses different credentials; explicit setup is required",
    "existing controller model differs; update its configuration explicitly",
    "existing controller revision differs; update its configuration explicitly",
    "controller exited; inspect endpoint-controller.log",
    "controller did not become responsive",
    "endpoint did not reject missing or invalid credentials",
    "endpoint advertises a different model",
    "private controller route is exposed through HTTPS",
    "endpoint did not return an OpenAI event stream",
    "endpoint returned a stream error",
    "verification needs nonempty content and a completed stream",
    "HTTPS endpoint revision differs from the deployment",
    "existing service environment differs; preserve it and review manually",
    "service environment values need paths without spaces or quotes",
}


@contextmanager
def _db():
    jobs_db.init_db()
    conn = sqlite3.connect(str(jobs_db.DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS deployment_schema (version INTEGER PRIMARY KEY);
            INSERT OR IGNORE INTO deployment_schema VALUES (1);
            CREATE TABLE IF NOT EXISTS deployment_runs (
                run_id TEXT PRIMARY KEY, data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS deployment_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL, data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS deployment_runtime (
                name TEXT PRIMARY KEY, data TEXT NOT NULL
            );
        """)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _save(run):
    run["updated_at"] = time.time()
    with _db() as db:
        db.execute("INSERT OR REPLACE INTO deployment_runs VALUES (?, ?)", (run["run_id"], json.dumps(run)))
    return run


def _runtime(name, value=None):
    with _db() as db:
        if value is not None:
            db.execute("INSERT OR REPLACE INTO deployment_runtime VALUES (?, ?)", (name, json.dumps(value)))
            return value
        row = db.execute("SELECT data FROM deployment_runtime WHERE name=?", (name,)).fetchone()
        return json.loads(row[0]) if row else None


def _event(run, stage, status, detail=None):
    event = {"at": time.time(), "attempt": run.get("attempt", 0), "stage": stage, "state": status, "detail": detail or {}}
    with _db() as db:
        db.execute("INSERT INTO deployment_events (run_id, data) VALUES (?, ?)", (run["run_id"], json.dumps(event)))
    run.update(stage=stage)
    _save(run)


def get_run(run_id):
    with _db() as db:
        row = db.execute("SELECT data FROM deployment_runs WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            return None
        run = json.loads(row[0])
        run["events"] = [json.loads(r[0]) for r in db.execute("SELECT data FROM deployment_events WHERE run_id=? ORDER BY sequence", (run_id,))]
    if run.get("state") in ACTIVE_STATES:
        job = jobs_db.get(run["job_id"]) if run.get("job_id") else None
        if (job and job["state"] == "failed") or time.time() - (job or run)["updated_at"] > 60:
            try:
                with _exclusive():
                    with _db() as db:
                        latest = json.loads(db.execute("SELECT data FROM deployment_runs WHERE run_id=?", (run_id,)).fetchone()[0])
                    if latest["state"] in ACTIVE_STATES and latest.get("job_id") == run["job_id"]:
                        latest.update(state="interrupted", error="deployment worker stopped before completion",
                                      recovery=f"Inspect stage history, then pixi run mxstudio runs resume {run_id}")
                        _save(latest)
                    run = dict(latest, events=run["events"])
            except RuntimeError:
                pass
    return run


def list_runs():
    with _db() as db:
        return sorted((json.loads(r[0]) for r in db.execute("SELECT data FROM deployment_runs")), key=lambda r: r["created_at"], reverse=True)


def list_endpoints():
    active = (_runtime("active_run") or {}).get("run_id")
    return [dict(r, active=r["run_id"] == active) for r in list_runs() if r.get("endpoint")]


def create_run(query: str, *, revision=None, mode="lan", hostname="api.mxstudio.local", ca_file=None):
    if not isinstance(query, str) or not query.strip() or len(query) > 256 or any(ord(c) < 32 for c in query):
        raise ValueError("model must be a nonempty reference of at most 256 characters")
    if mode not in {"pull", "local", "lan"}:
        raise ValueError("mode must be pull, local or lan")
    if revision is not None and (not isinstance(revision, str) or not re.fullmatch(r"[A-Za-z0-9._/-]{1,200}", revision)):
        raise ValueError("invalid model revision")
    if not isinstance(hostname, str) or len(hostname) > 253 or not all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) for label in hostname.split(".")):
        raise ValueError("hostname must be a DNS hostname, without scheme, path or port")
    if ca_file is not None and not Path(ca_file).expanduser().is_file():
        raise ValueError("CA file does not exist")
    now = time.time()
    identity = json.dumps({"query": query.strip(), "revision": revision, "mode": mode, "hostname": hostname.lower()}, sort_keys=True)
    return _save({"run_id": "deploy-" + uuid.uuid4().hex[:16], "schema_version": 1,
                   "input_fingerprint": hashlib.sha256(identity.encode()).hexdigest(),
                  "query": query.strip(), "revision": revision, "mode": mode, "hostname": hostname.lower(),
                  "ca_file": str(Path(ca_file).expanduser().resolve()) if ca_file else None,
                  "state": "created", "stage": "created", "created_at": now, "attempt": 0,
                  "model": None, "result": None, "error": None, "recovery": None, "job_id": None})


@contextmanager
def _exclusive(name="deployment"):
    import fcntl
    state.HOME.mkdir(parents=True, exist_ok=True)
    fd = os.open(state.HOME / f"{name}.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("another deployment operation is busy; retry later") from None
        yield
    finally:
        os.close(fd)


def enqueue(run_id):
    from notebooks.mxstudio import jobs
    with _exclusive("deployment-queue"):
        run = get_run(run_id)
        if run is None:
            raise ValueError("unknown deployment run")
        if run["state"] in ACTIVE_STATES and run.get("job_id"):
            job = jobs.get(run["job_id"])
            if job and job["state"] in {"queued", "processing"}:
                return run
        run.pop("events", None)
        run.update(state="queued", error=None, recovery=None)
        _save(run)
        try:
            job = jobs.enqueue_process("deployment", f"Deployment {run_id}", {"run_id": run_id})
        except Exception:
            run.update(state="failed", error="Could not start deployment worker", recovery=f"pixi run mxstudio runs resume {run_id}")
            _save(run)
            raise RuntimeError(run["error"]) from None
        # Update only the job id: the worker may already have completed stages.
        with _db() as db:
            db.execute("UPDATE deployment_runs SET data=json_set(data, '$.job_id', ?) WHERE run_id=?", (job["job_id"], run_id))
        return get_run(run_id)


def resume(run_id):
    return enqueue(run_id)


def _key():
    key = os.environ.get("MXSTUDIO_NUCLEUS_API_KEY", "").strip()
    if key:
        return key
    path = state.HOME / "endpoint.key"
    state.HOME.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if path.is_symlink() or path.stat().st_mode & 0o077:
            raise RuntimeError("endpoint key file must be private to its owner")
        key = path.read_text().strip()
    else:
        key = secrets.token_urlsafe(36)
        with os.fdopen(fd, "w") as output:
            output.write(key + "\n")
    if not key:
        raise RuntimeError("endpoint key is empty")
    return key


def _machine_fingerprint():
    try:
        identity = Path("/etc/machine-id").read_text().strip()
    except OSError:
        identity = platform.node()
    return hashlib.sha256(identity.encode()).hexdigest()


def _runtime_facts():
    try:
        version = importlib.metadata.version("modular")
    except importlib.metadata.PackageNotFoundError:
        version = "unknown"
    digest = hashlib.sha256()
    for name in ("deployments.py", "inventory.py", "servers.py", "nucleus.py"):
        digest.update((Path(__file__).parent / name).read_bytes())
    return {"max_package": version, "python": platform.python_version(), "os": platform.system(),
            "machine": platform.machine(), "code_fingerprint": digest.hexdigest()}


def _prepare(run):
    from notebooks.mxstudio import inventory
    entry = inventory.entry_for(run["model"])
    with SERVERS.transition():
        before = SERVERS.status()
        started = SERVERS.start(entry)
        if started.get("pid") and before.get("pid") != started["pid"]:
            run["max_owner"] = {k: started.get(k) for k in ("pid", "process_identity", "model_id", "revision")}
            _runtime("active_run", {"run_id": run["run_id"]})
            _save(run)
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            status = SERVERS.status()
            if status.get("state") == "running" and status.get("model_id") == entry.id and SERVERS.model_matches(entry.id):
                if status.get("revision") != run["model"]["revision"]:
                    raise RuntimeError("running model revision does not match deployment")
                if before.get("pid") != status.get("pid"):
                    run["max_owner"] = {k: status.get(k) for k in ("pid", "process_identity", "model_id", "revision")}
                else:
                    previous_id = (_runtime("active_run") or {}).get("run_id")
                    previous = get_run(previous_id) if previous_id else None
                    owner = (previous or {}).get("max_owner")
                    if owner and all(status.get(k) == value for k, value in owner.items()):
                        run["max_owner"] = owner
                return status
            if status.get("state") == "crashed":
                raise RuntimeError("MAX exited during preparation; inspect local MAX logs")
            time.sleep(0.2)
    raise TimeoutError("MAX preparation exceeded 600 seconds")


def _controller_status(key):
    try:
        with httpx.Client(timeout=3, trust_env=False) as client:
            response = client.get("http://127.0.0.1:8080/nucleus/status", headers={"Authorization": "Bearer " + key})
            if response.status_code == 401:
                raise RuntimeError("existing controller uses different credentials; explicit setup is required")
            response.raise_for_status()
            return response.json()
    except httpx.ConnectError:
        return None


def _ensure_controller(run, key):
    from notebooks.mxstudio import servers
    current = _controller_status(key)
    model = run["model"]
    if current is not None:
        if current.get("desired_model") != model["model_id"]:
            raise RuntimeError("existing controller model differs; update its configuration explicitly")
        observed = current.get("server", {})
        if current.get("desired_revision") != model["revision"] or observed.get("revision") != model["revision"]:
            raise RuntimeError("existing controller revision differs; update its configuration explicitly")
        owned = _runtime("controller")
        if owned and servers._owned(owned):
            _runtime("controller", dict(owned, run_id=run["run_id"]))
        return
    owned = _runtime("controller")
    if owned and servers._owned(owned):
        raise RuntimeError("owned controller is starting or unresponsive; inspect its log before retrying")
    log_path = state.HOME / "logs" / "endpoint-controller.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(MXSTUDIO_HOME=str(state.HOME), MXSTUDIO_NUCLEUS_API_KEY=key,
               NUCLEUS_MODEL_ID=model["model_id"], NUCLEUS_MODEL_REVISION=model["revision"],
               PYTHONPATH=str(ROOT / "notebooks"), NUCLEUS_PREWARM="0")
    with log_path.open("ab") as log:
        process = subprocess.Popen([sys.executable, "-m", "mxstudio.cli", "nucleus", "run", "--no-prewarm"],
                                   cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    owner = {"pid": process.pid, "process_identity": servers._process_identity(process.pid), "run_id": run["run_id"]}
    _runtime("controller", owner)
    _runtime("active_run", {"run_id": run["run_id"]})
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError("controller exited; inspect endpoint-controller.log")
        if _controller_status(key) is not None:
            return
        time.sleep(0.1)
    raise TimeoutError("controller did not become responsive")


def _stop_controller():
    from notebooks.mxstudio import servers
    owned = _runtime("controller")
    if owned and servers._owned(owned):
        os.killpg(owned["pid"], signal.SIGTERM)
        for _ in range(50):
            if not servers._owned(owned) or not servers._alive(owned["pid"]):
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("controller did not stop; inspect it before changing services")
    _runtime("controller", {})


def _stream_lines(stream, start):
    pending = b""
    total = 0
    for chunk in stream.iter_bytes():
        total += len(chunk)
        if total > 1024 * 1024:
            raise RuntimeError("verification stream exceeded size limit")
        if time.monotonic() - start > 300:
            raise TimeoutError("generation verification exceeded deadline")
        pending += chunk
        while b"\n" in pending:
            line, pending = pending.split(b"\n", 1)
            if len(line) > 65536:
                raise RuntimeError("verification event exceeded size limit")
            yield line.decode("utf-8").rstrip("\r")
        if len(pending) > 65536:
            raise RuntimeError("verification event exceeded size limit")
    if pending:
        yield pending.decode("utf-8").rstrip("\r")


def probe(base_url, model_id, key, *, ca_file=None, require_https=False):
    """Check auth, route isolation and real SSE content without logging prompts/keys."""
    from urllib.parse import urlsplit
    url = urlsplit(base_url)
    if url.username or url.password or url.query or url.fragment or url.path != "/v1":
        raise ValueError("base URL must end in /v1 without credentials, query or fragment")
    if require_https and url.scheme != "https":
        raise ValueError("LAN verification requires HTTPS")
    if url.scheme not in {"http", "https"}:
        raise ValueError("unsupported endpoint scheme")
    import ssl
    verification = ssl.create_default_context(cafile=ca_file) if ca_file else True
    headers = {"Authorization": "Bearer " + key}
    start = time.monotonic()
    with httpx.Client(timeout=httpx.Timeout(300, connect=5), verify=verification, trust_env=False, follow_redirects=False) as client:
        for bad in ({}, {"Authorization": "Bearer " + secrets.token_urlsafe(16)}):
            response = client.get(base_url + "/models", headers=bad)
            if response.status_code != 401:
                raise RuntimeError("endpoint did not reject missing or invalid credentials")
        response = client.get(base_url + "/models", headers=headers)
        response.raise_for_status()
        models = response.json().get("data", [])
        advertised = next((entry for entry in models if entry.get("id") == model_id), None)
        if advertised is None:
            raise RuntimeError("endpoint advertises a different model")
        revision = advertised.get("revision")
        if require_https:
            root = base_url[:-3]
            for path in ("/nucleus/status", "/api/settings", "/"):
                if client.get(root + path, headers=headers).status_code != 404:
                    raise RuntimeError("private controller route is exposed through HTTPS")
        content_bytes = 0
        completed = False
        with client.stream("POST", base_url + "/chat/completions", headers=headers,
                           json={"model": model_id, "messages": [{"role": "user", "content": "Reply with one word: ready"}],
                                 "max_tokens": 16, "temperature": 0, "stream": True}) as stream:
            stream.raise_for_status()
            if "text/event-stream" not in stream.headers.get("content-type", ""):
                raise RuntimeError("endpoint did not return an OpenAI event stream")
            for line in _stream_lines(stream, start):
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    completed = True
                    break
                payload = json.loads(raw)
                if payload.get("error"):
                    raise RuntimeError("endpoint returned a stream error")
                for choice in payload.get("choices", []):
                    content = choice.get("delta", {}).get("content")
                    if isinstance(content, str):
                        content_bytes += len(content.strip().encode())
        if not content_bytes or not completed:
            raise RuntimeError("verification needs nonempty content and a completed stream")
    return {"schema_version": 1, "base_url": base_url, "model_id": model_id, "revision": revision, "content_bytes": content_bytes,
            "stream_complete": True, "authentication_verified": True, "tls_verified": require_https,
            "private_routes_verified": require_https, "elapsed_s": round(time.monotonic() - start, 3),
            "verified_at": time.time(), "vantage": "server", "machine_fingerprint": _machine_fingerprint(), "runtime": _runtime_facts()}


def _setup(run, key):
    import pwd
    import grp
    import shlex
    directory = state.HOME / "deployments" / run["run_id"]
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / "nucleus.env"
    model = run["model"]
    values = {"MXSTUDIO_HOME": str(state.HOME.resolve()), "NUCLEUS_MODEL_ID": model["model_id"],
              "NUCLEUS_MODEL_REVISION": model["revision"], "MXSTUDIO_NUCLEUS_API_KEY": key,
              "NUCLEUS_HOSTNAME": run["hostname"], "HF_HOME": os.environ.get("HF_HOME", str(Path.home() / ".cache/huggingface"))}
    if any(any(c.isspace() or c in "\\\"'" for c in value) for value in values.values()):
        raise RuntimeError("service environment values need paths without spaces or quotes")
    text = "".join(f"{name}={value}\n" for name, value in values.items())
    if path.exists():
        if path.is_symlink() or path.stat().st_mode & 0o077 or path.read_text() != text:
            raise RuntimeError("existing service environment differs; preserve it and review manually")
    else:
        with os.fdopen(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w") as output:
            output.write(text)
    user = pwd.getpwuid(os.getuid()).pw_name
    group = grp.getgrgid(os.getgid()).gr_name
    installed = f"/etc/mxstudio/deployments/{run['run_id']}/nucleus.env"
    options = ["--with-caddy", "--env-file", installed, "--service-user-current", "--service-group", group]
    installer = str(ROOT / "deploy/nucleus/install.sh")
    copy = ["sudo", "install", "-D", "-o", "root", "-g", group, "-m", "0640", str(path), installed]
    return {"environment_file": str(path), "installed_environment_file": installed,
            "service_user": user, "service_group": group,
            "dry_run_command": shlex.join(["bash", installer, "--dry-run", *options]),
            "environment_install_command": f"sudo test ! -e {shlex.quote(installed)} && " + shlex.join(copy),
            "install_command": shlex.join(["sudo", "bash", installer, "--apply", *options]),
            "instructions": ["Review the installer dry run and the protected environment file locally; do not print or share its secrets.",
                             "After approval, copy the environment to its unique root-protected destination with the supplied command. If it exists, compare it privately; never overwrite hand edits.",
                             "Run the explicit installer only after approving its package/service changes. Existing services require reviewed manual configuration updates, not installer overwrite.",
                             "The service group must be private to the invoking user. Keep the existing state/cache paths and model revision.",
                             "Configure LAN hostname resolution and enroll the Caddy CA on each client. Do not disable TLS verification.",
                             f"Resume with pixi run mxstudio runs resume {run['run_id']}. Use endpoint verify with --ca-file for an explicitly trusted CA."]}


def _installed_service(run):
    from deploy.nucleus import preflight
    observed = subprocess.run(
        ["systemctl", "show", "--property=EnvironmentFiles", "--value", "mxstudio-nucleus.service"],
        capture_output=True, text=True, timeout=5, check=False,
    )
    match = re.fullmatch(r"(/[/A-Za-z0-9_.-]+) \(ignore_errors=no\)", observed.stdout.strip())
    if observed.returncode or not match:
        raise RuntimeError("Installed Nucleus service needs a single protected EnvironmentFile")
    path = Path(match[1])
    setup = run["setup"]
    identity = preflight.verify_installed_identity(path, setup["service_user"], setup["service_group"])
    values = preflight.read_environment(path)
    expected = {"MXSTUDIO_HOME": str(state.HOME.resolve()), "NUCLEUS_MODEL_ID": run["model"]["model_id"],
                "NUCLEUS_MODEL_REVISION": run["model"]["revision"], "NUCLEUS_HOSTNAME": run["hostname"],
                "HF_HOME": os.environ.get("HF_HOME", str(Path.home() / ".cache/huggingface"))}
    if identity["unit_state"] != "active" or any(values.get(k) != v for k, v in expected.items()):
        raise RuntimeError("Installed service does not match the approved model, cache, hostname and state")
    return identity


def execute(run_id, *, job_id, progress):
    from notebooks.mxstudio import inventory
    run = get_run(run_id)
    if run is None:
        raise ValueError("unknown deployment run")
    run.pop("events", None)
    locked = False
    try:
        with _exclusive():
            locked = True
            run = get_run(run_id)
            run.pop("events", None)
            run.update(state="running", job_id=job_id, attempt=run.get("attempt", 0) + 1, error=None, recovery=None, result=None)
            if run.get("endpoint"):
                for field in ("local_evidence", "https_evidence", "client_evidence"):
                    run["endpoint"].pop(field, None)
            _save(run)
            progress("Resolving exact model revision", 5)
            _event(run, "resolve", "running")
            previous = run.get("model") or {}
            pinned = bool(previous.get("model_id") and previous.get("revision"))
            if pinned:
                model = inventory.resolve_model(previous["model_id"], revision=previous["revision"])
            else:
                model = inventory.resolve_model(run["query"], revision=run["revision"])
            same_identity = all(model.get(k) == previous.get(k) for k in ("model_id", "revision"))
            if not pinned or same_identity:
                run["model"] = model
            download_only = (
                run["mode"] == "pull"
                and bool(model.get("model_id"))
                and bool(re.fullmatch(r"[0-9a-fA-F]{40}", str(model.get("revision", ""))))
                and model.get("format_status") == "supported"
                and model.get("has_safetensors") is True
                and model.get("support_status") in {"unsupported", "unknown", "unsupported_encoding"}
            )
            if model.get("ok") is False and not download_only:
                run.update(state="needs_input" if model.get("state") == "ambiguous" else "blocked",
                           error=model.get("error") or "Model reference could not be resolved",
                           recovery=model.get("advisory") or "Inspect model candidates and provide an exact accessible Hugging Face reference")
                _event(run, "resolve", run["state"], {"candidates": model.get("candidates", [])})
                return _save(run)
            if pinned and not same_identity:
                raise RuntimeError("resolved model identity does not match the pinned deployment")
            _event(run, "resolve", "done", {"model_id": model["model_id"], "revision": model["revision"]})
            if run["mode"] != "pull" and not model.get("supported", False):
                run.update(state="blocked", error="Architecture/encoding is not established as MAX-supported",
                           recovery={"observations": model.get("advisory", []),
                                     "next": "Check pixi run mxstudio catalog and model compatibility evidence. Unsupported architectures need the guided Architectures workflow; no automatic port or publication was attempted."})
                _event(run, "compatibility", "blocked")
                return _save(run)
            progress("Downloading and validating complete checkpoint", 15)
            _event(run, "download", "running")
            pulled = inventory.pull_model(model)
            downloaded_only = download_only and pulled.get("state") == "downloaded" and pulled.get("downloaded") is True
            if pulled.get("ok") is False and not downloaded_only:
                run.update(state="blocked", error=pulled.get("error") or "Checkpoint download was blocked",
                           recovery=pulled.get("advisory") or "Inspect supported weight formats and Hugging Face access")
                _event(run, "download", "blocked")
                return _save(run)
            if any(pulled.get(k) != model.get(k) for k in ("model_id", "revision")):
                raise RuntimeError("downloaded model identity does not match the pinned deployment")
            run["model"] = pulled
            _event(run, "download", "done", {"revision": run["model"]["revision"]})
            if run["mode"] == "pull":
                run.update(state="downloaded", result={"model": run["model"]})
                return _save(run)
            if not run["model"].get("supported", False):
                run.update(state="blocked", error="Architecture/encoding is not established as MAX-supported", recovery="Use the guided Architectures workflow; automatic porting is a later milestone. Download is not serving proof.")
                _event(run, "compatibility", "blocked")
                return _save(run)
            key = _key()
            controller = _controller_status(key)
            if controller is not None and (controller.get("desired_model") != run["model"]["model_id"]
                                           or controller.get("desired_revision") != run["model"]["revision"]):
                from notebooks.mxstudio import servers
                owned = _runtime("controller")
                if owned and servers._owned(owned):
                    with SERVERS.transition():
                        _stop_controller()
                else:
                    run["setup"] = _setup(run, key)
                    run.update(state="needs_setup", recovery=run["setup"], error="Existing controller has a different model/revision; update its managed configuration explicitly before resuming.")
                    _event(run, "publication", "needs_setup")
                    return _save(run)
            progress("Preparing pinned model with MAX", 45)
            _event(run, "prepare", "running")
            run["runtime"] = _runtime_facts()
            _prepare(run)
            _event(run, "prepare", "done")
            _ensure_controller(run, key)
            _runtime("active_run", {"run_id": run_id})
            progress("Verifying authenticated local generation", 70)
            _event(run, "local_verify", "running")
            local = probe(LOCAL_URL, run["model"]["model_id"], key)
            if local.get("revision") != run["model"]["revision"]:
                raise RuntimeError("running model revision does not match deployment")
            run["endpoint"] = {"endpoint_id": run_id, "profile": "nucleus", "base_url": LOCAL_URL,
                               "model_id": run["model"]["model_id"], "revision": run["model"]["revision"],
                               "credential_source": "environment:MXSTUDIO_NUCLEUS_API_KEY" if os.environ.get("MXSTUDIO_NUCLEUS_API_KEY", "").strip() else str(state.HOME / "endpoint.key"),
                               "ownership": "managed", "local_evidence": local}
            _event(run, "local_verify", "done", local)
            if run["mode"] == "local":
                run.update(state="local_ready", result=run["endpoint"])
                return _save(run)
            run["endpoint"]["base_url"] = f"https://{run['hostname']}/v1"
            run["setup"] = _setup(run, key)
            # A transient controller must release 8080 before an operator installs systemd.
            owned = _runtime("controller")
            if owned and owned.get("run_id") == run_id:
                _stop_controller()
                run.update(state="needs_setup", recovery=run["setup"], result=run["endpoint"])
                _event(run, "publication", "needs_setup")
                return _save(run)
            return _verify(run, key, run.get("ca_file"))
    except Exception as exc:
        if not locked:
            raise RuntimeError("another deployment operation is busy; retry later") from None
        # External libraries may include tokens or request headers in errors.
        message = str(exc) if str(exc) in SAFE_ERRORS else f"{run.get('stage', 'deployment')} failed ({type(exc).__name__}); inspect local prerequisites and retained stage evidence"
        run.update(state="blocked" if isinstance(exc, (ValueError, RuntimeError)) else "failed",
                   error=message,
                   recovery=f"Correct credentials, model support, cache, service or trust configuration; then pixi run mxstudio runs resume {run_id}")
        _event(run, run.get("stage", "deployment"), run["state"], {"error_type": type(exc).__name__, "message": message})
        return _save(run)


def _verify(run, key, ca_file):
    run["endpoint"].pop("https_evidence", None)
    run["endpoint"].pop("client_evidence", None)
    run.update(state="verifying", error=None, result=None)
    _event(run, "publication", "running")
    try:
        run["service"] = _installed_service(run)
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError):
        run.update(state="needs_setup", error="Installed service identity is missing or differs from this run",
                   recovery=run.get("setup") or "Resume the run and review its explicit installation plan")
        _event(run, "publication", "needs_setup")
        return _save(run)
    _event(run, "https_verify", "running")
    evidence = probe(f"https://{run['hostname']}/v1", run["model"]["model_id"], key, ca_file=ca_file, require_https=True)
    if evidence.get("revision") != run["model"]["revision"]:
        raise RuntimeError("HTTPS endpoint revision differs from the deployment")
    run["endpoint"]["https_evidence"] = evidence
    run["endpoint"].pop("client_evidence", None)
    if ca_file:
        run["ca_file"] = str(Path(ca_file).expanduser().resolve())
    run.update(state="awaiting_client_verification", result=run["endpoint"], error=None,
               recovery="Verify a streamed chat from a second LAN device with this hostname, model, API key and trusted CA. Server-side proof does not establish client trust.")
    _event(run, "https_verify", "done", evidence)
    return _save(run)


def record_client_evidence(run_id, evidence):
    """Accept a bounded trusted-operator report, not a remote attestation."""
    import math
    if not isinstance(evidence, dict) or len(json.dumps(evidence, allow_nan=False).encode()) > 65536:
        raise ValueError("client evidence must be a JSON object of at most 64 KiB")
    with _exclusive():
        run = get_run(run_id)
        if not run or run["mode"] != "lan" or run["state"] not in {"awaiting_client_verification", "ready"}:
            raise ValueError("run needs successful server HTTPS verification first")
        endpoint = run["endpoint"]
        server = endpoint.get("https_evidence", {})
        now = time.time()
        if now - server.get("verified_at", 0) > 86400:
            raise ValueError("server evidence expired; verify the endpoint again")
        for field in ("base_url", "model_id", "revision"):
            if evidence.get(field) != endpoint.get(field):
                raise ValueError(f"client evidence {field} does not match the endpoint")
        if type(evidence.get("schema_version")) is not int or evidence["schema_version"] != 1 or evidence.get("vantage") != "client":
            raise ValueError("unsupported client evidence schema or vantage")
        for field in ("stream_complete", "authentication_verified", "tls_verified", "private_routes_verified"):
            if evidence.get(field) is not True:
                raise ValueError(f"client evidence requires {field}")
        size = evidence.get("content_bytes")
        if type(size) is not int or not 0 < size <= 1024 * 1024:
            raise ValueError("client evidence requires bounded nonempty content")
        for field in ("verified_at", "elapsed_s"):
            value = evidence.get(field)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(f"invalid client evidence {field}")
        if not server.get("verified_at", 0) <= evidence["verified_at"] <= now + 300 or now - evidence["verified_at"] > 86400:
            raise ValueError("client evidence is stale or its clock is incorrect")
        if not 0 <= evidence["elapsed_s"] <= 300:
            raise ValueError("client probe exceeded its time budget")
        fingerprint = evidence.get("machine_fingerprint", "")
        if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint) or fingerprint == server.get("machine_fingerprint"):
            raise ValueError("client evidence must come from a different machine")
        fields = ("schema_version", "base_url", "model_id", "revision", "vantage", "stream_complete",
                  "authentication_verified", "tls_verified", "private_routes_verified", "content_bytes",
                  "verified_at", "elapsed_s", "machine_fingerprint")
        clean = {field: evidence[field] for field in fields}
        clean.update(provenance="trusted_operator_report", accepted_at=now)
        endpoint["client_evidence"] = clean
        run.pop("events", None)
        run.update(state="ready", result=endpoint, error=None, recovery=None)
        _event(run, "client_verify", "done", clean)
        return _save(run)


def verify_endpoint(run_id, *, ca_file=None):
    with _exclusive():
        run = get_run(run_id)
        if not run or not run.get("endpoint"):
            raise ValueError("deployment has no endpoint")
        run.pop("events", None)
        try:
            if run["mode"] != "lan":
                run.update(state="verifying", result=None)
                run["endpoint"].pop("local_evidence", None)
                _event(run, "local_verify", "running")
                evidence = probe(LOCAL_URL, run["model"]["model_id"], _key())
                if evidence.get("revision") != run["model"]["revision"]:
                    raise RuntimeError("running model revision does not match deployment")
                run["endpoint"]["local_evidence"] = evidence
                run.update(state="local_ready", result=run["endpoint"], error=None, recovery=None)
                _event(run, "local_verify", "done", evidence)
                return _save(run)
            return _verify(run, _key(), ca_file or run.get("ca_file"))
        except Exception as exc:
            message = str(exc) if str(exc) in SAFE_ERRORS else "Endpoint verification failed; check service, credentials and CA trust"
            run.update(state="failed", error=message, recovery=f"mxs endpoint verify {run_id}")
            _event(run, run.get("stage", "verify"), "failed", {"message": message})
            return _save(run)


def stop_endpoint(run_id):
    with _exclusive():
        run = get_run(run_id)
        owned = _runtime("controller")
        if not run or not (run.get("endpoint") or run.get("max_owner") or (owned or {}).get("run_id") == run_id):
            raise ValueError("deployment has no owned endpoint resources")
        if (_runtime("active_run") or {}).get("run_id") != run_id:
            raise RuntimeError("this run no longer owns the active endpoint")
        owned = _runtime("controller")
        if not owned and _controller_status(_key()) is not None:
            raise RuntimeError("service-managed endpoint: explicitly stop mxstudio-nucleus.service with systemctl before stopping MAX")
        with SERVERS.transition():
            current = SERVERS.status()
            owner = run.get("max_owner")
            if owner and current.get("state") not in {"stopped", "crashed"} and any(current.get(k) != v for k, v in owner.items()):
                raise RuntimeError("MAX identity changed; refusing to stop another process")
            _stop_controller()
            if owner:
                SERVERS.stop()
        run.pop("events", None)
        run.update(state="stopped", error=None, recovery=None)
        _runtime("active_run", {})
        _event(run, "stop", "done")
        return _save(run)
