from typer.testing import CliRunner

from hotfire.cli import app
from hotfire.store import Store

runner = CliRunner()


def add(name="tiny"):
    Store.open().add_model(name=name, repo_id="org/tiny", revision="abc123", source="hf-supported",
                           architecture="Qwen2ForCausalLM")


def test_set_pin_and_keep_alive_change_the_store_and_link_the_console():
    add()
    r = runner.invoke(app, ["set", "tiny", "--pin"])
    assert r.exit_code == 0, r.output
    assert Store.open().get_model("tiny").pinned
    assert "pinned" in r.output and "/console#models" in r.output
    r = runner.invoke(app, ["set", "tiny", "--unpin", "--keep-alive", "10m"])
    m = Store.open().get_model("tiny")
    assert r.exit_code == 0 and not m.pinned and m.keep_alive == 600
    assert "10m" in r.output


def test_set_errors_are_one_line_and_exit_1():
    add()
    cases = [(["set", "nope", "--pin"], "nope"), (["set", "tiny", "--keep-alive", "soon"], "duration"),
             (["set", "tiny"], "nothing to change"), (["set", "tiny", "--pin", "--unpin"], "--pin")]
    for args, word in cases:
        r = runner.invoke(app, args)
        assert r.exit_code == 1 and word in r.output and len(r.output.strip().splitlines()) == 1, (args, r.output)
    assert not Store.open().get_model("tiny").pinned
