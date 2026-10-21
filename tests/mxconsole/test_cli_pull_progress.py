"""`mxc pull`: live progress, --json events, Ctrl-C, and talking to a daemon. A fake hub, no network."""
import json
import sys
import threading

import pytest
from fake_hub import CHUNK, SlowHub
from fakes import CATALOG
from typer.testing import CliRunner

from mxconsole import cli, pull_cli
from mxconsole.store import Store

runner = CliRunner()


@pytest.fixture(autouse=True)
def fast_poll(monkeypatch, inproc_daemon):
    monkeypatch.setattr(pull_cli, "POLL", 0.01)


@pytest.fixture
def hub(monkeypatch):
    h = SlowHub(weights_bytes=10 * CHUNK, chunk_delay=0.01)
    monkeypatch.setattr("mxconsole.resolve.HubClient", lambda: h)
    monkeypatch.setattr("mxconsole.resolve.load_catalog", lambda **kw: CATALOG)
    return h


def test_progress_lines_show_steps_percent_and_the_result(hub):
    result = runner.invoke(cli.app, ["pull", "qwen2.5-0.5b"])
    assert result.exit_code == 0, result.output
    assert "download" in result.output and "%" in result.output and "█" in result.output
    assert result.output.strip().splitlines()[-1].startswith("pulled qwen2.5-0.5b (Qwen/Qwen2.5-0.5B-Instruct@abc123")
    assert "\r" not in result.output  # not a terminal: lines, not carriage returns


def test_json_prints_one_object_per_progress_event_then_a_final_one(hub):
    result = runner.invoke(cli.app, ["pull", "qwen2.5-0.5b", "--json"])
    assert result.exit_code == 0, result.output
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert events[-1]["event"] == "done" and events[-1]["percent"] == 100.0 and events[-1]["model"] == "qwen2.5-0.5b"
    progress = [e for e in events if e["event"] == "progress"]
    assert len(progress) >= 3 and "download" in {e["step"] for e in progress}  # a fast step can pass between two polls
    done_bytes = [e["done_bytes"] for e in progress]
    assert done_bytes == sorted(done_bytes)  # never goes backwards
    assert all(set(e) >= {"steps", "files", "percent", "speed", "total_bytes"} for e in events)


def test_json_errors_end_with_a_final_event_and_exit_1(hub):
    result = runner.invoke(cli.app, ["pull", "acme/weird", "--json"])
    assert result.exit_code == 1
    last = json.loads(result.stdout.splitlines()[-1])
    assert last["event"] == "unsupported" and last["hint"] == "mxc port acme/weird"
    bad = runner.invoke(cli.app, ["pull", "../x", "--json"])
    assert bad.exit_code == 1 and json.loads(bad.stdout.splitlines()[-1])["event"] == "failed"


def test_name_flag_registers_under_that_name(hub):
    assert runner.invoke(cli.app, ["pull", "someone/qwen-finetune", "--name", "mine"]).exit_code == 0
    assert Store.open().get_model("mine") is not None


def test_network_failure_is_one_error_line(monkeypatch):
    slow = SlowHub(weights_bytes=4 * CHUNK, fail_after=CHUNK)
    monkeypatch.setattr("mxconsole.resolve.HubClient", lambda: slow)
    monkeypatch.setattr("mxconsole.resolve.load_catalog", lambda **kw: CATALOG)
    result = runner.invoke(cli.app, ["pull", "qwen2.5-0.5b"])
    assert result.exit_code == 1 and "error: download of the model failed" in result.output
    assert Store.open().models() == []


def _wait_status(daemon, want, timeout=5.0):
    import time
    end = time.time() + timeout
    while time.time() < end:
        if daemon.get("/admin/pulls", headers={"X-Mst-Admin": "1"}).json()["pulls"][0]["status"] == want:
            return
        time.sleep(0.02)
    raise AssertionError(f"pull never became {want}")


def test_ctrl_c_cancels_cleanly_and_exits_130(hub, inproc_daemon):
    hub.weights_bytes = 200 * CHUNK
    hub.chunk_delay = 0.02
    ticks = []

    def interrupt(_):
        ticks.append(1)
        if len(ticks) == 12:
            raise KeyboardInterrupt

    code, text = pull_cli.run("qwen2.5-0.5b", None, False, source=pull_cli.DaemonSource(), sleep=interrupt)
    assert code == 130 and "cancelled" in text
    assert Store.open().get_model("qwen2.5-0.5b") is None
    _wait_status(inproc_daemon, "cancelled")  # the job really stopped, not just the CLI


def test_ctrl_c_in_json_mode_ends_with_a_cancelled_event(hub, capsys):
    hub.weights_bytes, hub.chunk_delay = 200 * CHUNK, 0.02
    n = []

    def interrupt(_):
        n.append(1)
        if len(n) == 8:
            raise KeyboardInterrupt

    code, text = pull_cli.run("qwen2.5-0.5b", None, True, source=pull_cli.DaemonSource(), sleep=interrupt)
    assert code == 130 and text == ""
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["event"] == "cancelled"


def test_the_terminal_bar_redraws_one_line_and_clears_it(hub, capsys):
    class Tty:
        def __init__(self):
            self.text = ""

        def isatty(self):
            return True

        def write(self, s):
            self.text += s

        def flush(self):
            pass

    err = Tty()
    code, text = pull_cli.run("qwen2.5-0.5b", None, False, display=pull_cli.Display(False, err=err))
    assert code == 0 and "\r" in err.text and "█" in err.text and err.text.endswith("\x1b[2K")
    assert "pulled" in text


def test_render_handles_unknown_totals_and_every_step(hub):
    job = {"ref": "x", "step": "resolve", "status": "running", "percent": None, "done_bytes": 0, "total_bytes": 0, "speed": 0,
           "eta_seconds": None, "steps": [{"id": "resolve", "label": "Resolve", "state": "now", "detail": "looking"}]}
    assert pull_cli.render(job) == "x  resolve…  looking"
    job.update(step="download", percent=50.0, done_bytes=5 * 1024 * 1024, total_bytes=10 * 1024 * 1024, speed=2 * 1024 * 1024, eta_seconds=3,
               steps=[{"id": "download", "label": "Download", "state": "now", "detail": ""}])
    line = pull_cli.render(job)
    assert "50.0%" in line and "5.0 MB of 10.0 MB" in line and "2.0 MB/s" in line and "eta 3s" in line


def test_a_running_daemon_does_the_pull_and_a_second_pull_watches_the_first(hub, monkeypatch, inproc_daemon):
    """DaemonSource against the real gateway app through starlette's test client."""
    tc = inproc_daemon
    hub.weights_bytes, hub.chunk_delay = 30 * CHUNK, 0.02
    other = {}

    def second():  # a second terminal pulling the same model while the first is running
        other["job"] = pull_cli.DaemonSource().start("qwen2.5-0.5b", None)

    first_started = threading.Event()
    real_start = pull_cli.DaemonSource.start

    def start(self, ref, name):
        job = real_start(self, ref, name)
        first_started.set()
        return job
    monkeypatch.setattr(pull_cli.DaemonSource, "start", start)
    t = threading.Timer(0.1, lambda: (first_started.wait(2), second()))
    t.start()
    result = runner.invoke(cli.app, ["pull", "qwen2.5-0.5b"])
    t.join()
    assert result.exit_code == 0 and "pulled qwen2.5-0.5b" in result.output
    assert other["job"]["ref"] == "qwen2.5-0.5b"  # got the running job back (409), not an error
    assert [p["status"] for p in tc.get("/admin/pulls", headers={"X-Mst-Admin": "1"}).json()["pulls"]] == ["done"]  # one job


def test_a_daemon_that_refuses_gives_one_error_line(hub, monkeypatch):
    monkeypatch.setenv("MXC_PORT", "1")  # nothing listens
    import httpx as real_httpx
    monkeypatch.setattr("httpx.request", lambda *a, **kw: (_ for _ in ()).throw(real_httpx.ConnectError("refused")))
    result = runner.invoke(cli.app, ["pull", "qwen2.5-0.5b"])
    assert result.exit_code == 1 and "could not reach the omxconsole daemon" in result.output
    assert "Traceback" not in result.output


def test_pull_starts_the_daemon_like_bench_does(hub, monkeypatch):
    started = []
    monkeypatch.setattr("mxconsole.serve.ensure_daemon", lambda *a, **k: started.append(1) or False)
    assert runner.invoke(cli.app, ["pull", "qwen2.5-0.5b"]).exit_code == 0
    assert started == [1]


def test_dry_run_stays_local_and_never_touches_the_daemon(hub, monkeypatch):
    monkeypatch.setattr("mxconsole.serve.ensure_daemon", lambda *a, **k: (_ for _ in ()).throw(AssertionError("daemon")))
    assert runner.invoke(cli.app, ["pull", "qwen2.5-0.5b", "--dry-run"]).exit_code == 0


# -- a daemon that goes away, and Ctrl-C at awkward moments ------------------------------------------------

JOB = {"id": 1, "ref": "x", "name": None, "status": "running", "step": "download", "done_bytes": 5, "total_bytes": 10,
       "percent": 50.0, "speed": 0, "eta_seconds": None, "files": [], "model": None, "message": "",
       "steps": [{"id": "download", "label": "Download", "state": "now", "detail": ""}]}


class Scripted:
    """A source whose calls do what a test says: return a job, or raise."""

    def __init__(self, start=None, gets=(), cancel=None):
        self._start, self.gets, self._cancel = start or JOB, list(gets), cancel
        self.cancelled = False

    def start(self, ref, name):
        if isinstance(self._start, BaseException):
            raise self._start
        return self._start

    def get(self, pull_id):
        item = self.gets.pop(0) if self.gets else JOB
        if isinstance(item, BaseException):
            raise item
        return item

    def cancel(self, pull_id):
        self.cancelled = True
        if self._cancel:
            raise self._cancel


def test_a_daemon_that_stops_mid_pull_ends_with_one_line_not_a_traceback(capsys):
    src = Scripted(gets=[JOB, pull_cli.PullFailed("could not reach the daemon")])
    code, text = pull_cli.run("x", None, False, source=src, sleep=lambda _: None)
    assert code == 1 and text == pull_cli.INTERRUPTED
    assert text == "the pull was interrupted (the daemon stopped); run the same command again to resume"


def test_json_mode_ends_with_a_failed_event_when_the_daemon_stops(capsys):
    src = Scripted(gets=[pull_cli.PullFailed("gone")])
    code, text = pull_cli.run("x", None, True, source=src, sleep=lambda _: None)
    last = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert code == 1 and text == "" and last["event"] == "failed" and "interrupted" in last["message"]


def test_a_restarted_daemon_that_marked_the_pull_interrupted_gives_the_same_line():
    done = {**JOB, "status": "failed", "error_kind": "interrupted", "message": "interrupted: the daemon stopped"}
    code, text = pull_cli.run("x", None, False, source=Scripted(gets=[done]), sleep=lambda _: None)
    assert code == 1 and text == pull_cli.INTERRUPTED


def test_the_daemon_stopping_during_the_cancel_is_the_same_one_line():
    calls = []

    def interrupt(_):
        calls.append(1)
        raise KeyboardInterrupt

    src = Scripted(cancel=pull_cli.PullFailed("gone"))
    code, text = pull_cli.run("x", None, False, source=src, sleep=interrupt)
    assert src.cancelled and code == 1 and text == pull_cli.INTERRUPTED


def test_a_second_ctrl_c_while_waiting_for_the_cancel_exits_130():
    n = []

    def sleep(_):
        n.append(1)
        raise KeyboardInterrupt  # the first one lands in the poll loop, the second in the cancel wait

    code, text = pull_cli.run("x", None, False, source=Scripted(), sleep=sleep)
    assert code == 130 and "cancelled" in text and len(n) == 2


def test_ctrl_c_during_the_start_request_exits_130_cleanly():
    code, text = pull_cli.run("x", None, False, source=Scripted(start=KeyboardInterrupt()), sleep=lambda _: None)
    assert code == 130 and "cancelled" in text


def test_daemon_source_treats_forbidden_and_server_errors_as_errors_not_no_change(monkeypatch):
    import httpx as real_httpx

    def answer(status):
        monkeypatch.setattr("httpx.request", lambda *a, **kw: real_httpx.Response(status, json={}))
        return pull_cli.DaemonSource()
    for status in (403, 500, 503):
        with pytest.raises(pull_cli.PullFailed):
            answer(status).get(1)
        with pytest.raises(pull_cli.PullFailed):
            answer(status).cancel(1)
    assert answer(404).get(1) is None
    assert answer(200).get(1) == {}
