"""try-port: the agent compiles, serves and compares its own package, the way mxconsole's final check will.
Runs real processes against the fakes (fake_max.py serves, fake_port_check.py stands in for the skill's probe
and gates), so nothing needs a GPU."""
import os
import sys
import time
from pathlib import Path

from mxconsole import port_try

HERE = Path(__file__).parent
FAKE_MAX = [sys.executable, str(HERE / "fake_max.py")]
CHECK = [sys.executable, str(HERE / "fake_port_check.py")]


def make(tmp_path, arch="ARCHITECTURES = []\n", **over) -> Path:
    pkg = tmp_path / "port" / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "arch.py").write_text(arch)
    state = tmp_path / "try"
    cfg = {
        "serve": [*FAKE_MAX, "serve", "--no-enable-overlap-scheduler", "--served-model-name", "acme/weird",
                  "--custom-architectures", str(pkg), "--model-path", str(tmp_path)],
        "probe": [*CHECK, "probe", str(pkg)], "gates": [*CHECK, "verify", str(pkg)], "cwd": str(tmp_path),
        "port_dir": str(pkg), "repo_id": "acme/weird", "smoke_prompt": "The capital of France is",
        "max_tries": 4, "health_timeout": 30, "step_timeout": 30}
    cfg.update(over)
    return port_try.write_config(state, cfg)


def call(cfg, wait=30.0) -> tuple[int, str]:
    lines: list[str] = []
    code = port_try.main(["--config", str(cfg), "--wait", str(wait)], out=lines.append)
    return code, "\n".join(lines)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def test_a_good_package_compiles_serves_and_passes(tmp_path):
    code, out = call(make(tmp_path))
    assert code == 0, out
    assert "serve      ok" in out and "smoke      'The capital of France is' -> 'hello from acme/weird'" in out
    assert "probe: ok" in out and "verify: ok" in out and "RESULT: PASS" in out


def test_the_server_is_always_shut_down(tmp_path, monkeypatch):
    pid_file = tmp_path / "max.pid"
    monkeypatch.setenv("FAKE_MAX_PID_FILE", str(pid_file))
    call(make(tmp_path))
    pid = int(pid_file.read_text())
    for _ in range(50):
        if not alive(pid):
            break
        time.sleep(0.1)
    assert not alive(pid)


def test_a_package_that_does_not_load_shows_the_real_error(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MAX_FAIL", "1")
    code, out = call(make(tmp_path))
    assert code == 1
    assert "serve      FAILED" in out and "boom: cannot load model" in out and "RESULT: FAIL" in out


def test_a_failing_gate_shows_its_output(tmp_path):
    code, out = call(make(tmp_path, arch="PARITY_BAD\n"))
    assert code == 1
    assert "prefill logprob differs from Hugging Face by 3.2 nats" in out and "RESULT: FAIL" in out


def test_a_slow_compile_says_still_running_and_the_next_call_picks_up_the_same_run(tmp_path, monkeypatch):
    starts = tmp_path / "starts"
    monkeypatch.setenv("FAKE_MAX_STARTS_FILE", str(starts))
    monkeypatch.setenv("FAKE_MAX_DELAY", "2")
    cfg = make(tmp_path)
    code, out = call(cfg, wait=0.3)
    assert code == 2 and "still running" in out and "run the same command again" in out
    code, out = call(cfg, wait=30)
    assert code == 0, out
    assert starts.read_text().count("start") == 1          # attached to the run, not a second one


def test_a_result_that_finished_between_calls_is_not_lost(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MAX_DELAY", "0.5")
    cfg = make(tmp_path)
    assert call(cfg, wait=0.1)[0] == 2
    time.sleep(3)                                           # it finishes while nobody is waiting
    code, out = call(cfg, wait=30)
    assert code == 0 and "RESULT: PASS" in out and port_try.read_state(cfg.parent)["tries"] == 1


def test_tries_are_capped(tmp_path):
    cfg = make(tmp_path, max_tries=1)
    assert call(cfg)[0] == 0
    code, out = call(cfg)
    assert code == 3 and "used all 1 tries" in out


def test_a_package_edited_during_the_run_is_flagged(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MAX_DELAY", "1")
    cfg = make(tmp_path)
    assert call(cfg, wait=0.2)[0] == 2
    (tmp_path / "port" / "pkg" / "arch.py").write_text("ARCHITECTURES = [1]\n")
    _, out = call(cfg, wait=30)
    assert "your package changed while this ran" in out


def test_stop_kills_a_running_try(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MAX_DELAY", "30")
    pid_file = tmp_path / "max.pid"
    monkeypatch.setenv("FAKE_MAX_PID_FILE", str(pid_file))
    cfg = make(tmp_path)
    assert call(cfg, wait=0.5)[0] == 2
    for _ in range(50):
        if pid_file.exists():
            break
        time.sleep(0.1)
    port_try.stop(cfg.parent)
    for _ in range(50):
        if not alive(int(pid_file.read_text())):
            break
        time.sleep(0.1)
    assert not alive(int(pid_file.read_text()))
    assert port_try.read_state(cfg.parent)["status"] != "running"


def test_noise_is_dropped_from_what_the_agent_reads():
    lines = ["Warning: cache directory '/x' is not writable; compilation caching will be disabled",
             "12:00:00.000 INFO: Still compiling Foo (59.0s elapsed)", "error: real problem"]
    assert port_try.clean(lines) == ["error: real problem"]


def test_the_skill_envs_manifest_warning_and_hub_notice_are_dropped_too():
    """Seen on the first real run: pixi prints this block before every skill script."""
    lines = [" WARN Encountered 1 warning while parsing the manifest:",
             "  ⚠ the `[system-requirements]` table is deprecated in favor of virtual",
             "  │ packages on `platforms`", "    ╭─[/x/pixi.toml:86:23]", " 85 │ platforms = [\"linux-64\"]",
             "    ·                              ╰── declare these on the `platforms` entries instead",
             "    ╰────", "  help: e.g. platforms = [{ name = \"my-linux-64\", platform = \"linux-64\", cuda",
             "        = \"13\" }]",
             "Warning: You are sending unauthenticated requests to the HF Hub. Please set a HF_TOKEN to enable higher rate limits",
             "logits_prefill   PASS  top-1 logprob hf=-2.4699 max=-2.3600 rel_diff=0.0445"]
    assert port_try.clean(lines) == [lines[-1]]
