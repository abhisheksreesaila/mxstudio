"""Automatic port: the job (fake claude, fake checks, fake max, fake hub), its routes and the CLI."""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx
import pytest
from fake_hub import CHUNK, SlowHub
from fakes import CATALOG
from test_port import Runner, fake_fetch
from typer.testing import CliRunner

from mxconsole import cli, port, port_auto
from mxconsole.gateway import create_app
from mxconsole.port_auto import PortManager, PortTools
from mxconsole.pulls import BadPull
from mxconsole.store import Store, mxc_home
from mxconsole.supervisor import Supervisor

HERE = Path(__file__).parent
ADMIN = {"X-Mst-Admin": "1"}
STAGES = ["workspace", "skills", "generate", "build", "parity", "register", "serve"]


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    script = tmp_path / "bin" / "claude"
    script.parent.mkdir()
    script.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{HERE / "fake_claude.py"}" "$@"\n')
    script.chmod(0o755)
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(tmp_path / "claude.log"))
    monkeypatch.setenv("FAKE_CLAUDE_PIDS", str(tmp_path / "claude.pids"))
    return str(script)


def calls(tmp_path) -> list[dict]:
    p = tmp_path / "claude.log"
    return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


def check_tools() -> PortTools:
    py, script = sys.executable, str(HERE / "fake_port_check.py")
    return PortTools(build=lambda c: ([py, script, "build", str(c.port_dir)], c.workspace),
                     verify=lambda c, p: ([py, script, "verify", str(c.port_dir)], c.workspace))


def make_manager(tmp_path, claude, store=None, **kw):
    store = store or Store(tmp_path / "mst.db")
    started = []
    kw.setdefault("tools", check_tools())
    kw.setdefault("serve", started.append)
    (tmp_path / "archs" / "qwen2").mkdir(parents=True, exist_ok=True)
    kw.setdefault("arch_dir", tmp_path / "archs")
    kw.setdefault("sandbox", False)
    kw.setdefault("git_runner", Runner())
    kw.setdefault("claude_home", tmp_path / "claude-home")
    kw.setdefault("fetch_config", fake_fetch)
    m = PortManager(store, hub_factory=lambda: SlowHub(weights_bytes=CHUNK), catalog_loader=lambda: CATALOG,
                    claude=claude, max_command=(sys.executable, str(HERE / "fake_max.py")), **kw)
    m.started = started
    return m


def run(m, ref="acme/weird", **kw):
    job = m.start(ref, **kw)
    return m.wait(job["id"], timeout=60)


def rows(job):
    return [(s["name"], s["attempt"], s["status"]) for s in job["stages"]]


def test_happy_path_registers_and_serves(tmp_path, fake_claude):
    m = make_manager(tmp_path, fake_claude)
    job = run(m)
    assert job["status"] == "done", job["message"]
    assert [s["name"] for s in job["stages"]] == STAGES
    assert all(s["status"] == "done" and s["started"] and s["ended"] for s in job["stages"]), rows(job)
    assert job["model"] == "weird" and m.started == ["weird"]
    model = m.store.get_model("weird")
    assert model.source == "port" and model.custom_arch_path == str(mxc_home() / "ports" / "weird" / "port" / "weird")
    assert (Path(model.custom_arch_path) / "arch.py").is_file()


SKILL_SCRIPTS = ["inspect_hf.py", "list_checkpoint_keys.py", "list_native_archs.py", "check_walls.py", "scaffold.py",
                 "check_port.py", "run_oss_gates.py", "compare_layers.py", "import_model.py"]
ALLOWED = (["Read", "Write", "Edit", "Glob", "Grep", "WebFetch(domain:huggingface.co)",
            "Bash(cd:*)", "Bash(pixi install)", "Bash(pixi install --locked)"]
           + [f"Bash(pixi run python scripts/{n}:*)" for n in SKILL_SCRIPTS]
           + [f"Bash(pixi run -e cuda python scripts/{n}:*)" for n in SKILL_SCRIPTS])


def test_claude_is_run_headless_in_the_workspace_with_exactly_these_flags(tmp_path, fake_claude):
    run(make_manager(tmp_path, fake_claude))
    (call,) = calls(tmp_path)
    prompt = call["prompt"]
    assert call["argv"] == ["-p", prompt, "--permission-mode", "acceptEdits", "--permission-prompts", "none",
                            "--allowedTools", *ALLOWED, f"Bash(cp -r {tmp_path}/archs/qwen2/:*)",
                            *port_auto.skill_script_allowed(mxc_home() / "skills" / "modular"),
                            "--setting-sources", "project", "--strict-mcp-config",
                            "--output-format", "stream-json", "--verbose", "--no-session-persistence"]
    assert "--add-dir" not in call["argv"]
    for broad in ("Bash", "Bash(*)", "Bash(pixi run:*)", "Bash(sed:*)", "Bash(cp:*)", "Bash(cat:*)", "Bash(find:*)"):
        assert broad not in call["argv"]
    ws = mxc_home() / "ports" / "weird"
    assert call["cwd"] == str(ws)
    clone_skill = mxc_home() / "skills" / "modular" / "import-model"
    for needle in ("acme/weird", "import-model", "Package folder: ", "PORT.md", "check_port.py", "Nobody is available",
                   str(clone_skill)):
        assert needle in prompt
    assert "Fix this" not in prompt


def test_the_skills_are_links_to_the_read_only_clone(tmp_path, fake_claude):
    run(make_manager(tmp_path, fake_claude))
    ws = mxc_home() / "ports" / "weird"
    link = ws / ".claude" / "skills" / "import-model"
    assert link.is_symlink() and link.resolve() == (mxc_home() / "skills" / "modular" / "import-model").resolve()


def test_log_file_has_everything_and_tail_is_streamed(tmp_path, fake_claude):
    m = make_manager(tmp_path, fake_claude)
    job = run(m)
    log = Path(job["log"])
    assert log == mxc_home() / "ports" / str(job["id"]) / "port.log"
    text = log.read_text()
    for needle in ("== workspace", "== generate (attempt 1)", "claude: Porting (call 1)",
                   "claude> Bash pixi run python scripts/scaffold.py", "smoke:", "== done"):
        assert needle in text
    assert "== done" in job["log_tail"]


def test_build_failure_feeds_the_error_back_and_retries(tmp_path, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "fail_once")
    job = run(make_manager(tmp_path, fake_claude))
    assert job["status"] == "done", job["message"]
    by = {(s["name"], s["attempt"]): s["status"] for s in job["stages"]}
    assert by[("build", 1)] == "failed" and by[("generate", 2)] == by[("build", 2)] == by[("parity", 2)] == "done"
    assert by[("parity", 1)] == "skipped"
    first, second = calls(tmp_path)
    assert "Fix this" not in first["prompt"]
    assert "Fix this" in second["prompt"] and "ImportError: BROKEN layer" in second["prompt"]
    assert "attempt 2" in second["prompt"]


def test_parity_failure_feeds_the_error_back_and_retries(tmp_path, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "parity_once")
    job = run(make_manager(tmp_path, fake_claude))
    assert job["status"] == "done", job["message"]
    by = {(s["name"], s["attempt"]): s["status"] for s in job["stages"]}
    assert by[("build", 1)] == "done" and by[("parity", 1)] == "failed" and by[("parity", 2)] == "done"
    assert "differs from Hugging Face" in calls(tmp_path)[1]["prompt"]


def test_gives_up_after_max_attempts_and_says_what_it_tried(tmp_path, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "broken")
    m = make_manager(tmp_path, fake_claude)
    job = run(m, max_attempts=2)
    assert job["status"] == "failed"
    assert len(calls(tmp_path)) == 2
    assert "after 2 attempts" in job["message"] and "port.log" in job["message"]
    assert [s["status"] for s in job["stages"] if s["name"] == "register"] == ["skipped"]
    assert m.store.models() == []
    assert "BROKEN layer" in Path(job["log"]).read_text()


def test_attempts_are_bounded_when_asked(tmp_path, fake_claude):
    m = make_manager(tmp_path, fake_claude)
    for bad in (0, 11, "3", True):
        with pytest.raises(BadPull):
            m.start("acme/weird", max_attempts=bad)


def test_missing_claude_fails_at_generate_with_the_fix(tmp_path):
    job = run(make_manager(tmp_path, str(tmp_path / "no-such-claude")))
    assert job["status"] == "failed"
    by = {s["name"]: s for s in job["stages"]}
    assert by["generate"]["status"] == "failed" and by["skills"]["status"] == "done"
    assert "Install Claude Code" in job["message"] and "mxc port acme/weird" in job["message"]


def test_claude_crash_is_retried_then_reported(tmp_path, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "crash")
    job = run(make_manager(tmp_path, fake_claude), max_attempts=2)
    assert job["status"] == "failed" and "claude exited with code 2" in job["message"]
    assert "authentication failed" in Path(job["log"]).read_text()


def test_supported_model_is_refused_at_workspace(tmp_path, fake_claude):
    job = run(make_manager(tmp_path, fake_claude), ref="qwen2.5-0.5b")
    assert job["status"] == "failed" and "mxc pull" in job["message"]
    assert job["stages"][0]["status"] == "failed" and calls(tmp_path) == []


def test_cancel_kills_the_agent_process_tree(tmp_path, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "hang")
    m = make_manager(tmp_path, fake_claude)
    job = m.start("acme/weird")
    pids = tmp_path / "claude.pids"
    for _ in range(300):
        if pids.exists() and pids.read_text().count(" "):
            break
        time.sleep(0.02)
    agent, grandchild = map(int, pids.read_text().split())
    done = m.cancel(job["id"])
    assert done["status"] in ("running", "cancelled")
    final = m.wait(job["id"], timeout=30)
    assert final["status"] == "cancelled"
    for pid in (agent, grandchild):
        for _ in range(100):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            except PermissionError:
                break
            time.sleep(0.05)
        else:
            raise AssertionError(f"process {pid} survived the cancel")
    assert m.store.models() == []


def test_same_ref_twice_is_busy_and_restart_marks_interrupted(tmp_path, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "hang")
    m = make_manager(tmp_path, fake_claude)
    first = m.start("acme/weird")
    with pytest.raises(port_auto.PortBusy) as busy:
        m.start("acme/weird")
    assert busy.value.job["id"] == first["id"]
    m.cancel(first["id"])
    m.wait(first["id"], timeout=30)
    m.store.db.execute("UPDATE ports SET status='running' WHERE id=?", (first["id"],))
    again = PortManager(m.store)
    assert again.get(first["id"])["status"] == "failed" and "interrupted" in again.get(first["id"])["message"]


def test_no_serve_hook_skips_the_serve_stage(tmp_path, fake_claude):
    m = make_manager(tmp_path, fake_claude, serve=None)
    job = run(m)
    assert job["status"] == "done"
    assert {s["name"]: s["status"] for s in job["stages"]}["serve"] == "skipped"


def test_job_survives_in_the_database(tmp_path, fake_claude):
    m = make_manager(tmp_path, fake_claude)
    job = run(m)
    fresh = PortManager(m.store)
    again = fresh.get(job["id"])
    assert again["status"] == "done" and again["model"] == "weird" and again["stages"] == job["stages"]
    assert [p["id"] for p in fresh.list()] == [job["id"]]


# ---- routes -----------------------------------------------------------------------------------

@pytest.fixture
def daemon_env(tmp_path, fake_claude, monkeypatch):
    monkeypatch.setenv("MXC_CLAUDE", fake_claude)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-home"))
    monkeypatch.setattr("mxconsole.port_sandbox.have_bwrap", lambda path="bwrap": None)
    monkeypatch.setattr("mxconsole.resolve.HubClient", lambda: SlowHub(weights_bytes=CHUNK))
    monkeypatch.setattr("mxconsole.resolve.load_catalog", lambda **kw: CATALOG)
    monkeypatch.setattr("mxconsole.port._run", Runner())
    monkeypatch.setattr("mxconsole.port._download_config", fake_fetch)
    tools = check_tools()
    monkeypatch.setattr(port_auto, "_skill_build", tools.build)
    monkeypatch.setattr(port_auto, "_skill_verify", tools.verify)


@pytest.fixture
async def http(tmp_path, daemon_env):
    store = Store(tmp_path / "mst.db")
    sup = Supervisor(store, max_command=(sys.executable, str(HERE / "fake_max.py")))
    app = create_app(store, sup)
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 5000))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:11435") as c:
        c.store, c.sup = store, sup
        yield c
        await sup.stop_all()


async def settle(http, port_id):
    for _ in range(600):
        job = (await http.get(f"/admin/ports/{port_id}", headers=ADMIN)).json()
        if job["status"] != "running":
            return job
        await asyncio.sleep(0.05)
    raise AssertionError("port did not finish")


async def test_every_port_route_refuses_non_admin_callers(http):
    for method, path in [("GET", "/admin/ports"), ("POST", "/admin/ports"), ("GET", "/admin/ports/1"),
                         ("POST", "/admin/ports/1/cancel")]:
        kw = {"content": b'{"ref": "acme/weird"}'}
        assert (await http.request(method, path, **kw)).status_code == 403
        assert (await http.request(method, path, headers={**ADMIN, "X-Forwarded-For": "192.168.1.9"}, **kw)).status_code == 403
        assert (await http.request(method, path, headers={**ADMIN, "Origin": "https://evil.example"}, **kw)).status_code == 403
    assert http.store.models() == []


async def test_start_poll_finish_list_and_the_model_is_served(http):
    started = await http.post("/admin/ports", json={"ref": "acme/weird", "max_attempts": 2, "unsandboxed": True}, headers=ADMIN)
    assert started.status_code == 202
    job = started.json()
    assert job["ref"] == "acme/weird" and job["status"] == "running" and job["max_attempts"] == 2
    assert set(job) >= {"id", "ref", "status", "stage", "stages", "log_tail", "model"}
    done = await settle(http, job["id"])
    assert done["status"] == "done", done["message"]
    assert done["model"] == "weird" and [s["name"] for s in done["stages"]] == STAGES
    assert [m["name"] for m in (await http.get("/admin/models", headers=ADMIN)).json()] == ["weird"]
    assert [c["name"] for c in http.sup.status()] == ["weird"]  # the serve stage started it
    listing = (await http.get("/admin/ports", headers=ADMIN)).json()
    assert [p["id"] for p in listing["ports"]] == [job["id"]]


async def test_bad_bodies_unknown_ids_and_double_start(http, monkeypatch):
    assert (await http.post("/admin/ports", content=b"nope", headers=ADMIN)).status_code == 400
    assert (await http.post("/admin/ports", json={"ref": "../x"}, headers=ADMIN)).status_code == 400
    assert (await http.post("/admin/ports", json={"ref": "acme/weird", "max_attempts": 99}, headers=ADMIN)).status_code == 400
    assert (await http.post("/admin/ports", content=b"x" * 5000, headers=ADMIN)).status_code == 413
    assert (await http.get("/admin/ports/99", headers=ADMIN)).status_code == 404
    assert (await http.post("/admin/ports/99/cancel", headers=ADMIN)).status_code == 404
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "hang")
    first = (await http.post("/admin/ports", json={"ref": "acme/weird", "unsandboxed": True}, headers=ADMIN)).json()
    again = await http.post("/admin/ports", json={"ref": "acme/weird", "unsandboxed": True}, headers=ADMIN)
    assert again.status_code == 409 and again.json()["port"]["id"] == first["id"]
    cancelled = await http.post(f"/admin/ports/{first['id']}/cancel", headers=ADMIN)
    assert cancelled.status_code == 200
    assert (await settle(http, first["id"]))["status"] == "cancelled"


# ---- CLI --------------------------------------------------------------------------------------

def test_cli_auto_json_streams_progress_then_a_final_event(tmp_path, daemon_env, monkeypatch):
    from starlette.testclient import TestClient
    store = Store.open()
    sup = Supervisor(store, max_command=(sys.executable, str(HERE / "fake_max.py")))
    app = create_app(store, sup)
    monkeypatch.setattr("mxconsole.port_cli.POLL", 0.05)
    with TestClient(app, base_url="http://127.0.0.1:11435", client=("127.0.0.1", 5000)) as tc:
        monkeypatch.setattr("httpx.request", lambda method, url, **kw: tc.request(method, url, **kw))
        monkeypatch.setattr("mxconsole.serve.ensure_daemon", lambda *a, **k: False)
        out = CliRunner().invoke(cli.app, ["port", "acme/weird", "--auto", "--json", "--unsandboxed", "--max-attempts", "2"])
    assert out.exit_code == 0, out.output
    events = [json.loads(line) for line in out.stdout.splitlines() if line.startswith("{")]
    assert events[0]["event"] == "progress" and events[-1]["event"] == "done"
    assert events[-1]["model"] == "weird" and events[-1]["max_attempts"] == 2
    assert [s["name"] for s in events[-1]["stages"]] == STAGES and "== done" in events[-1]["log_tail"]


def test_cli_auto_prints_stages_and_exits_1_on_failure(tmp_path, daemon_env, monkeypatch):
    from starlette.testclient import TestClient
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "broken")
    store = Store.open()
    app = create_app(store, Supervisor(store, max_command=(sys.executable, str(HERE / "fake_max.py"))))
    monkeypatch.setattr("mxconsole.port_cli.POLL", 0.05)
    with TestClient(app, base_url="http://127.0.0.1:11435", client=("127.0.0.1", 5000)) as tc:
        monkeypatch.setattr("httpx.request", lambda method, url, **kw: tc.request(method, url, **kw))
        monkeypatch.setattr("mxconsole.serve.ensure_daemon", lambda *a, **k: False)
        out = CliRunner().invoke(cli.app, ["port", "acme/weird", "--auto", "--unsandboxed", "--max-attempts", "2"])
    assert out.exit_code == 1
    assert "generate (attempt 1)" in out.output and "build (attempt 2)" in out.output and "FAILED" in out.output
    assert "after 2 attempts" in out.output


def test_guided_port_is_unchanged_without_auto(monkeypatch):
    monkeypatch.setattr("mxconsole.resolve.load_catalog", lambda **kw: {})
    out = CliRunner().invoke(cli.app, ["port", "--help"])
    assert "--auto" in out.output and "--max-attempts" in out.output and "--json" in out.output


def test_the_prompt_names_the_max_that_will_serve_the_port(tmp_path):
    """The skill's env can run a newer MAX than mxconsole serves with (26.7 vs 26.5 on the first real port,
    which then failed with `cannot import name 'tree' from 'max'`): the agent must write for the server's MAX."""
    from mxconsole.port_auto import Ctx, build_prompt
    c = Ctx("acme/weird", "acme/weird", "weird", tmp_path, tmp_path / "port" / "weird", tmp_path / "s",
            serving_max="26.5.0.dev2026080106")
    p = build_prompt(c, 1, None, "donor note")
    assert "26.5.0.dev2026080106" in p and "newer" in p
    assert "26.5" not in build_prompt(Ctx("a/b", "a/b", "b", tmp_path, tmp_path / "p", tmp_path / "s"), 1, None, "")


def test_each_attempt_keeps_the_agents_raw_transcript(tmp_path, fake_claude, monkeypatch):
    """The raw stream-json of every attempt is kept beside port.log: what the port study and any future
    fine-tune learn from (port.log only has a readable rendering)."""
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "fail_once")
    job = run(make_manager(tmp_path, fake_claude))
    assert job["status"] == "done", job["message"]
    folder = Path(job["log"]).parent
    for attempt in (1, 2):
        events = [json.loads(line) for line in (folder / f"agent-{attempt}.jsonl").read_text().splitlines()]
        assert any(c.get("type") == "tool_use" for e in events for c in e.get("message", {}).get("content", []))
    assert not (folder / "agent-3.jsonl").exists()


def test_a_timed_out_attempt_keeps_its_partial_transcript(tmp_path, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "hang")
    job = run(make_manager(tmp_path, fake_claude, generate_timeout=1), max_attempts=1)
    assert job["status"] == "failed"
    text = (Path(job["log"]).parent / "agent-1.jsonl").read_text()
    assert '"tool_use"' in text and text.endswith("\n")


def test_a_rerun_with_a_reused_job_id_does_not_mix_transcripts(tmp_path, fake_claude):
    stale = mxc_home() / "ports" / "1" / "agent-1.jsonl"
    stale.parent.mkdir(parents=True)
    stale.write_text('{"old": "run"}\n')
    job = run(make_manager(tmp_path, fake_claude))
    assert job["status"] == "done", job["message"]
    assert '"old"' not in (Path(job["log"]).parent / "agent-1.jsonl").read_text()


def test_a_transcript_that_cannot_be_written_does_not_fail_the_port(tmp_path, fake_claude):
    (mxc_home() / "ports" / "1" / "agent-1.jsonl").mkdir(parents=True)   # a directory: open() fails
    job = run(make_manager(tmp_path, fake_claude))
    assert job["status"] == "done", job["message"]
    assert "could not keep the agent transcript" in Path(job["log"]).read_text()


def test_a_numeric_model_name_never_shares_a_job_folder():
    """Job folders are ports/<id>; a workspace named like one would let the agent write a job's logs."""
    assert not port._workspace_name("acme/12", "acme/12").isdigit()


# ---- spend attempts only where they can help (port study fixes 2-4) ---------------------------

def test_a_hub_rate_limit_retries_the_check_without_using_an_attempt(tmp_path, fake_claude, monkeypatch):
    """BLOOM's attempt 3 was lost to a Hub 429: that's not the port's fault."""
    monkeypatch.setenv("FAKE_CHECK_INFRA", "1")
    monkeypatch.setenv("FAKE_CHECK_INFRA_STATE", str(tmp_path / "infra"))
    job = run(make_manager(tmp_path, fake_claude, infra_backoff=0))
    assert job["status"] == "done", job["message"]
    assert len(calls(tmp_path)) == 1                                          # no second agent attempt
    assert "infrastructure error" in Path(job["log"]).read_text()


def test_a_hub_that_keeps_failing_stops_the_job_and_says_it_is_not_the_port(tmp_path, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CHECK_INFRA", "99")
    monkeypatch.setenv("FAKE_CHECK_INFRA_STATE", str(tmp_path / "infra"))
    job = run(make_manager(tmp_path, fake_claude, infra_backoff=0))
    assert job["status"] == "failed" and "not the port" in job["message"] and "429" in job["message"]
    assert len(calls(tmp_path)) == 1


def test_the_same_failure_twice_stops_early(tmp_path, fake_claude, monkeypatch):
    """EXAONE and BLOOM repeated the identical failure; the third attempt was wasted."""
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "broken")
    job = run(make_manager(tmp_path, fake_claude), max_attempts=3)
    assert job["status"] == "failed" and len(calls(tmp_path)) == 2
    assert "same failure twice" in job["message"]


def test_different_failures_still_use_every_attempt(tmp_path, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "broken_n")
    job = run(make_manager(tmp_path, fake_claude), max_attempts=3)
    assert job["status"] == "failed" and len(calls(tmp_path)) == 3


def test_the_best_attempt_is_kept_when_a_later_one_does_worse(tmp_path, fake_claude, monkeypatch):
    """StableLM: attempts 1-2 were probably right, attempt 3 broke the output and the harness kept attempt 3."""
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "regress")
    job = run(make_manager(tmp_path, fake_claude), max_attempts=3)
    assert job["status"] == "failed"
    pkg = mxc_home() / "ports" / "weird" / "port" / "weird"
    assert "PARITY_BAD" in (pkg / "arch.py").read_text()                      # attempt 1's package is back
    assert "attempt 1 got furthest" in job["message"] and "restored" in job["message"]


def test_a_model_whose_config_needs_remote_code_is_parked_before_the_agent_runs(tmp_path, fake_claude):
    """EXAONE: max serve won't load a config that only exists as Hub Python code."""
    cfg = {"architectures": ["ExaoneForCausalLM"], "model_type": "exaone",
           "auto_map": {"AutoConfig": "configuration_exaone.ExaoneConfig"}}
    def fetch(repo_id, revision):
        p = tmp_path / "remote-config.json"
        p.write_text(json.dumps(cfg))
        return p
    job = run(make_manager(tmp_path, fake_claude, fetch_config=fetch))
    assert job["status"] == "failed" and "remote code" in job["message"]
    assert calls(tmp_path) == []


def test_auto_map_on_a_model_type_transformers_knows_is_fine():
    assert not port_auto.needs_remote_code({"model_type": "llama", "auto_map": {"AutoConfig": "x.Y"}})
    assert port_auto.needs_remote_code({"model_type": "exaone", "auto_map": {"AutoConfig": "x.Y"}})
    assert not port_auto.needs_remote_code({"model_type": "exaone"})


def test_errors_from_our_own_max_server_are_the_ports_problem_not_infrastructure():
    """The verify gate reports a crashed or erroring local server in these forms (run_oss_gates / server_error):
    they must reach the agent, not stop the job as "not the port"."""
    for line in ("logits_multi     FAIL  MAX server unreachable on 41234: [Errno 111] Connection refused",
                 "logits_prefill   FAIL  [Errno 104] Connection reset by peer",
                 "logits_prefill   FAIL  HTTP 500 Internal Server Error",
                 "logits_multi     FAIL  HTTP 500: shape mismatch in layer 3"):
        assert port_auto.infra_error(line, server_alive=True) == "", line
    hub = "429 Too Many Requests for url: https://huggingface.co/bigscience/bloomz-560m/resolve/main/model.safetensors"
    assert port_auto.infra_error(hub, server_alive=True)
    assert port_auto.infra_error("logits_prefill   FAIL  HF side failed: [Errno 30] Read-only file system: '/x'",
                                 server_alive=True)
    assert port_auto.infra_error(hub, server_alive=False) == ""            # our server died: that's the port


def test_build_failures_with_the_same_counts_but_different_weights_are_different():
    out1 = "missing (compile raises KeyError): 1\n    model.layers.0.attn.q.weight\n"
    out2 = "missing (compile raises KeyError): 1\n    model.layers.0.attn.k.weight\n"
    a = port_auto._StageFailed("build failed", retry=True, output=out1, reached=1)
    b = port_auto._StageFailed("build failed", retry=True, output=out2, reached=1)
    assert port_auto.failure_signature(a) != port_auto.failure_signature(b)
    c = port_auto._StageFailed("max serve exited", retry=True, reached=2,
                               output="12:38:59.893 ERROR: boom on 41234\n12:39:00.001 INFO: x")
    d = port_auto._StageFailed("max serve exited", retry=True, reached=2,
                               output="13:01:02.004 ERROR: boom on 52311\n13:01:03.111 INFO: y")
    assert port_auto.failure_signature(c) == port_auto.failure_signature(d)


def test_the_restore_message_says_how_far_the_kept_attempt_really_got():
    assert port_auto.reached_text(1) == "it got to the build check"
    assert port_auto.reached_text(2) == "it built but did not serve"
    assert port_auto.reached_text(3.05) == "it served; the comparison failed"


def test_a_package_with_a_pipe_in_it_does_not_crash_the_job(tmp_path, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "regress")
    real = port_auto.PortManager._attempt

    def attempt(self, job, ctx, n, *a, **k):
        err = real(self, job, ctx, n, *a, **k)
        if ctx.port_dir.is_dir() and not (ctx.port_dir / "fifo").exists():
            os.mkfifo(ctx.port_dir / "fifo")
        return err
    monkeypatch.setattr(port_auto.PortManager, "_attempt", attempt)
    job = run(make_manager(tmp_path, fake_claude), max_attempts=3)
    assert job["status"] == "failed" and "unexpected error" not in job["message"]


def test_a_symlinked_package_dir_is_never_copied_or_restored(tmp_path, fake_claude):
    m = make_manager(tmp_path, fake_claude)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "secret").write_text("x")
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "pkg").symlink_to(elsewhere)
    ctx = port_auto.Ctx("r", "o/r", "r", ws, ws / "pkg", tmp_path)
    job = port_auto._Job(7, "r", "r", 3)
    job.dir = tmp_path / "job"
    assert m._snapshot(job, ctx) is False and not (job.dir / "best").exists()
    assert m._restore_best(job, ctx, (3.0, 1), 3, 1.0) == ""
    assert (elsewhere / "secret").exists()


def test_links_inside_the_package_are_copied_as_links(tmp_path, fake_claude):
    m = make_manager(tmp_path, fake_claude)
    pkg = tmp_path / "ws" / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "arch.py").write_text("x")
    (pkg / "link").symlink_to("/etc/hostname")
    ctx = port_auto.Ctx("r", "o/r", "r", tmp_path / "ws", pkg, tmp_path)
    job = port_auto._Job(8, "r", "r", 3)
    job.dir = tmp_path / "job"
    assert m._snapshot(job, ctx)
    assert (job.dir / "best" / "link").is_symlink()


def test_any_auto_map_on_a_model_type_transformers_does_not_know_is_parked():
    assert port_auto.needs_remote_code({"model_type": "exaone", "auto_map": {"AutoModelForCausalLM": "m.M"}})


def test_identical_parity_failures_match_despite_progress_bars_and_throughput():
    """BLOOM attempts 1 and 2 failed identically, but the check's output carries tqdm bars and tok/s that change
    every run; only the gate result lines decide."""
    def parity(bar, tput):
        return port_auto._StageFailed("parity check failed (exit 1)", retry=True, reached=3.04, output=(
            f"Loading weights: 46%|####6     | 135/293 [00:00<00:00, {bar}it/s]\n"
            f"Prompt Tput: {tput} tok/s\n"
            "walls            PASS  no wall signals\n"
            "logits_prefill   FAIL  top-1 logprob hf=-0.2062 max=-0.2681 rel_diff=0.2998\n"
            "logits_multi     FAIL  first top-1 mismatch at prefix length 5\noverall: FAIL\n"))
    assert port_auto.failure_signature(parity("1348.52", "27.6")) == port_auto.failure_signature(parity("977.10", "31.2"))
    moved = parity("1348.52", "27.6")
    moved.output = moved.output.replace("rel_diff=0.2998", "rel_diff=0.1310")
    assert port_auto.failure_signature(moved) != port_auto.failure_signature(parity("1.0", "1.0"))
    build = lambda bar: port_auto._StageFailed("build failed", retry=True, reached=1, output=(  # noqa: E731
        f"Parse safetensors files: 100%|##########| 2/2 [00:01<00:00, {bar}it/s]\nmissing (compile raises KeyError): 1\n"
        "    model.layers.0.attn.q.weight\n"))
    assert port_auto.failure_signature(build("1.92")) == port_auto.failure_signature(build("2.07"))
