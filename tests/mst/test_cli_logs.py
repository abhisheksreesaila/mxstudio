import json

from typer.testing import CliRunner

from mst.cli import app
from mst.store import Store, mst_home

runner = CliRunner()


def add(name="tiny"):
    Store.open().add_model(name=name, repo_id="org/tiny", revision="abc123", source="hf-supported",
                           architecture="Qwen2ForCausalLM")


def write_log(name, text):
    d = mst_home() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.log").write_text(text)


def test_logs_prints_the_tail_without_the_daemon(monkeypatch):
    monkeypatch.setenv("MST_PORT", "1")
    add()
    write_log("tiny", "".join(f"line {i}\n" for i in range(100)))
    r = runner.invoke(app, ["logs", "tiny", "--lines", "3"])
    assert r.exit_code == 0 and r.stdout.splitlines() == ["line 97", "line 98", "line 99"]
    assert len(runner.invoke(app, ["logs", "tiny"]).stdout.splitlines()) == 50


def test_logs_unknown_model_and_missing_log_are_errors():
    r = runner.invoke(app, ["logs", "nope"])
    assert r.exit_code == 1 and r.stdout == "" and r.stderr.startswith("error: ") and "nope" in r.stderr
    add()
    r = runner.invoke(app, ["logs", "tiny"])
    assert r.exit_code == 1 and r.stdout == "" and r.stderr.startswith("error: ")


def test_logs_follow_prints_new_lines_until_interrupted(monkeypatch):
    add()
    write_log("tiny", "first\n")
    sleeps = []

    def fake_sleep(_):
        sleeps.append(1)
        if len(sleeps) == 1:
            with open(mst_home() / "logs" / "tiny.log", "a") as f:
                f.write("second\n")
        elif len(sleeps) == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr("mst.cli.time.sleep", fake_sleep)
    r = runner.invoke(app, ["logs", "tiny", "--follow"])
    assert r.exit_code == 0 and r.stdout.splitlines() == ["first", "second"]


def test_set_json_prints_the_updated_row(monkeypatch):
    monkeypatch.setenv("MST_PORT", "1")
    add()
    r = runner.invoke(app, ["set", "tiny", "--pin", "--json"])
    row = json.loads(r.stdout)
    assert r.exit_code == 0 and row["name"] == "tiny" and row["pinned"] is True
    assert row == json.loads(runner.invoke(app, ["ls", "--json"]).stdout)[0]
