"""The automatic port treats config.json and the model card as untrusted: sandbox, scrubbed environment,
pristine gates, cancel races, orphans."""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from fake_hub import CHUNK, SlowHub
from fakes import CATALOG
from test_port import Runner, fake_fetch, fake_skills
from test_port_auto import (HERE, STAGES, calls, check_tools, fake_claude, make_manager, rows,  # noqa: F401
                            run)

from mxconsole import port_auto, port_sandbox
from mxconsole.port_auto import PortBusy, PortManager, _Job
from mxconsole.port_sandbox import bwrap_argv, scrub_env
from mxconsole.store import Store, mxc_home


# ---- pure pieces ------------------------------------------------------------------------------

def test_bwrap_argv_exact():
    argv = bwrap_argv(["claude", "-p", "x"], bwrap="/usr/bin/bwrap", cwd=Path("/w"), home=Path("/home/me"),
                      mxc_home=Path("/home/me/.mxc"), rw=[Path("/w"), Path("/j")], ro=[Path("/opt/claude")])
    assert argv == ["/usr/bin/bwrap", "--die-with-parent", "--new-session", "--unshare-pid", "--unshare-ipc",
                    "--unshare-uts", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp",
                    "--tmpfs", "/run/user", "--tmpfs", "/home/me", "--tmpfs", "/home/me/.mxc",
                    "--ro-bind-try", "/opt/claude", "/opt/claude", "--bind-try", "/w", "/w", "--bind-try", "/j", "/j",
                    "--chdir", "/w", "--", "claude", "-p", "x"]
    assert "--unshare-net" not in argv  # claude and pixi need the network


def test_bwrap_argv_gpu_gets_real_dev():
    argv = bwrap_argv(["max"], cwd=Path("/w"), home=Path("/h"), mxc_home=Path("/h/.mxc"), gpu=True)
    assert argv[argv.index("--dev-bind") + 1:argv.index("--dev-bind") + 3] == ["/dev", "/dev"] and "--dev" not in argv


def test_scrub_env_drops_secrets_but_keeps_what_claude_needs():
    env = {"PATH": "/usr/bin", "HOME": "/h", "MXC_API_KEY": "a", "HF_TOKEN": "b", "HUGGING_FACE_HUB_TOKEN": "c",
           "MY_SECRET": "d", "GITHUB_TOKEN": "e", "SSH_AUTH_SOCK": "/s", "ANTHROPIC_API_KEY": "f", "LANG": "C"}
    assert scrub_env(env) == {"PATH": "/usr/bin", "HOME": "/h", "LANG": "C"}
    assert scrub_env(env, port_sandbox.CLAUDE_AUTH) == {"PATH": "/usr/bin", "HOME": "/h", "LANG": "C", "ANTHROPIC_API_KEY": "f"}


def test_render_event_tolerates_odd_content():
    assert port_auto._render_event(json.dumps({"type": "assistant", "message": {"content": ["just text", 3, None,
                                   {"type": "text", "text": "hi"}]}})) == ["claude: hi"]
    assert port_auto._render_event(json.dumps({"type": "user", "message": {"content": "plain"}})) == []
    assert port_auto._render_event(json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "content": ["x", {"text": "ok"}]}]}})) == ["  ok"]
    assert port_auto._render_event("[1, 2]") == ["[1, 2]"]


@pytest.mark.skipif(not shutil.which("bwrap") or not sys.platform.startswith("linux"), reason="needs bubblewrap")
def test_real_bwrap_confines_the_command(tmp_path):
    rw, other = tmp_path / "rw", tmp_path / "other"
    rw.mkdir()
    other.mkdir()
    (rw / "locked").write_text("x")
    script = (f"touch {rw}/ok && echo wrote-rw; (echo y > {rw}/locked) 2>/dev/null || echo locked-ro; touch {other}/x; touch /usr/nope 2>/dev/null || echo ro-system; "
              f"test -z \"$(ls -A $HOME)\" && echo home-empty; test -d {mxc_home().parent} || true")
    argv = bwrap_argv(["sh", "-c", script], cwd=rw, home=Path.home(), mxc_home=mxc_home(), rw=[rw], ro_after=[rw / "locked"])
    out = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    assert "wrote-rw" in out.stdout and "ro-system" in out.stdout and "home-empty" in out.stdout, out
    assert "locked-ro" in out.stdout
    assert (rw / "ok").exists()          # the writable bind is the real directory
    assert not (other / "x").exists()    # everything else is a throwaway tmpfs


# ---- the job uses them ------------------------------------------------------------------------

def test_environment_is_scrubbed_and_claude_gets_a_private_config(tmp_path, fake_claude, monkeypatch):
    home = tmp_path / "claude-home"
    home.mkdir()
    (home / ".credentials.json").write_text('{"login": "x"}')
    for k, v in (("MXC_API_KEY", "1"), ("HF_TOKEN", "2"), ("SOME_SECRET", "3"), ("SSH_AUTH_SOCK", "/x"),
                 ("ANTHROPIC_API_KEY", "4")):
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("FAKE_CHECK_ENV_LOG", str(tmp_path / "check-env.log"))
    job = run(make_manager(tmp_path, fake_claude))
    assert job["status"] == "done", job["message"]
    (call,) = calls(tmp_path)
    for k in ("MXC_API_KEY", "HF_TOKEN", "SOME_SECRET", "SSH_AUTH_SOCK"):
        assert k not in call["env"]
    assert "ANTHROPIC_API_KEY" in call["env"]
    assert call["config_dir"] == str(mxc_home() / "ports" / str(job["id"]) / "work" / "claude-cfg")
    assert call["creds"] == '{"login": "x"}'  # reached through a link to the one real login, not a copy
    assert not (mxc_home() / "ports" / str(job["id"]) / "work").exists()
    assert (home / ".credentials.json").read_text() == '{"login": "x"}'
    checks = [json.loads(line) for line in (tmp_path / "check-env.log").read_text().splitlines()]
    assert {c["phase"] for c in checks} == {"build", "verify"}
    for c in checks:
        assert not {"MXC_API_KEY", "HF_TOKEN", "SOME_SECRET", "SSH_AUTH_SOCK", "ANTHROPIC_API_KEY"} & set(c["env"])


@pytest.fixture
def fake_bwrap(tmp_path, monkeypatch):
    script = tmp_path / "bin" / "bwrap"
    script.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{HERE / "fake_bwrap.py"}" "$@"\n')
    script.chmod(0o755)
    monkeypatch.setenv("FAKE_BWRAP_LOG", str(tmp_path / "bwrap.log"))
    return str(script)


def bind_pairs(argv, flag):
    return [(argv[i + 1], argv[i + 2]) for i, a in enumerate(argv) if a == flag]


def test_agent_checks_and_server_run_inside_bwrap_with_nothing_shared_writable(tmp_path, fake_claude, fake_bwrap):
    if not sys.platform.startswith("linux"):
        pytest.skip("bubblewrap is Linux only")
    home = tmp_path / "claude-home"
    home.mkdir()
    (home / ".credentials.json").write_text('{"login": "x"}')
    m = make_manager(tmp_path, fake_claude, sandbox=True, bwrap=fake_bwrap)
    job = run(m)
    assert job["status"] == "done", job["message"]
    runs = [json.loads(line) for line in (tmp_path / "bwrap.log").read_text().splitlines()]
    ws = mxc_home() / "ports" / "weird"
    work = mxc_home() / "ports" / str(job["id"]) / "work"
    clone = mxc_home() / "skills" / "modular"
    agent = next(r for r in runs if r["cmd"][0] == fake_claude)
    checks = [r for r in runs if "fake_port_check.py" in " ".join(r["cmd"])]
    server = next(r for r in runs if "serve" in r["cmd"])
    assert len(checks) == 2
    assert agent["bwrap"][:9] == ["--die-with-parent", "--new-session", "--unshare-pid", "--unshare-ipc",
                                  "--unshare-uts", "--ro-bind", "/", "/", "--dev-bind"]
    allowed_rw = {"agent": {str(ws), str(work)}, "check": {str(work)}, "serve": {str(work)}}
    for role, r in (("agent", agent), ("check", checks[0]), ("check", checks[1]), ("serve", server)):
        rw = {src for src, dst in bind_pairs(r["bwrap"], "--bind-try") if src == dst}
        assert rw == allowed_rw[role], (role, rw)          # no shared cache or home path is ever writable
        assert str(clone) in [src for src, _ in bind_pairs(r["bwrap"], "--ro-bind-try")], role  # pristine skills, read-only
    assert str(ws) in [src for src, _ in bind_pairs(checks[0]["bwrap"], "--ro-bind-try")]
    ws_at = agent["bwrap"].index(str(ws))
    for name in ("settings.json", "settings.local.json"):  # it cannot widen its own permissions either
        f = str(ws / ".claude" / name)
        i = agent["bwrap"].index(f)
        assert i > ws_at and agent["bwrap"][i - 1] == "--ro-bind-try"
    assert all("--dev-bind" in r["bwrap"] for r in (server, agent, *checks))  # MAX needs the GPU to load bf16
    log = Path(job["log"]).read_text()
    assert "[agent] sandboxed with bubblewrap" in log and "[serve] sandboxed with bubblewrap" in log
    assert not str(Path(job["log"])).startswith(str(work))   # port.log is outside everything the agent can write
    assert not work.exists()                                 # private caches and the claude config are deleted at the end


def test_credentials_are_bound_not_copied(tmp_path, fake_claude, fake_bwrap):
    home = tmp_path / "claude-home"
    home.mkdir()
    real = home / ".credentials.json"
    real.write_text('{"login": "x"}')
    job = run(make_manager(tmp_path, fake_claude, sandbox=True, bwrap=fake_bwrap))
    runs = [json.loads(line) for line in (tmp_path / "bwrap.log").read_text().splitlines()]
    agent = next(r for r in runs if r["cmd"][0] == fake_claude)
    work = mxc_home() / "ports" / str(job["id"]) / "work"
    assert (str(real), str(work / "claude-cfg" / ".credentials.json")) in bind_pairs(agent["bwrap"], "--bind-try")
    # nothing else of ~/.claude is exposed: only that one file
    assert not any(str(home) in a for a in agent["bwrap"] if a != str(real))


def test_private_caches_are_in_the_job_work_dir(tmp_path, fake_claude):
    m = make_manager(tmp_path, fake_claude)
    job = _Job(5, "acme/weird", "weird", 1)
    job.dir = tmp_path / "j"
    _, env, _ = m._confine(job, ["x"], "check", None)
    for k in ("PIXI_CACHE_DIR", "RATTLER_CACHE_DIR", "UV_CACHE_DIR", "XDG_CACHE_HOME", "MODULAR_DERIVED_PATH", "HF_HOME", "HF_HUB_CACHE"):
        assert env[k].startswith(str(job.dir / "work")), k


def test_skill_env_is_installed_outside_the_sandbox_before_the_agent(tmp_path, fake_claude, fake_bwrap):
    order = tmp_path / "order.log"
    tools = check_tools()
    tools.install = lambda c: ([sys.executable, "-c", f"open({str(order)!r}, 'a').write('install')"], c.skills_root)
    job = run(make_manager(tmp_path, fake_claude, sandbox=True, bwrap=fake_bwrap, tools=tools))
    assert job["status"] == "done", job["message"]
    assert order.read_text() == "install"
    runs = [json.loads(line) for line in (tmp_path / "bwrap.log").read_text().splitlines()]
    assert not any(r["cmd"][:2] == [sys.executable, "-c"] for r in runs)      # never wrapped
    assert "trusted install of the pristine skill env, not sandboxed" in Path(job["log"]).read_text()


def test_failed_skill_install_fails_the_skills_stage(tmp_path, fake_claude):
    tools = check_tools()
    tools.install = lambda c: ([sys.executable, "-c", "import sys; print('no space left'); sys.exit(3)"], c.skills_root)
    job = run(make_manager(tmp_path, fake_claude, tools=tools))
    assert job["status"] == "failed" and "could not install the skill's pixi environment" in job["message"]
    assert calls(tmp_path) == []


def test_default_install_uses_the_lockfile_when_there_is_one(tmp_path):
    clone = tmp_path / "c"
    (clone / "import-model").mkdir(parents=True)
    ctx = port_auto.Ctx("r", "o/r", "r", tmp_path, tmp_path / "p", clone)
    assert port_auto._skill_install(ctx) is None                       # no manifest: nothing to install
    (clone / "import-model" / "pixi.toml").write_text("")
    assert port_auto._skill_install(ctx) == (["pixi", "install"], clone / "import-model")
    (clone / "import-model" / "pixi.lock").write_text("")
    assert port_auto._skill_install(ctx)[0] == ["pixi", "install", "--locked"]


def test_without_bwrap_auto_is_refused_unless_unsandboxed_is_given(tmp_path, fake_claude):
    m = make_manager(tmp_path, fake_claude, sandbox=True, bwrap="no-such-bwrap")
    with pytest.raises(port_auto.PortUnsafe, match="--unsandboxed"):
        m.start("acme/weird")
    assert m.list() == [] and calls(tmp_path) == []


def test_without_bwrap_it_says_so_and_asks_claude_for_its_own_sandbox(tmp_path, fake_claude):
    m = make_manager(tmp_path, fake_claude, sandbox=True, bwrap="no-such-bwrap")
    job = run(m, unsandboxed=True)
    assert job["status"] == "done", job["message"]
    assert "[agent] NOT sandboxed (bubblewrap (bwrap) not found; --unsandboxed was given); using Claude Code's own sandbox setting" in Path(job["log"]).read_text()
    (call,) = calls(tmp_path)
    i = call["argv"].index("--settings")
    assert json.loads(call["argv"][i + 1])["sandbox"]["enabled"] is True


async def test_route_refuses_auto_without_a_sandbox_with_the_reason(tmp_path, monkeypatch):
    import httpx
    from mxconsole.gateway import create_app
    from mxconsole.supervisor import Supervisor
    monkeypatch.setattr("mxconsole.port_sandbox.have_bwrap", lambda path="bwrap": None)
    store = Store(tmp_path / "mst.db")
    app = create_app(store, Supervisor(store, max_command=(sys.executable, "-c", "pass")))
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 5000))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:11435") as c:
        r = await c.post("/admin/ports", json={"ref": "acme/weird"}, headers={"X-Mst-Admin": "1"})
        assert r.status_code == 400 and r.json()["error"]["code"] == "no_sandbox"
        assert "bubblewrap" in r.json()["error"]["message"] and "--unsandboxed" in r.json()["error"]["message"]
        bad = await c.post("/admin/ports", json={"ref": "acme/weird", "unsandboxed": "yes"}, headers={"X-Mst-Admin": "1"})
        assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_request_body"


# ---- the gates cannot be edited by the agent --------------------------------------------------

class GateRunner(Runner):
    """git stand-in: the clone gets a check script; `status` reports it dirty when it was changed; `checkout`
    restores it when `restore` is set."""

    def __init__(self, restore=True):
        super().__init__()
        self.restore = restore
        self.script: Path | None = None

    def __call__(self, cmd, cwd=None):
        out = super().__call__(cmd, cwd)
        if cmd[:2] == ["git", "clone"]:
            self.script = Path(cmd[-1]) / "import-model" / "scripts" / "check_port.py"
            self.script.parent.mkdir(parents=True)
            self.script.write_text("# the real check\n")
        if cmd[:2] == ["git", "status"]:
            return 0, " M import-model/scripts/check_port.py" if "pwned" in self.script.read_text() else ""
        if cmd[:2] == ["git", "checkout"] and self.restore:
            self.script.write_text("# the real check\n")
        return out


def test_a_modified_gate_script_is_restored_before_the_build_check(tmp_path, fake_claude, monkeypatch):
    runner = GateRunner(restore=True)
    seen = []
    tools = check_tools()
    py, script = sys.executable, str(HERE / "fake_port_check.py")
    tools.build = lambda c: (seen.append(runner.script.read_text()) or [py, script, "build", str(c.port_dir)], c.workspace)
    monkeypatch.setenv("FAKE_CLAUDE_TAMPER", str(mxc_home() / "skills" / "modular" / "import-model" / "scripts" / "check_port.py"))
    job = run(make_manager(tmp_path, fake_claude, git_runner=runner, tools=tools))
    assert job["status"] == "done", job["message"]
    assert seen == ["# the real check\n"]                    # the check ran from the restored script
    assert ["git", "checkout", "--", "import-model/scripts", "import-model/pixi.toml", "import-model/SKILL.md"] in runner.calls
    assert any(c[:3] == ["git", "clean", "-fd"] for c in runner.calls)
    assert "gate scripts were modified; restoring" in Path(job["log"]).read_text()


def test_a_gate_that_cannot_be_restored_does_not_count(tmp_path, fake_claude, monkeypatch):
    runner = GateRunner(restore=False)
    built = []
    tools = check_tools()
    tools.build = lambda c: built.append(1) or ([sys.executable, "-c", "pass"], c.workspace)
    monkeypatch.setenv("FAKE_CLAUDE_TAMPER", str(mxc_home() / "skills" / "modular" / "import-model" / "scripts" / "check_port.py"))
    m = make_manager(tmp_path, fake_claude, git_runner=runner, tools=tools)
    job = run(m)
    assert job["status"] == "failed" and "could not be restored" in job["message"]
    assert built == [] and m.store.models() == []


# ---- cancel races and orphans -----------------------------------------------------------------

def test_cancel_before_the_agent_starts(tmp_path, fake_claude):
    gate = threading.Event()
    store = Store(tmp_path / "mst.db")
    m = PortManager(store, hub_factory=lambda: SlowHub(weights_bytes=CHUNK),
                    catalog_loader=lambda: (gate.wait(10), CATALOG)[1], claude=fake_claude, sandbox=False,
                    git_runner=Runner(), fetch_config=fake_fetch, tools=check_tools(), claude_home=tmp_path)
    job = m.start("acme/weird")
    m.cancel(job["id"])
    gate.set()
    final = m.wait(job["id"], 30)
    assert final["status"] == "cancelled" and calls(tmp_path) == []


def test_cancel_landing_between_spawn_and_registration_still_kills(tmp_path, fake_claude, monkeypatch):
    m = make_manager(tmp_path, fake_claude)
    job = _Job(99, "acme/weird", "weird", 1)
    job.dir = tmp_path / "jobdir"
    real = m._track
    monkeypatch.setattr(m, "_track", lambda j, p, mk, add=True: (j.cancel.set() if add else None, real(j, p, mk, add))[1])
    start = time.monotonic()
    with pytest.raises(port_auto._Cancelled):
        m._exec(job, ["sleep", "30"], tmp_path, timeout=60)
    assert time.monotonic() - start < 10


def test_cancel_during_parity_kills_max_serve(tmp_path, fake_claude, monkeypatch):
    pidfile = tmp_path / "max.pid"
    monkeypatch.setenv("FAKE_MAX_DELAY", "60")
    monkeypatch.setenv("FAKE_MAX_PID_FILE", str(pidfile))
    m = make_manager(tmp_path, fake_claude)
    job = m.start("acme/weird")
    for _ in range(500):
        if pidfile.exists() and pidfile.read_text():
            break
        time.sleep(0.02)
    pid = int(pidfile.read_text())
    m.cancel(job["id"])
    final = m.wait(job["id"], 30)
    assert final["status"] == "cancelled"
    assert {s["name"]: s["status"] for s in final["stages"]}["parity"] == "cancelled"
    for _ in range(100):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        raise AssertionError("max serve survived the cancel")


def test_serve_stage_can_be_cancelled_and_has_a_deadline(tmp_path, fake_claude):
    release = threading.Event()
    m = make_manager(tmp_path, fake_claude, serve=lambda name: release.wait(30))
    job = m.start("acme/weird")
    for _ in range(500):
        if m.get(job["id"])["stage"] == "serve":
            break
        time.sleep(0.02)
    m.cancel(job["id"])
    assert m.wait(job["id"], 10)["status"] == "cancelled"
    release.set()


def test_serve_deadline(tmp_path, fake_claude):
    m = make_manager(tmp_path, fake_claude, serve=lambda name: time.sleep(3), serve_timeout=0.4)
    job = run(m)
    assert job["status"] == "failed" and "did not start within" in job["message"] and job["model"] == "weird"


def test_restart_stops_a_leftover_agent_only_if_it_is_still_ours(tmp_path):
    store = Store(tmp_path / "mst.db")
    PortManager(store)  # creates the table
    ours = subprocess.Popen(["sleep", "60"], start_new_session=True)
    reused = subprocess.Popen(["sleep", "61"], start_new_session=True)
    try:
        for pid, marker in ((ours.pid, "sleep"), (reused.pid, "some-other-command")):
            store.db.execute("INSERT INTO ports (ref, status, stage, started_at, procs) VALUES (?, 'running', 'generate', 0, ?)",
                             (f"r{pid}", json.dumps([[pid, marker]])))
        PortManager(store)
        for _ in range(100):
            if ours.poll() is not None:
                break
            time.sleep(0.05)
        assert ours.poll() is not None          # the matching group was stopped
        assert reused.poll() is None            # a pid whose command does not match is left alone
        assert store.db.execute("SELECT COUNT(*) FROM ports WHERE status='running'").fetchone()[0] == 0
    finally:
        for p in (ours, reused):
            p.kill()
            p.wait()


def test_a_second_ref_that_would_register_the_same_name_is_busy(tmp_path, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "hang")
    m = make_manager(tmp_path, fake_claude)
    first = m.start("acme/weird")
    with pytest.raises(PortBusy) as busy:
        m.start("other/weird")
    assert busy.value.job["id"] == first["id"]
    m.start("other/different")  # a different name is fine
    m.cancel_all(10)


def test_one_atexit_hook_for_all_managers(tmp_path, fake_claude):
    before = len(port_auto._ATEXIT)
    port_auto.cleanup_at_exit(make_manager(tmp_path, fake_claude))
    port_auto.cleanup_at_exit(make_manager(tmp_path, fake_claude))
    assert len(port_auto._ATEXIT) == max(before, 1)


def test_restart_removes_the_work_dir_of_interrupted_jobs_only(tmp_path):
    store = Store(tmp_path / "mst.db")
    PortManager(store)
    for ref, status in (("a/one", "running"), ("b/two", "done")):
        store.db.execute("INSERT INTO ports (ref, status, stage, started_at) VALUES (?, ?, 'generate', 0)", (ref, status))
    ids = {r["ref"]: r["id"] for r in store.db.execute("SELECT id, ref FROM ports").fetchall()}
    for i in ids.values():
        w = mxc_home() / "ports" / str(i) / "work" / "cache"
        w.mkdir(parents=True)
        (w / "big").write_text("x")
        (mxc_home() / "ports" / str(i) / "port.log").write_text("log")
    PortManager(store)
    assert not (mxc_home() / "ports" / str(ids["a/one"]) / "work").exists()
    assert (mxc_home() / "ports" / str(ids["a/one"]) / "port.log").exists()   # the log stays
    assert (mxc_home() / "ports" / str(ids["b/two"]) / "work").exists()       # finished jobs are not touched


def test_scrub_env_drops_the_daemons_own_pixi_activation():
    """The daemon runs under `pixi run`: its PIXI_PROJECT_* / CONDA_* point at mxstudio's pixi.toml and env,
    which the sandbox can't see, so a `pixi run` inside the sandbox failed with "failed to open pixi.toml"."""
    from mxconsole import port_sandbox
    env = {"PIXI_PROJECT_MANIFEST": "/x/pixi.toml", "PIXI_PROJECT_ROOT": "/x", "PIXI_PROJECT_NAME": "mxconsole",
           "PIXI_ENVIRONMENT_NAME": "default", "PIXI_IN_SHELL": "1", "CONDA_PREFIX": "/x/.pixi/envs/default",
           "CONDA_DEFAULT_ENV": "mxconsole", "PIXI_CACHE_DIR": "/c", "PATH": "/usr/bin", "LANG": "C.UTF-8"}
    assert port_sandbox.scrub_env(env) == {"PIXI_CACHE_DIR": "/c", "PATH": "/usr/bin", "LANG": "C.UTF-8"}


def test_a_sandboxed_check_runs_in_the_directory_it_asked_for(tmp_path, fake_claude, fake_bwrap):
    """The skill's checks run `pixi run` from the skill root; inside bwrap they ran from the workspace,
    where pixi finds no pixi.toml (first real port, OLMoE)."""
    py, script = sys.executable, str(HERE / "fake_port_check.py")
    tools = check_tools()
    tools.build = lambda c: ([py, script, "build", str(c.port_dir)], c.skills_root)
    job = run(make_manager(tmp_path, fake_claude, sandbox=True, bwrap=fake_bwrap, tools=tools))
    runs = [json.loads(line) for line in (tmp_path / "bwrap.log").read_text().splitlines()]
    build = next(r for r in runs if r["cmd"][:3] == [py, script, "build"])
    argv = build["bwrap"]
    chdir = argv[argv.index("--chdir") + 1]
    assert chdir.endswith("skills/modular") or "skills" in chdir, (chdir, job["status"])
    assert chdir != str(mxc_home() / "ports" / "weird")  # not the workspace


def test_weights_are_downloaded_once_outside_the_sandbox_before_the_agent_and_checks_use_them(tmp_path, fake_claude, fake_bwrap):
    """check_port.py loads the real weights; with each sandbox's private HF cache it re-downloaded the whole
    model on every attempt (14 GB for OLMoE). Download once, outside, and hand the checks the local path."""
    job = run(make_manager(tmp_path, fake_claude, sandbox=True, bwrap=fake_bwrap))
    assert job["status"] == "done", job["message"]
    log = Path(job["log"]).read_text()
    assert log.index("downloading the weights") < log.index("== generate (attempt 1)")
    runs = [json.loads(line) for line in (tmp_path / "bwrap.log").read_text().splitlines()]
    agent = next(r for r in runs if r["cmd"][0] == fake_claude)
    ro = bind_pairs(agent["bwrap"], "--ro-bind-try")
    weights = Path(log.split("weights at ", 1)[1].splitlines()[0].strip())
    assert any(weights == Path(src) or Path(src) in weights.parents for src, _ in ro), "the agent sees the weights read-only"


def test_the_skill_checks_are_pointed_at_the_local_weights(tmp_path):
    from mxconsole import port, port_auto
    scripts = tmp_path / "s" / port.ENTRY_SKILL / "scripts"
    scripts.mkdir(parents=True)
    for f in ("check_port.py", "run_oss_gates.py"):
        (scripts / f).write_text("")
    c = port_auto.Ctx("acme/weird", "acme/weird", "weird", Path("/w"), Path("/w/port/weird"), tmp_path / "s")
    c.weights = "/hf/models--acme--weird/snapshots/abc"
    build, _ = port_auto._skill_build(c)
    assert build[build.index("scripts/check_port.py") + 1] == c.weights
    verify, _ = port_auto._skill_verify(c, 9000)
    # the verify gate loads the Hub config by repo id: it gets the id and finds the weights in its private,
    # offline HF cache (a local path failed its hub_config preflight on the first real port)
    assert verify[verify.index("scripts/run_oss_gates.py") + 1] == "acme/weird"
    assert verify[verify.index("--served-model-name") + 1] == "acme/weird"
    c.weights = ""
    assert port_auto._skill_build(c)[0][port_auto._skill_build(c)[0].index("scripts/check_port.py") + 1] == "acme/weird"


def test_the_checks_and_the_agent_see_the_gpu_too(tmp_path, fake_claude, fake_bwrap):
    """check_port.py has no device flag: with no GPU in the sandbox MAX picks the CPU, where bfloat16 weights
    are refused ("encoding 'bfloat16' is not compatible with the selected device type 'cpu'", OLMoE)."""
    py, script = sys.executable, str(HERE / "fake_port_check.py")
    job = run(make_manager(tmp_path, fake_claude, sandbox=True, bwrap=fake_bwrap))
    assert job["status"] == "done", job["message"]
    runs = [json.loads(line) for line in (tmp_path / "bwrap.log").read_text().splitlines()]
    agent = next(r for r in runs if r["cmd"][0] == fake_claude)
    build = next(r for r in runs if r["cmd"][:3] == [py, script, "build"])
    for r in (agent, build):
        a = r["bwrap"]
        assert a[a.index("--dev-bind") + 1] == "/dev", r["cmd"][:3]



def test_the_sandbox_hf_cache_holds_only_this_models_weights_read_only(tmp_path, fake_claude, fake_bwrap):
    job = run(make_manager(tmp_path, fake_claude, sandbox=True, bwrap=fake_bwrap))
    assert job["status"] == "done", job["message"]
    log = Path(job["log"]).read_text()
    weights = Path(log.split("weights at ", 1)[1].splitlines()[0].strip())
    repo = next((a for a in [weights, *weights.parents] if a.name.startswith("models--")), weights)
    runs = [json.loads(line) for line in (tmp_path / "bwrap.log").read_text().splitlines()]
    work = mxc_home() / "ports" / str(job["id"]) / "work"
    py, script = sys.executable, str(HERE / "fake_port_check.py")
    build = next(r for r in runs if r["cmd"][:3] == [py, script, "build"])
    pairs = bind_pairs(build["bwrap"], "--ro-bind-try")
    assert (str(repo), str(work / "hf" / "hub" / repo.name)) in pairs
    # online: the verify gate reads safetensors metadata from the Hub (get_safetensors_metadata has no
    # offline mode); the weights themselves are cache hits from the read-only bind, and no token is visible
    assert build["env"].get("HF_HUB_OFFLINE") != "1"
    assert not any(k in build["env"] for k in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"))
