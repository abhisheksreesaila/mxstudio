"""Automatic port: `mxc port <ref> --auto` as a background job.

A job runs Modular's `import-model` skill unattended through Claude Code (headless), then checks the
result and registers it. Stages, in order:

  workspace  resolve the ref, make MXC_HOME/ports/<name> with config.json and the chosen donor
  skills     clone/update github.com/modular/skills, link them into the workspace, write PORT.md
  generate   `claude -p` in the workspace writes the custom architecture package
  build      the skill's check_port.py: imports the port like `max serve` does and checks every weight
  parity     smoke generation through `max serve --custom-architectures`, then the skill's
             run_oss_gates.py verify phase (HF vs MAX logprobs, greedy prefixes)
  register   `mxc pull --port` path (weights pinned from Hugging Face, architecture = the package)
  serve      start the model like any other

When build or parity fails, generate runs again with the error output appended ("fix this"), up to
`max_attempts` times; each attempt is its own set of stage rows. The whole transcript of the agent and
of every command goes to MXC_HOME/ports/<id>/port.log. Cancel kills the process tree of whatever runs.
Everything outside this process (claude, the skill's checks, max) is injectable, so tests run fakes.

Safety (config.json and the model card are untrusted, so assume prompt injection):
- The agent, the gate checks and the smoke server run under bubblewrap (port_sandbox.py) with a scrubbed
  environment, a job-private Claude config, and `--permission-prompts none`. The allow list is only the
  skill's own scripts plus file tools. Without bwrap `--auto` is refused unless `--unsandboxed` is given.
- Nothing shared is writable from a sandbox. The skill's pixi env is installed beforehand from the pristine
  clone, outside the sandbox; the clone, the model's Hugging Face repo dir and the toolchain are bound
  read-only; every cache (pixi, rattler, uv, HF_HOME with its token and modules, MODULAR_DERIVED_PATH)
  lives in MXC_HOME/ports/<id>/work, which is deleted when the job ends. port.log is outside it.
  MAX also keeps a cache in its install prefix (`share/max/.max_cache`); the prefix is read-only here.
- The gate scripts are hash-checked, and restored from git if touched, before every use.
Accepted risks, documented rather than solved:
- Code in the agent sandbox can read the Claude login (the real ~/.claude/.credentials.json is bound into
  the private config dir, so a token refresh writes back to the one file instead of a stale copy) and it
  has network access: a prompt-injected agent could send the login out. bwrap cannot prevent that.
- check_port.py and `max serve` import and run the generated code, so a passing gate means "an agent that did
  not cheat produced matching output". The sandbox limits damage; it does not vouch for the port.
"""
from __future__ import annotations

import atexit
import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import signal
import socket
import subprocess
import threading
import time
import weakref
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

from mxconsole import port, port_sandbox, resolve, supervisor
from mxconsole.pulls import NAME_RE, REF_RE, BadPull
from mxconsole.store import Store, mxc_home

log = logging.getLogger("mxconsole.port_auto")

FINISHED = ("done", "failed", "cancelled")
STAGE_NAMES = ["workspace", "skills", "generate", "build", "parity", "register", "serve"]
ATTEMPT_STAGES = ("generate", "build", "parity")
DEFAULT_ATTEMPTS = 3
MAX_ATTEMPTS = 10
GENERATE_TIMEOUT = 3 * 3600   # seconds for one `claude -p` run
CHECK_TIMEOUT = 45 * 60       # seconds for the skill's build/verify checks
INSTALL_TIMEOUT = 60 * 60       # the skill env pulls MAX and PyTorch the first time
SMOKE_TIMEOUT = 40 * 60       # a cold compile of a new architecture takes 5 to 25 minutes
SERVE_TIMEOUT = 40 * 60
TAIL_LINES = 40
FEEDBACK_CHARS = 6000         # how much error output is fed back to the agent
KEEP_ROWS = 30
SMOKE_PROMPT = "The capital of France is"

NO_SANDBOX = ("Automatic port needs bubblewrap (`bwrap`) to confine the agent that reads the model's untrusted files, "
              "and it was not found. Install bubblewrap, or pass --unsandboxed to run without confinement (not recommended).")
NO_CLAUDE = ("Claude Code (`claude`) was not found. Install Claude Code, or run the guided `mxc port {ref}`. "
             "(Set MXC_CLAUDE to its full path if it is installed but not on the daemon's PATH.)")

# What the agent may do. Files: Read/Edit/Write/Glob/Grep (acceptEdits confines edits to the workspace).
# Commands: only the skill's own scripts through its pixi env, `pixi install`, and `cd` (to reach the skill
# root). No general shell tools: file work goes through Read/Edit/Write/Glob/Grep. The model card may be read
# from huggingface.co. Anything else would prompt, and prompts are denied (--permission-prompts none).
SKILL_SCRIPTS = ("inspect_hf.py", "list_checkpoint_keys.py", "list_native_archs.py", "check_walls.py",
                 "scaffold.py", "check_port.py", "run_oss_gates.py", "compare_layers.py", "import_model.py")
ALLOWED_TOOLS = ["Read", "Write", "Edit", "Glob", "Grep", "WebFetch(domain:huggingface.co)",
                 "Bash(cd:*)", "Bash(pixi install)", "Bash(pixi install --locked)",
                 *[f"Bash(pixi run python scripts/{n}:*)" for n in SKILL_SCRIPTS],
                 *[f"Bash(pixi run -e cuda python scripts/{n}:*)" for n in SKILL_SCRIPTS]]

SCHEMA = """
CREATE TABLE IF NOT EXISTS ports (
    id INTEGER PRIMARY KEY,
    ref TEXT NOT NULL,
    name TEXT,
    repo_id TEXT,
    status TEXT NOT NULL,
    stage TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    started_at REAL NOT NULL,
    finished_at REAL,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    stages TEXT NOT NULL DEFAULT '[]',
    model TEXT,
    procs TEXT
);
"""


class PortBusy(Exception):
    def __init__(self, job: dict):
        super().__init__(f"{job['ref']} is already being ported")
        self.job = job


class PortUnsafe(Exception):
    """No sandbox is available and the caller did not accept running without one."""


class _Cancelled(Exception):
    pass


class _StageFailed(Exception):
    """A stage failed. `retry` says whether another generate attempt could fix it; `output` is the
    error text fed back to the agent."""

    def __init__(self, message: str, *, retry: bool = False, output: str = ""):
        super().__init__(message)
        self.retry, self.output = retry, output


@dataclass
class Ctx:
    """What the checks need to know about one port."""
    ref: str
    repo_id: str
    name: str
    workspace: Path
    port_dir: Path
    skills_root: Path             # the pristine clone: the build/parity gates run from here, never the agent
    weights: str = ""            # the downloaded snapshot, set once before the agent runs (outside the sandbox)
    gate_hash: str = ""           # hash of the gate scripts right after the clone was updated
    extra_allowed: tuple = ()     # per-model additions to the allow list (the donor copy commands)
    serving_max: str = ""         # the MAX that will serve the port (mxconsole's), which the skill env may not match


@dataclass
class PortTools:
    """The commands the job shells out to. Each returns (argv, cwd), or None to skip the check."""
    install: Callable[[Ctx], "tuple[list[str], Path] | None"] | None = None
    build: Callable[[Ctx], "tuple[list[str], Path] | None"] | None = None
    verify: Callable[[Ctx, int], "tuple[list[str], Path] | None"] | None = None

    def install_cmd(self, c: Ctx):
        return (self.install or _skill_install)(c)

    def build_cmd(self, c: Ctx):
        return (self.build or _skill_build)(c)

    def verify_cmd(self, c: Ctx, port_: int):
        return (self.verify or _skill_verify)(c, port_)


def _pixi_run(c: Ctx, script: str, *args: str):
    if not (c.skills_root / port.ENTRY_SKILL / "scripts" / script).is_file():
        return None
    return (["pixi", "run", "python", f"scripts/{script}", *args], c.skills_root / port.ENTRY_SKILL)


def _skill_install(c: Ctx):
    """Install the pristine skill's pixi env, outside any sandbox (its lockfile when it has one), so the
    sandboxed processes find it ready and need no network or shared cache to run it."""
    d = c.skills_root / port.ENTRY_SKILL
    if not (d / "pixi.toml").is_file():
        return None
    return (["pixi", "install", *(["--locked"] if (d / "pixi.lock").is_file() else [])], d)


def _skill_build(c: Ctx):
    # the local snapshot, not the repo id: inside the sandbox the HF cache is private and empty, so a repo id
    # would re-download the whole model on every attempt
    return _pixi_run(c, "check_port.py", c.weights or c.repo_id, "--port-dir", str(c.port_dir))


def _skill_verify(c: Ctx, port_: int):
    # by repo id: the gate loads the Hub config; the weights are found in the sandbox's offline HF cache
    return _pixi_run(c, "run_oss_gates.py", c.repo_id, "--port-dir", str(c.port_dir),
                     "--phase", "verify", "--port", str(port_), "--served-model-name", c.repo_id)


def find_claude(explicit: str | None = None) -> str | None:
    cand = explicit or os.environ.get("MXC_CLAUDE") or "claude"
    return shutil.which(cand)


def claude_argv(claude: str, prompt: str, extra_allowed: tuple[str, ...] = (), *, fallback_sandbox: bool = False) -> list[str]:
    """The headless Claude Code call. `-p` runs once and exits; acceptEdits lets it edit inside the
    workspace; the allow list covers the skill's scripts; anything that would prompt is denied; only the
    (job-private) user settings load, no MCP servers, no saved session. stream-json (needs --verbose)
    gives one event per line, which the log renders readably. `fallback_sandbox` turns on Claude Code's
    own sandbox when bubblewrap is not available."""
    argv = [claude, "-p", prompt, "--permission-mode", "acceptEdits", "--permission-prompts", "none",
            "--allowedTools", *ALLOWED_TOOLS, *extra_allowed,
            "--setting-sources", "project", "--strict-mcp-config",
            "--output-format", "stream-json", "--verbose", "--no-session-persistence"]
    if fallback_sandbox:
        argv += ["--settings", json.dumps({"sandbox": {"enabled": True, "autoAllowBashIfSandboxed": False}})]
    return argv


def package_slug(name: str) -> str:
    slug = re.sub(r"[^0-9a-z]+", "_", name.lower()).strip("_") or "port"
    return slug if slug[0].isalpha() else f"m_{slug}"


def build_prompt(c: Ctx, attempt: int, feedback: str | None, donor_note: str) -> str:
    serving = (f"The port will be served by MAX {c.serving_max} (mxconsole's), which may be older than the MAX in "
               f"the skill's environment: use only APIs that exist in {c.serving_max}, nothing newer.\n"
               if c.serving_max else "")
    base = f"""You are porting a Hugging Face model to MAX, unattended. Nobody is available to answer questions or confirm a plan: where the skill says to propose a plan and wait for a veto, state your default in one paragraph and carry on.

Model: {c.repo_id}
Workspace (your working directory): {c.workspace}
Read PORT.md and config.json here first. Follow the `import-model` skill (.claude/skills/import-model/SKILL.md; the skill root is {c.skills_root / port.ENTRY_SKILL}, read-only, with its pixi environment already installed) for this model, phase by phase, and write the custom architecture package for it.

{donor_note}
{serving}Package folder: {c.port_dir}
That exact folder must end up holding arch.py and an __init__.py that exposes ARCHITECTURES. Scaffold with `--output-dir {c.port_dir.parent} --slug {c.port_dir.name}` (or create it by hand). Do not write outside the workspace, except by running the skill's own commands.

You are done when `pixi run python scripts/check_port.py {c.weights or c.repo_id} --port-dir {c.port_dir}` (run from the skill root; the weights are already downloaded there, read-only — use that path, not the repo id, or the model is downloaded again) passes and the implementation-complete criteria in the skill are met. Do not start `max serve` yourself and do not install packages with pip; mxconsole serves and verifies the port afterwards, against the Hugging Face reference. Finish with a short summary of what you changed."""
    if attempt <= 1 and not feedback:
        return base
    return (base + f"""

This is attempt {attempt}. The package folder already exists from the previous attempt; fix it, do not start over.
The last check failed with this output. Fix this:

{feedback}""")


GATE_PATHS = ("scripts", "pixi.toml", "SKILL.md")
GIT_PATHSPECS = [f"{port.ENTRY_SKILL}/{x}" for x in GATE_PATHS]


# Known bugs in Modular's published skill that break a gate for every model, fixed in our clone (trusted,
# mxconsole's own code). Each: (file under the skills root, the buggy text, the fix, why). Applied right after
# the clone is updated and again after any restore, before the gate hash is taken or compared.
SKILL_PATCHES = [
    ("import-model/scripts/compare_layers.py",
     "            input_ids[0, :pos].tolist(), skip_special_tokens=True",
     "            ids[0, :pos].tolist(), skip_special_tokens=True",
     "the multi-position probe reads `input_ids` where it stored `ids` (NameError in logits_multi)"),
]
# The verify gate asks the parity server for log probabilities, which MAX refuses under its overlap scheduler.
PARITY_SERVE_FLAGS = ["--no-enable-overlap-scheduler"]


def apply_skill_patches(skills_root: Path) -> list[str]:
    """Apply SKILL_PATCHES where the buggy text is present; returns what was patched (idempotent)."""
    done = []
    for rel, old, new, why in SKILL_PATCHES:
        f = skills_root / rel
        try:
            text = f.read_text()
        except OSError:
            continue
        if old in text:
            f.write_text(text.replace(old, new))
            done.append(f"{rel}: {why}")
    return done


def gate_hash(skills_root: Path) -> str:
    """Hash of the files the gates run (the skill's scripts and manifest), so tampering is detectable."""
    h = hashlib.sha256()
    base = skills_root / port.ENTRY_SKILL
    files = []
    for g in GATE_PATHS:
        q = base / g
        files += sorted(f for f in q.rglob("*") if f.is_file() and "__pycache__" not in f.parts) if q.is_dir() else ([q] if q.is_file() else [])
    for f in files:
        h.update(str(f.relative_to(base)).encode() + b"\0" + f.read_bytes() + b"\0")
    return h.hexdigest()


def _group_matches(pgid: int, marker: str) -> bool:
    """Is process group `pgid` still led by the command we started (and not a reused pid)?"""
    if not pgid or not marker:
        return False
    try:
        out = subprocess.run(["ps", "-o", "args=", "-p", str(pgid)], capture_output=True, text=True, timeout=5).stdout
        return os.getpgid(pgid) == pgid and marker in out
    except (OSError, subprocess.SubprocessError):
        return False


_LIVE_MANAGERS: "weakref.WeakSet[PortManager]" = weakref.WeakSet()
_ATEXIT = []


def cleanup_at_exit(manager: "PortManager") -> None:
    """Cancel this manager's jobs when the process exits. One atexit hook for all managers."""
    _LIVE_MANAGERS.add(manager)
    if not _ATEXIT:
        _ATEXIT.append(True)
        atexit.register(lambda: [m.cancel_all(2.0) for m in list(_LIVE_MANAGERS)])


def _kill_tree(proc: subprocess.Popen, grace: float = 5.0) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(grace)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)


def _items(content) -> list[dict]:
    """Content blocks that are dicts; a string or anything else in there is ignored, not a crash."""
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _render_event(line: str) -> list[str]:
    """One stream-json line from claude -> readable log lines (the raw line when it is not JSON)."""
    try:
        ev = json.loads(line)
    except ValueError:
        return [line]
    if not isinstance(ev, dict):
        return [line]
    kind = ev.get("type")
    out = []
    if kind == "assistant":
        for b in _items((ev.get("message") or {}).get("content")):
            if b.get("type") == "text" and str(b.get("text", "")).strip():
                out.append("claude: " + b["text"].strip())
            elif b.get("type") == "tool_use":
                inp = b.get("input") or {}
                what = inp.get("command") or inp.get("file_path") or inp.get("pattern") or inp.get("url") or ""
                out.append(f"claude> {b.get('name')} {str(what)[:300]}".rstrip())
    elif kind == "user":
        for b in _items((ev.get("message") or {}).get("content")):
            if b.get("type") == "tool_result":
                body = b.get("content")
                if isinstance(body, list):
                    body = " ".join(str(x.get("text", "")) for x in body if isinstance(x, dict))
                text = str(body or "").strip().splitlines()
                if text:
                    out.append("  " + text[0][:300] + (f" (+{len(text) - 1} more lines)" if len(text) > 1 else ""))
    elif kind == "result":
        out.append(f"claude finished: {ev.get('subtype', 'done')}" + (f" ({ev.get('result')})" if ev.get("result") and ev.get("is_error") else ""))
    return out


class _Job:
    def __init__(self, id: int, ref: str, name: str | None, max_attempts: int):
        self.id, self.ref, self.name, self.max_attempts = id, ref, name, max_attempts
        self.repo_id: str | None = None
        self.status, self.stage, self.message = "running", "workspace", ""
        self.started_at, self.finished_at = time.time(), None
        self.model: str | None = None
        self.stages: list[dict] = [self._row(n, 1 if n in ATTEMPT_STAGES else None) for n in STAGE_NAMES]
        self.tail: deque[str] = deque(maxlen=TAIL_LINES)
        self.cancel = threading.Event()
        self.proc: subprocess.Popen | None = None
        self.unsandboxed = False
        self.procs: dict[int, tuple[subprocess.Popen, str]] = {}
        self.lock = threading.RLock()
        self.thread: threading.Thread | None = None
        self.dir = mxc_home() / "ports" / str(id)
        self.log_path = self.dir / "port.log"

    @staticmethod
    def _row(name: str, attempt: int | None) -> dict:
        return {"name": name, "status": "pending", "started": None, "ended": None, "detail": "", "attempt": attempt}

    def row(self, name: str, attempt: int | None = None) -> dict:
        for r in reversed(self.stages):
            if r["name"] == name and (attempt is None or r["attempt"] == attempt):
                return r
        raise KeyError(name)

    def view(self) -> dict:
        with self.lock:
            return {"id": self.id, "ref": self.ref, "name": self.name, "repo_id": self.repo_id,
                    "status": self.status, "stage": self.stage, "message": self.message,
                    "started_at": self.started_at, "finished_at": self.finished_at,
                    "max_attempts": self.max_attempts, "stages": [dict(s) for s in self.stages],
                    "log": str(self.log_path), "log_tail": "\n".join(self.tail), "model": self.model}

    def say(self, line: str) -> None:
        line = line.rstrip()
        with self.lock:
            for part in line.splitlines() or [""]:
                self.tail.append(part)
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass


class PortManager:
    """Starts, tracks, cancels and remembers automatic ports. One manager per daemon.

    hub_factory()/catalog_loader() as in PullManager. `claude` is the binary (None: MXC_CLAUDE, then
    PATH). `max_command` is how `max` is run for the smoke test (the supervisor's). `serve(name)` starts
    the registered model (the daemon passes one that talks to its supervisor); None skips that stage.
    `git_runner`/`fetch_config`/`arch_dir` are port.py's injection points.
    """

    def __init__(self, store: Store, *, hub_factory: Callable | None = None, catalog_loader: Callable | None = None,
                 claude: str | None = None, max_command: tuple[str, ...] = ("max",), tools: PortTools | None = None,
                 serve: Callable[[str], None] | None = None, git_runner=None, fetch_config=None,
                 arch_dir: Path | None = None, generate_timeout: float = GENERATE_TIMEOUT,
                 check_timeout: float = CHECK_TIMEOUT, smoke_timeout: float = SMOKE_TIMEOUT,
                 sandbox: bool = True, bwrap: str = "bwrap", claude_home: Path | None = None,
                 serve_timeout: float = SERVE_TIMEOUT):
        self.store = store
        self.hub_factory = hub_factory or (lambda: resolve.HubClient())
        self.catalog_loader = catalog_loader or (lambda: resolve.load_catalog())
        self.claude, self.max_command = claude, tuple(max_command)
        self.tools = tools or PortTools()
        self.serve = serve
        self.git_runner, self.fetch_config, self.arch_dir = git_runner, fetch_config, arch_dir
        self.generate_timeout, self.check_timeout, self.smoke_timeout = generate_timeout, check_timeout, smoke_timeout
        self.sandbox, self.bwrap, self.serve_timeout = sandbox, bwrap, serve_timeout
        self.claude_home = claude_home
        self.db_lock = threading.Lock()
        self.live: dict[int, _Job] = {}
        self.live_lock = threading.Lock()
        with self.db_lock:
            store.db.executescript(SCHEMA)
            cols = {r[1] for r in store.db.execute("PRAGMA table_info(ports)")}
            if "procs" not in cols:
                store.db.execute("ALTER TABLE ports ADD COLUMN procs TEXT")
            # agents and checks a dead daemon left running: stop their process groups, if still ours
            for r in store.db.execute("SELECT procs FROM ports WHERE status='running' AND procs IS NOT NULL").fetchall():
                for pgid, marker in json.loads(r["procs"] or "[]"):
                    if _group_matches(pgid, marker):
                        with contextlib.suppress(ProcessLookupError, PermissionError):
                            os.killpg(pgid, signal.SIGKILL)
            store.db.execute("UPDATE ports SET procs=NULL")
            for r in store.db.execute("SELECT id FROM ports WHERE status='running'").fetchall():
                shutil.rmtree(mxc_home() / "ports" / str(r["id"]) / "work", ignore_errors=True)  # private caches
            store.db.execute("UPDATE ports SET status='failed', finished_at=?, "
                             "message='interrupted: the daemon stopped during this port. Run it again.' "
                             "WHERE status='running'", (time.time(),))

    # -- public ----------------------------------------------------------------------------------
    def start(self, ref: str, name: str | None = None, max_attempts: int = DEFAULT_ATTEMPTS, *,
              unsandboxed: bool = False) -> dict:
        ref = (ref or "").strip()
        if not REF_RE.match(ref):
            raise BadPull("Send a short name or a Hugging Face id like owner/repo.")
        if name is not None and not NAME_RE.match(name):
            raise BadPull("`name` may use letters, digits, '.', '_' and '-' (64 characters at most).")
        if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or not 1 <= max_attempts <= MAX_ATTEMPTS:
            raise BadPull(f"`max_attempts` must be a whole number from 1 to {MAX_ATTEMPTS}.")
        if self.sandbox and not unsandboxed and port_sandbox.have_bwrap(self.bwrap) is None:
            raise PortUnsafe(NO_SANDBOX)
        key = resolve.CURATED.get(ref.lower(), ref).lower()
        name = name or resolve.model_name(ref, resolve.CURATED.get(ref.lower(), ref))
        with self.live_lock:
            for j in self.live.values():  # the same repo, or two repos that would register the same name
                if j.status == "running" and (resolve.CURATED.get(j.ref.lower(), j.ref).lower() == key or j.name == name):
                    raise PortBusy(j.view())
            with self.db_lock:
                cur = self.store.db.execute("INSERT INTO ports (ref, name, status, stage, started_at, max_attempts) "
                                            "VALUES (?, ?, 'running', 'workspace', ?, ?)",
                                            (ref, name, time.time(), max_attempts))
            job = _Job(cur.lastrowid, ref, name, max_attempts)
            job.unsandboxed = unsandboxed
            self.live[job.id] = job
        job.thread = threading.Thread(target=self._run, args=(job,), name=f"port-{job.id}", daemon=True)
        job.thread.start()
        return job.view()

    def get(self, port_id: int) -> dict | None:
        job = self.live.get(port_id)
        if job:
            return job.view()
        with self.db_lock:
            row = self.store.db.execute("SELECT * FROM ports WHERE id = ?", (port_id,)).fetchone()
        return _row_view(row) if row else None

    def list(self, limit: int = 20) -> list[dict]:
        with self.db_lock:
            rows = self.store.db.execute("SELECT * FROM ports ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [self.live[r["id"]].view() if r["id"] in self.live else _row_view(r) for r in rows]

    def cancel(self, port_id: int) -> dict | None:
        job = self.live.get(port_id)
        if job is None:
            return self.get(port_id)
        job.cancel.set()
        with job.lock:
            procs = [p for p, _ in job.procs.values()]
        for proc in procs:
            _kill_tree(proc, grace=2.0)
        return job.view()

    def wait(self, port_id: int, timeout: float = 30.0) -> dict | None:
        job = self.live.get(port_id)
        if job and job.thread:
            job.thread.join(timeout)
        return self.get(port_id)

    def cancel_all(self, timeout: float = 5.0) -> None:
        ids = list(self.live)
        for i in ids:
            self.cancel(i)
        for i in ids:
            j = self.live.get(i)
            if j and j.thread:
                j.thread.join(timeout)

    # -- stage bookkeeping -----------------------------------------------------------------------
    def _begin(self, job: _Job, name: str, attempt: int | None = None, detail: str = "") -> dict:
        self._check(job)
        with job.lock:
            row = job.row(name, attempt)
            row.update(status="running", started=time.time(), detail=detail or row["detail"])
            job.stage = name
        self._save(job)
        job.say(f"== {name}" + (f" (attempt {attempt})" if attempt else "") + (f": {detail}" if detail else ""))
        return row

    def _end(self, job: _Job, row: dict, status: str = "done", detail: str | None = None) -> None:
        with job.lock:
            row.update(status=status, ended=time.time())
            if detail is not None:
                row["detail"] = detail
        self._save(job)
        job.say(f"-- {row['name']} {status}" + (f": {row['detail']}" if row["detail"] else ""))

    def _detail(self, job: _Job, row: dict, detail: str) -> None:
        with job.lock:
            row["detail"] = detail

    def _check(self, job: _Job) -> None:
        if job.cancel.is_set():
            raise _Cancelled()

    def _add_attempt(self, job: _Job, attempt: int) -> None:
        with job.lock:
            for r in job.stages:  # what the failed attempt never reached
                if r["status"] == "pending" and r["name"] in ATTEMPT_STAGES:
                    r["status"] = "skipped"
            at = next(i for i, r in enumerate(job.stages) if r["name"] == "register")
            job.stages[at:at] = [job._row(n, attempt) for n in ATTEMPT_STAGES]

    def _skip_rest(self, job: _Job, from_name: str) -> None:
        """Mark pending rows from the failing one on as skipped, so the list reads true."""
        with job.lock:
            for r in job.stages:
                if r["status"] == "pending":
                    r["status"] = "skipped"

    # -- running things --------------------------------------------------------------------------
    def _creds_src(self) -> Path:
        return (self.claude_home or Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")) / ".credentials.json"

    def _confine(self, job: _Job, argv: list[str], role: str, ctx: Ctx | None,
                 cwd: Path | None = None) -> tuple[list[str], dict, str]:
        """(argv, env, note) for running `argv` as role "agent", "check" (the skill's gates), "serve" (the
        smoke `max serve`) or "install" (trusted, pristine code, never confined). The environment is
        scrubbed of secrets (claude keeps only its own auth). Every cache a sandboxed process writes to
        (pixi, rattler, uv, Hugging Face, MAX) lives in the job's private `work/` dir, so nothing shared
        and later used outside the sandbox is ever writable from inside it. The skill env is installed
        beforehand, outside; the clone and the model weights are bound read-only."""
        keep = port_sandbox.CLAUDE_AUTH if role == "agent" else ()
        env = port_sandbox.scrub_env(dict(os.environ), keep)
        if role == "install":
            return argv, env, "trusted install of the pristine skill env, not sandboxed"
        work = job.dir / "work"
        cache = work / "cache"
        for d in (cache / "pixi", cache / "rattler", cache / "uv", cache / "xdg", cache / "modular", work / "hf" / "hub"):
            d.mkdir(parents=True, exist_ok=True)
        env.update(PIXI_CACHE_DIR=str(cache / "pixi"), RATTLER_CACHE_DIR=str(cache / "rattler"),
                   UV_CACHE_DIR=str(cache / "uv"), XDG_CACHE_HOME=str(cache / "xdg"),
                   MODULAR_DERIVED_PATH=str(cache / "modular"), HF_HOME=str(work / "hf"),
                   HF_HUB_CACHE=str(work / "hf" / "hub"))
        # Not offline: the verify gate reads safetensors metadata from the Hub (no offline mode for that). The
        # model's own files are cache hits from the read-only bind below; the HF token is scrubbed.
        env.pop("HF_HUB_OFFLINE", None)
        binds: list[tuple[Path, Path]] = []
        if role == "agent":
            cfg = work / "claude-cfg"
            cfg.mkdir(parents=True, exist_ok=True)
            env["CLAUDE_CONFIG_DIR"] = str(cfg)
            creds = self._creds_src()
            if creds.is_file():  # the one real login file, not a copy: a token refresh writes back to it
                binds.append((creds, cfg / ".credentials.json"))
        bw = port_sandbox.have_bwrap(self.bwrap) if self.sandbox and not job.unsandboxed else None
        if bw is None:
            why = "sandbox disabled" if not self.sandbox else "bubblewrap (bwrap) not found; --unsandboxed was given"
            for src, dst in binds:  # no mount namespace: link the login into the private config dir instead
                with contextlib.suppress(OSError):
                    dst.unlink(missing_ok=True)
                    dst.symlink_to(src)
            return argv, env, f"NOT sandboxed ({why})" + ("; using Claude Code's own sandbox setting" if role == "agent" and self.sandbox else "")
        home = Path.home()
        ws = ctx.workspace if ctx else job.dir
        ro = [*port_sandbox.tool_roots(argv[0], "pixi", "git")]
        ro_after: list[Path] = []
        if ctx:
            ro.append(ctx.skills_root)  # the pristine clone, with its pre-installed env: read-only everywhere
        if role == "agent":
            # claude reads project settings from the workspace; the agent must not be able to widen its own
            # permissions there, so those files are bound read-only on top of the writable workspace
            for name in ("settings.json", "settings.local.json"):
                f = ws / ".claude" / name
                f.parent.mkdir(parents=True, exist_ok=True)
                if not f.exists():
                    f.write_text("{}\n")
                ro_after.append(f)
            rw = [ws, work]
            ro += self._weights_roots(ctx)  # so the agent's own check_port.py runs use the local weights too
        else:
            ro.append(ws)
            ro += self._weights_roots(ctx)
            rw = [work]
        gpu = True  # MAX picks its device by what it can see; without the GPU, bf16 weights fail on CPU
        ro_binds = []
        for repo in self._weights_roots(ctx):  # this model only, in the job's private HF cache, read-only
            dst = work / "hf" / "hub" / repo.name
            dst.mkdir(parents=True, exist_ok=True)
            ro_binds.append((repo, dst))
        wrapped = port_sandbox.bwrap_argv(argv, bwrap=bw, cwd=Path(cwd or ws), home=home, mxc_home=mxc_home(),
                                          rw=rw, ro=ro, ro_after=ro_after, binds=binds, ro_binds=ro_binds, gpu=gpu)
        return wrapped, env, "sandboxed with bubblewrap (read-only system and skills, empty $HOME and MXC_HOME, " \
            "private /tmp and caches, writable: " + ", ".join(str(x) for x in rw) + ")"

    def _weights_roots(self, ctx: Ctx | None) -> list[Path]:
        """The downloaded model, read-only inside the sandbox: its whole Hugging Face repo dir (the snapshot
        files are symlinks into the sibling blobs/ dir), not the cache around it."""
        w = getattr(ctx, "weights", None) if ctx else None
        if not w:
            return []
        w = Path(w)
        repo = next((a for a in [w, *w.parents] if a.name.startswith("models--")), w)
        return [repo]

    def _track(self, job: _Job, proc: subprocess.Popen, marker: str, add: bool = True) -> None:
        """Remember the process groups a job has running, in memory (for cancel) and in the database (so a
        restarted daemon can stop what a dead one left behind)."""
        with job.lock:
            if add:
                job.procs[proc.pid] = (proc, marker)
            else:
                job.procs.pop(proc.pid, None)
            job.proc = next(iter(job.procs.values()))[0] if job.procs else None
            listing = json.dumps([[pid, m] for pid, (_, m) in job.procs.items()]) if job.procs else None
        try:
            with self.db_lock:
                self.store.db.execute("UPDATE ports SET procs=? WHERE id=?", (listing, job.id))
        except Exception:
            log.exception("could not record the process groups of port %s", job.id)

    def _exec(self, job: _Job, argv: list[str], cwd: Path, *, timeout: float, render=None, role: str = "check",
              ctx: Ctx | None = None) -> tuple[int, str]:
        """Run a command in its own process group (confined, with a scrubbed environment), stream every
        line to the log, and return (exit code, the last of its output). Cancel and the timeout kill the
        whole group."""
        self._check(job)
        run_argv, env, note = self._confine(job, argv, role, ctx, cwd)  # inside the sandbox too (pixi needs it)
        job.say(f"[{role}] {note}")
        job.say("$ " + " ".join(argv if len(" ".join(argv)) < 600 else argv[:3] + ["..."]) + f"   (cwd {cwd})")
        try:
            proc = subprocess.Popen(run_argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    text=True, errors="replace", start_new_session=True, env=env)
        except OSError as e:
            job.say(f"could not run {run_argv[0]}: {e}")
            return 127, f"could not run {run_argv[0]}: {e}"
        self._track(job, proc, run_argv[0])
        if job.cancel.is_set():  # cancel() ran before the process was registered: it could not kill it
            _kill_tree(proc, grace=2.0)
        timed_out = threading.Event()

        def expire():
            timed_out.set()
            _kill_tree(proc, grace=2.0)
        timer = threading.Timer(timeout, expire)
        timer.daemon = True
        timer.start()
        captured: deque[str] = deque(maxlen=400)
        try:
            for line in proc.stdout:
                line = line.rstrip("\n")
                captured.append(line)
                for out in (render(line) if render else [line]):
                    job.say(out)
                if job.cancel.is_set():
                    _kill_tree(proc, grace=2.0)
            proc.wait()
        finally:
            timer.cancel()
            self._track(job, proc, run_argv[0], add=False)
            if proc.poll() is None:
                _kill_tree(proc)
        self._check(job)
        if timed_out.is_set():
            job.say(f"timed out after {timeout:g}s")
            captured.append(f"(timed out after {timeout:g}s)")
            return 124, "\n".join(captured)
        return proc.returncode, "\n".join(captured)

    def _pristine(self, job: _Job, ctx: Ctx) -> None:
        """Before a gate runs: the skill's check scripts must be exactly what was cloned. If the clone was
        touched, restore it from git; if it still differs, the gate does not count and the job fails."""
        runner = self.git_runner or port._run
        if gate_hash(ctx.skills_root) == ctx.gate_hash:  # the hash covers every gate file (our patches included)
            return
        job.say("the skill's gate scripts were modified; restoring them from git")
        runner(["git", "checkout", "--", *GIT_PATHSPECS], cwd=ctx.skills_root)
        runner(["git", "clean", "-fd", "--", *GIT_PATHSPECS], cwd=ctx.skills_root)
        apply_skill_patches(ctx.skills_root)
        if gate_hash(ctx.skills_root) != ctx.gate_hash:
            raise _StageFailed(f"the skill's check scripts in {ctx.skills_root} were modified and could not be restored; "
                               f"the build and parity checks cannot be trusted")

    # -- the pipeline ----------------------------------------------------------------------------
    def _run(self, job: _Job) -> None:
        try:
            self._pipeline(job)
        except _Cancelled:
            self._skip_rest(job, "")
            with job.lock:
                for r in job.stages:
                    if r["status"] == "running":
                        r.update(status="cancelled", ended=time.time())
            self._finish(job, "cancelled", "Cancelled. The agent and its commands were stopped; the workspace is kept.")
        except _StageFailed as e:
            self._fail_stage(job, str(e))
        except (port.PortError, resolve.ResolveError) as e:
            self._fail_stage(job, str(e))
        except resolve.PullError as e:
            self._fail_stage(job, e.resolution.message)
        except Exception as e:
            log.exception("port %s crashed", job.id)
            self._fail_stage(job, f"unexpected error: {type(e).__name__}: {str(e)[:160]}")

    def _fail_stage(self, job: _Job, message: str) -> None:
        with job.lock:
            for r in job.stages:
                if r["status"] == "running":
                    r.update(status="failed", ended=time.time(), detail=message[:300])
        self._skip_rest(job, "")
        self._finish(job, "failed", message)

    def _pipeline(self, job: _Job) -> None:
        hub = self.hub_factory()
        # workspace
        row = self._begin(job, "workspace", detail=f"looking up {job.ref}…")
        catalog = self.catalog_loader()
        res = resolve.resolve(job.ref, catalog=catalog, hf=hub)
        job.repo_id = res.repo_id
        if res.supported:
            raise _StageFailed(f"{res.repo_id} ({res.architecture}) is already supported by MAX: run `mxc pull {job.ref}` instead.")
        if res.kind != "unsupported":
            raise _StageFailed(res.message)
        name = job.name or resolve.model_name(job.ref, res.repo_id)
        job.name = name
        resolve.check_name_free(self.store, name, job.ref, res)
        workspace, config, donor = port.make_workspace(job.ref, res, catalog, fetch_config=self.fetch_config,
                                                       arch_dir=self.arch_dir)
        donor_note = (f"Closest supported architecture in MAX (donor): {donor.architecture}"
                      + (f" ({donor.slug})" if donor.slug else "") + f". {donor.reason}.")
        self._end(job, row, detail=f"{res.architecture}, donor {donor.architecture}, workspace {workspace}")
        # skills
        row = self._begin(job, "skills", detail="installing Modular's skills…")
        clone = mxc_home() / "skills" / "modular"
        if (clone / ".git").exists():  # drop our own patches first, or `git pull --ff-only` refuses to update
            (self.git_runner or port._run)(["git", "checkout", "--", *GIT_PATHSPECS], cwd=clone)
        skills_root = port.ensure_skills(runner=self.git_runner)
        if port.ENTRY_SKILL not in port.list_skills(skills_root):
            raise _StageFailed(f"Modular's skills in {skills_root} have no {port.ENTRY_SKILL} skill")
        for note in apply_skill_patches(skills_root):
            job.say(f"-- patched a known bug in Modular's skill: {note}")
        skills = port.finish_workspace(job.ref, res, workspace, config, donor, skills_root)
        allowed = tuple(f"Bash(cp -r {d}/:*)" for d in (donor.source_dir, donor.v3_dir) if d)
        ctx = Ctx(job.ref, res.repo_id, name, workspace, workspace / "port" / package_slug(name), skills_root,
                  gate_hash=gate_hash(skills_root), extra_allowed=allowed, serving_max=port.mst_max_version() or "")
        cmd = self.tools.install_cmd(ctx)
        if cmd is not None:
            self._detail(job, row, "installing the skill's environment (outside the sandbox; the first time takes a while)…")
            code, out = self._exec(job, cmd[0], cmd[1], timeout=INSTALL_TIMEOUT, role="install", ctx=ctx)
            if code != 0:
                raise _StageFailed(f"could not install the skill's pixi environment (exit {code}): {out[-300:]}")
        self._detail(job, row, f"downloading the weights of {res.repo_id} (once, outside the sandbox)…")
        job.say(f"-- downloading the weights of {res.repo_id} (once, outside the sandbox)…")
        try:
            ctx.weights = str(resolve.download_snapshot(res, hub, cancel=job.cancel))
        except resolve.PullCancelled:
            raise _Cancelled()
        self._end(job, row, detail=f"{len(skills)} skills in {skills_root}, read-only for the agent and the gates; "
                                   f"weights at {ctx.weights}")

        feedback: str | None = None
        for attempt in range(1, job.max_attempts + 1):
            if attempt > 1:
                self._add_attempt(job, attempt)
            err = self._attempt(job, ctx, attempt, feedback, donor_note, hub, res)
            if err is None:
                break
            feedback = err.output[-FEEDBACK_CHARS:] or str(err)
            if not err.retry or attempt == job.max_attempts:
                tried = f" after {attempt} attempt{'s' if attempt != 1 else ''}" if err.retry else ""
                raise _StageFailed(f"{err}{tried}. Full log: {job.log_path}")
            job.say(f"attempt {attempt} failed: {err}. Trying again with the error fed back.")
        # register
        row = self._begin(job, "register", detail=f"adding {name} (port {ctx.port_dir})…")
        model = port.register(job.ref, ctx.port_dir, store=self.store, catalog=catalog, hf=hub, name=name)
        job.model = model.name
        self._end(job, row, detail=f"registered {model.name} with custom architecture {model.custom_arch_path}")
        # serve
        row = self._begin(job, "serve", detail=f"starting {model.name}…")
        if self.serve is None:
            self._end(job, row, "skipped", "no server attached; the model starts on its first request")
        else:
            err = self._serve(job, model.name)
            if err:
                raise _StageFailed(f"{model.name} is registered but did not start: {err[-500:]}")
            self._end(job, row, detail=f"{model.name} is up")
        self._finish(job, "done", f"Ported {model.name} ({model.repo_id}@{model.revision[:8]}) and serving it.")

    def _serve(self, job: _Job, name: str) -> str | None:
        """Start the model on a helper thread, polling for cancel and a deadline. Returns an error or None."""
        box: dict = {}

        def go():
            try:
                self.serve(name)
            except Exception as e:  # reported to the job, not raised in a helper thread
                box["err"] = str(e) or type(e).__name__
        t = threading.Thread(target=go, name=f"port-{job.id}-serve", daemon=True)
        t.start()
        deadline = time.monotonic() + self.serve_timeout
        while t.is_alive():
            self._check(job)
            if time.monotonic() > deadline:
                return f"it did not start within {self.serve_timeout:g}s"
            t.join(0.2)
        return box.get("err")

    def _attempt(self, job: _Job, ctx: Ctx, attempt: int, feedback: str | None, donor_note: str, hub, res) -> _StageFailed | None:
        """generate, build, parity once. Returns None on success, or the failure (retryable or not)."""
        # generate
        row = self._begin(job, "generate", attempt, detail="running Claude Code headless…" if attempt == 1 else "fixing…")
        claude = find_claude(self.claude)
        if claude is None:
            msg = NO_CLAUDE.format(ref=job.ref)
            self._end(job, row, "failed", msg)
            raise _StageFailed(msg)
        fallback = self.sandbox and port_sandbox.have_bwrap(self.bwrap) is None
        argv = claude_argv(claude, build_prompt(ctx, attempt, feedback, donor_note), ctx.extra_allowed, fallback_sandbox=fallback)
        code, out = self._exec(job, argv, ctx.workspace, timeout=self.generate_timeout, render=_render_event,
                               role="agent", ctx=ctx)
        if code != 0:
            msg = f"claude exited with code {code}" + (" (timed out)" if code == 124 else "")
            self._end(job, row, "failed", msg)
            return _StageFailed(msg, retry=code != 127, output=f"{msg}. Its last output:\n{out}")
        self._end(job, row, detail=f"claude finished; package at {ctx.port_dir}")
        # build
        row = self._begin(job, "build", attempt, detail="checking the package…")
        try:
            port.check_port_dir(ctx.port_dir)
        except port.PortError as e:
            self._end(job, row, "failed", str(e))
            return _StageFailed(f"build failed: {e}", retry=True, output=str(e))
        self._pristine(job, ctx)
        cmd = self.tools.build_cmd(ctx)
        if cmd is None:
            self._end(job, row, detail="package layout is valid (the skill has no check_port.py to run)")
        else:
            code, out = self._exec(job, cmd[0], cmd[1], timeout=self.check_timeout, role="check", ctx=ctx)
            if code != 0:
                msg = f"the build check failed (exit {code})"
                self._end(job, row, "failed", msg)
                return _StageFailed(f"build failed: {msg}", retry=True, output=f"{' '.join(cmd[0])}\n{out}")
            self._end(job, row, detail="the package imports and every weight maps")
        # parity
        row = self._begin(job, "parity", attempt, detail="downloading weights…")
        failure = self._parity(job, ctx, row, hub, res)
        if failure:
            self._end(job, row, "failed", failure.args[0][:300])
            return failure
        self._end(job, row)
        return None

    def _parity(self, job: _Job, ctx: Ctx, row: dict, hub, res) -> _StageFailed | None:
        if not ctx.weights:  # downloaded in the skills stage; this covers a manager driven another way
            try:
                ctx.weights = str(resolve.download_snapshot(res, hub, cancel=job.cancel))
            except resolve.PullCancelled:
                raise _Cancelled()
        weights = ctx.weights
        self._check(job)
        port_ = _free_port()
        self._detail(job, row, f"compiling and serving on :{port_} (a cold compile takes 5 to 25 minutes)…")
        argv = [*self.max_command, "serve", *supervisor.launch_flags(), *PARITY_SERVE_FLAGS, "--port", str(port_), "--served-model-name", ctx.repo_id,
                "--custom-architectures", str(ctx.port_dir), "--model-path", str(weights)]
        run_argv, env, note = self._confine(job, argv, "serve", ctx)
        job.say(f"[serve] {note}")
        job.say("$ " + " ".join(argv))
        log_file = open(job.log_path, "ab")
        try:
            proc = subprocess.Popen(run_argv, cwd=ctx.workspace, stdout=log_file, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, start_new_session=True,
                                    env={**env, "MAX_SERVE_HOST": "127.0.0.1"})
        except OSError as e:
            return _StageFailed(f"could not run {' '.join(self.max_command)}: {e}", retry=False)
        finally:
            log_file.close()
        self._track(job, proc, run_argv[0])
        try:
            self._check(job)  # a cancel that landed before the server was registered
            problem = self._smoke(job, proc, port_, ctx, row)
            if problem:
                return problem
            vc = self.tools.verify_cmd(ctx, port_)
            if vc is None:
                self._detail(job, row, "smoke generation passed (the skill has no verify gate to run)")
                return None
            self._detail(job, row, "smoke passed; comparing with the Hugging Face reference…")
            self._pristine(job, ctx)
            code, out = self._exec(job, vc[0], vc[1], timeout=self.check_timeout, role="check", ctx=ctx)
            if code != 0:
                return _StageFailed(f"parity check failed (exit {code}): the port's output does not match Hugging Face",
                                    retry=True, output=f"{' '.join(vc[0])}\n{out}")
            self._detail(job, row, "matches the Hugging Face reference")
            return None
        finally:
            self._track(job, proc, run_argv[0], add=False)
            _kill_tree(proc)

    def _smoke(self, job: _Job, proc: subprocess.Popen, port_: int, ctx: Ctx, row: dict) -> _StageFailed | None:
        """Wait for max serve to be healthy, then ask for a few greedy tokens."""
        deadline = time.monotonic() + self.smoke_timeout
        base = f"http://127.0.0.1:{port_}"
        with httpx.Client(timeout=5.0) as http:
            while True:
                self._check(job)
                if proc.poll() is not None:
                    return _StageFailed(f"max serve exited with code {proc.returncode} while loading the port",
                                        retry=True, output=_log_tail(job, "max serve exited while loading the port"))
                if time.monotonic() > deadline:
                    return _StageFailed(f"max serve was not healthy after {self.smoke_timeout:g}s", retry=True,
                                        output=_log_tail(job, "max serve never became healthy"))
                try:
                    if http.get(base + "/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.5)
            self._detail(job, row, "serving; sending a prompt…")
            try:
                r = http.post(base + "/v1/completions", timeout=300.0, json={
                    "model": ctx.repo_id, "prompt": SMOKE_PROMPT, "max_tokens": 16, "temperature": 0.0})
                text = ((r.json().get("choices") or [{}])[0].get("text") or "") if r.status_code == 200 else ""
            except (httpx.HTTPError, ValueError) as e:
                return _StageFailed(f"smoke generation failed: {e}", retry=True, output=_log_tail(job, f"request failed: {e}"))
        job.say(f"smoke: {SMOKE_PROMPT!r} -> {text!r}")
        if not text.strip():
            return _StageFailed("smoke generation returned no text", retry=True,
                                output=_log_tail(job, f"HTTP {r.status_code}: {r.text[:500]}"))
        return None

    # -- finishing -------------------------------------------------------------------------------
    def _finish(self, job: _Job, status: str, message: str) -> None:
        shutil.rmtree(job.dir / "work", ignore_errors=True)  # private caches and the Claude config dir (never the login itself)
        with job.lock:
            job.status, job.message, job.finished_at = status, message, time.time()
        job.say(f"== {status}: {message}")
        self._save(job)
        with self.live_lock:
            self.live.pop(job.id, None)
        with self.db_lock:
            self.store.db.execute("DELETE FROM ports WHERE id <= (SELECT MAX(id) FROM ports) - ? AND status != 'running'",
                                  (KEEP_ROWS,))

    def _save(self, job: _Job) -> None:
        v = job.view()
        try:
            with self.db_lock:
                self.store.db.execute(
                    "UPDATE ports SET name=?, repo_id=?, status=?, stage=?, message=?, finished_at=?, stages=?, model=? WHERE id=?",
                    (v["name"], v["repo_id"], v["status"], v["stage"], v["message"], v["finished_at"],
                     json.dumps(v["stages"]), v["model"], job.id))
        except Exception:
            log.exception("could not save port %s", job.id)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _log_tail(job: _Job, heading: str, lines: int = 60) -> str:
    try:
        tail = job.log_path.read_text(errors="replace").splitlines()[-lines:]
    except OSError:
        tail = []
    return heading + "\n" + "\n".join(tail)


def _row_view(row) -> dict:
    job = _Job(row["id"], row["ref"], row["name"], row["max_attempts"])
    job.repo_id, job.status, job.stage, job.message = row["repo_id"], row["status"], row["stage"], row["message"]
    job.started_at, job.finished_at, job.model = row["started_at"], row["finished_at"], row["model"]
    job.stages = json.loads(row["stages"] or "[]")
    try:
        job.tail.extend(job.log_path.read_text(errors="replace").splitlines()[-TAIL_LINES:])
    except OSError:
        pass
    return job.view()
