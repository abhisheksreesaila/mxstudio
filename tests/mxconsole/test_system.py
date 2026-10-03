"""mxconsole.system: the crew, listeners, log tails and counters, read from a fake /proc."""
from datetime import date, timedelta

from mxconsole import system


def fake_proc(root, pid, *, rss_kb=2048, ticks=1000, state="S", cmd=("max", "serve", "--port", "5555"), up=100.0):
    d = root / str(pid)
    d.mkdir(parents=True)
    fields = [state] + ["0"] * 18 + [str(ticks)] + ["0"] * 5
    (d / "stat").write_text(f"{pid} (max serve) " + " ".join(fields))
    (d / "status").write_text(f"Name:\tmax\nVmRSS:\t   {rss_kb} kB\n")
    (d / "cmdline").write_bytes(b"\0".join(c.encode() for c in cmd) + b"\0")
    (root / "uptime").write_text(f"{up} 0.0")


def test_proc_stats_reads_rss_and_uptime(tmp_path):
    fake_proc(tmp_path, 42, rss_kb=10240, ticks=system.CLK_TCK * 30, up=100.0)
    assert system.proc_stats(42, tmp_path) == {"rss_mb": 10.0, "uptime_seconds": 70}


def test_proc_stats_is_none_for_missing_and_zombie_processes(tmp_path):
    assert system.proc_stats(1, tmp_path) is None
    fake_proc(tmp_path, 7, state="Z")
    assert system.proc_stats(7, tmp_path) is None


def test_proc_stats_survives_a_missing_status_file(tmp_path):
    fake_proc(tmp_path, 9)
    (tmp_path / "9" / "status").unlink()
    assert system.proc_stats(9, tmp_path)["rss_mb"] is None


def test_collect_with_supervisor_rows_lists_daemon_expose_and_children(tmp_path):
    fake_proc(tmp_path, 10, rss_kb=51200)
    fake_proc(tmp_path, 11)
    rows = [{"name": "tiny", "state": "ready", "port": 5555, "pid": 11},
            {"name": "cold-one", "state": "cold", "port": None, "pid": None}]
    crew = system.collect(rows, proc_root=tmp_path, daemon=10, expose_pids={"caddy": None, "litellm": None})
    assert [c["id"] for c in crew] == ["daemon", "caddy", "litellm", "model:tiny"]
    assert crew[0]["pid"] == 10 and crew[0]["rss_mb"] == 50.0 and crew[0]["state"] == "running"
    assert crew[1]["state"] == "stopped" and crew[1]["pid"] is None and crew[1]["rss_mb"] is None
    assert crew[3]["port"] == 5555 and "gpu" not in " ".join(crew[3])  # no invented GPU memory


def test_collect_without_the_daemon_reads_pid_files(tmp_path, mxc_home, monkeypatch):
    monkeypatch.setenv("MXC_MAX_CMD", "python /x/fake_max.py")
    fake_proc(tmp_path, 21, cmd=("python", "/x/fake_max.py", "serve", "--port", "6001"))
    run = mxc_home / "run"
    run.mkdir(parents=True)
    (run / "qwen2.5-0.5b.21.pid").write_text("21 21")  # a model name with dots
    (run / "stale.99.pid").write_text("99 99")  # process is gone
    crew = system.collect(None, proc_root=tmp_path, expose_pids={})
    assert crew[0]["state"] == "stopped" and crew[0]["pid"] is None
    models = [c for c in crew if c["kind"] == "model"]
    assert [(m["name"], m["pid"], m["port"]) for m in models] == [("qwen2.5-0.5b", 21, 6001)]


def test_pid_that_is_not_max_serve_is_ignored(tmp_path, mxc_home):
    fake_proc(tmp_path, 31, cmd=("sleep", "100"))  # a reused pid
    (mxc_home / "run").mkdir(parents=True)
    (mxc_home / "run" / "m.31.pid").write_text("31 31")
    assert [c for c in system.collect(None, proc_root=tmp_path, daemon=None, expose_pids={}) if c["kind"] == "model"] == []


def test_listeners_cover_every_port_and_say_who_talks_to_it(tmp_path):
    fake_proc(tmp_path, 10)
    fake_proc(tmp_path, 11)
    crew = system.collect([{"name": "tiny", "state": "ready", "port": 5555, "pid": 11}], proc_root=tmp_path,
                          daemon=10, expose_pids={"caddy": None, "litellm": None})
    rows = {r["name"]: r for r in system.listeners(crew)}
    assert rows["mxconsole daemon"]["state"] == "listening" and rows["mxconsole daemon"]["host"] == "127.0.0.1"
    assert rows["Caddy"]["state"] == "off" and rows["Caddy"]["exposed"] and rows["LiteLLM"]["state"] == "off"
    assert rows["max serve · tiny"]["port"] == 5555 and rows["max serve · tiny"]["talks_to"] == "the daemon only"
    assert all(r["talks_to"] for r in rows.values())


def test_a_reused_pid_running_npm_serve_is_not_a_model(tmp_path, mxc_home):
    fake_proc(tmp_path, 32, cmd=("node", "/usr/bin/npm", "run", "serve", "--port", "3000"))
    (mxc_home / "run").mkdir(parents=True)
    (mxc_home / "run" / "m.32.pid").write_text("32 32")
    assert [c for c in system.collect(None, proc_root=tmp_path, daemon=None, expose_pids={}) if c["kind"] == "model"] == []


def test_a_max_serve_found_through_its_interpreter_still_counts(tmp_path, mxc_home):
    fake_proc(tmp_path, 33, cmd=("/env/bin/python", "/env/bin/max", "serve", "--port", "6002"))
    (mxc_home / "run").mkdir(parents=True)
    (mxc_home / "run" / "m.33.pid").write_text("33 33")
    crew = system.collect(None, proc_root=tmp_path, daemon=None, expose_pids={})
    assert [c["pid"] for c in crew if c["kind"] == "model"] == [33]


def lock_file(mxc_home, text):
    run = mxc_home / "run"
    run.mkdir(parents=True, exist_ok=True)
    (run / "daemon.lock").write_text(text)


def test_daemon_pid_compares_the_start_time_and_never_takes_the_lock(mxc_home, tmp_path):
    import fcntl
    fake_proc(tmp_path, 50, ticks=777)
    lock_file(mxc_home, "50 777")
    assert system.daemon_pid(proc_root=tmp_path) == 50
    lock_file(mxc_home, "50 778")  # the pid was reused
    assert system.daemon_pid(proc_root=tmp_path) is None
    lock_file(mxc_home, "51 777")  # no such process
    assert system.daemon_pid(proc_root=tmp_path) is None
    lock_file(mxc_home, "50 777")
    with open(mxc_home / "run" / "daemon.lock", "a+") as held:  # held by a starting daemon: still readable
        fcntl.flock(held, fcntl.LOCK_EX)
        assert system.daemon_pid(proc_root=tmp_path) == 50


def test_daemon_pid_from_an_older_daemon_checks_the_command_line(mxc_home, tmp_path):
    fake_proc(tmp_path, 60, cmd=("python", "-m", "mxconsole", "daemon"))
    fake_proc(tmp_path, 61, cmd=("sleep", "9"))
    fake_proc(tmp_path, 62, cmd=("/env/bin/mxc", "daemon"))
    lock_file(mxc_home, "60")
    assert system.daemon_pid(proc_root=tmp_path) == 60
    lock_file(mxc_home, "61")
    assert system.daemon_pid(proc_root=tmp_path) is None
    lock_file(mxc_home, "62")
    assert system.daemon_pid(proc_root=tmp_path) == 62
    lock_file(mxc_home, "")  # a daemon older than the pid: unknown
    assert system.daemon_pid(proc_root=tmp_path) is None


def test_collect_says_running_pid_unknown_when_a_pidless_daemon_answers_health(tmp_path):
    crew = system.collect([], proc_root=tmp_path, daemon=None, expose_pids={}, health=lambda: True)
    assert crew[0]["state"] == "running" and crew[0]["pid"] is None
    assert "running" in system.ps_text(crew).splitlines()[1]
    crew = system.collect([], proc_root=tmp_path, daemon=None, expose_pids={}, health=lambda: False)
    assert crew[0]["state"] == "stopped"


def test_read_tail_caps_the_size_and_tolerates_a_missing_file(tmp_path):
    log = tmp_path / "x.log"
    log.write_text("a" * 100000 + "END")
    tail = system.read_tail(log, 10**9)
    assert len(tail) == system.MAX_LOG_BYTES and tail.endswith("END")
    assert system.read_tail(log, 5) == "aaEND"
    assert system.read_tail(tmp_path / "nope.log") == ""


def test_log_sources_list_only_logs_that_exist(mxc_home):
    assert system.log_sources(["tiny"]) == [{"id": "model:tiny", "label": "tiny"}]
    (mxc_home / "logs").mkdir(parents=True)
    (mxc_home / "logs" / "daemon.log").write_text("hi")
    (mxc_home / "expose").mkdir(exist_ok=True)
    (mxc_home / "expose" / "caddy.log").write_text("hi")
    assert [s["id"] for s in system.log_sources(["tiny"])] == ["daemon", "caddy", "model:tiny"]


def test_counters_count_per_day():
    day = [date(2026, 10, 3)]
    c = system.Counters(today=lambda: day[0])
    c.bump("x"); c.bump("x")
    assert c.get("x") == 2 and c.get("y") == 0
    day[0] += timedelta(days=1)
    assert c.get("x") == 0


def test_ps_text_is_a_table_with_dashes_for_what_is_unknown(tmp_path):
    fake_proc(tmp_path, 10, rss_kb=51200)
    crew = system.collect([], proc_root=tmp_path, daemon=10, expose_pids={})
    out = system.ps_text(crew)
    assert out.splitlines()[0].split() == ["NAME", "STATE", "PID", "PORT", "MEMORY", "UPTIME"]
    assert "50 MB" in out and "stopped" in out


def test_counters_are_safe_across_threads():
    import threading
    c = system.Counters()
    ts = [threading.Thread(target=lambda: [c.bump("x") for _ in range(2000)]) for _ in range(8)]
    [x.start() for x in ts]
    [x.join() for x in ts]
    assert c.get("x") == 16000
