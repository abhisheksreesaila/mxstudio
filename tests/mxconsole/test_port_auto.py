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

from mxconsole import cli, port_auto
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
    m = PortManager(store, hub_factory=lambda: SlowHub(weights_bytes=CHUNK), catalog_loader=lambda: CATALOG,
                    claude=claude, max_command=(sys.executable, str(HERE / "fake_max.py")),
                    git_runner=Runner(), fetch_config=fake_fetch, **kw)
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


def test_claude_is_run_headless_in_the_workspace_with_the_prompt(tmp_path, fake_claude):
    run(make_manager(tmp_path, fake_claude))
    (call,) = calls(tmp_path)
    argv = call["argv"]
    assert argv[0] == "-p" and argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    assert "--output-format" in argv and "stream-json" in argv and "--no-session-persistence" in argv
    allowed = argv[argv.index("--allowedTools") + 1:argv.index("--add-dir")]
    assert "Bash(pixi run:*)" in allowed and "Bash" not in allowed and "Bash(*)" not in allowed
    assert call["cwd"] == str(mxc_home() / "ports" / "weird")
    p = call["prompt"]
    for needle in ("acme/weird", "import-model", "Package folder: ", "PORT.md", "check_port.py", "Nobody is available"):
        assert needle in p
    assert "Fix this" not in p


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
    started = await http.post("/admin/ports", json={"ref": "acme/weird", "max_attempts": 2}, headers=ADMIN)
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
    first = (await http.post("/admin/ports", json={"ref": "acme/weird"}, headers=ADMIN)).json()
    again = await http.post("/admin/ports", json={"ref": "acme/weird"}, headers=ADMIN)
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
        out = CliRunner().invoke(cli.app, ["port", "acme/weird", "--auto", "--json", "--max-attempts", "2"])
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
        out = CliRunner().invoke(cli.app, ["port", "acme/weird", "--auto", "--max-attempts", "2"])
    assert out.exit_code == 1
    assert "generate (attempt 1)" in out.output and "build (attempt 2)" in out.output and "FAILED" in out.output
    assert "after 2 attempts" in out.output


def test_guided_port_is_unchanged_without_auto(monkeypatch):
    monkeypatch.setattr("mxconsole.resolve.load_catalog", lambda **kw: {})
    out = CliRunner().invoke(cli.app, ["port", "--help"])
    assert "--auto" in out.output and "--max-attempts" in out.output and "--json" in out.output
