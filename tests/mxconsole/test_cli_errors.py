"""Errors go to stderr as one `error:` line with exit 1; stdout stays clean for `| jq`."""
import json

import pytest
from typer.testing import CliRunner

from mxconsole.cli import app
from mxconsole.store import Store

runner = CliRunner()


def add(name="tiny"):
    Store.open().add_model(name=name, repo_id="org/tiny", revision="abc123", source="hf-supported",
                           architecture="Qwen2ForCausalLM")


def assert_refused(r, word):
    assert r.exit_code == 1, (r.stdout, r.stderr)
    assert r.stdout == ""
    assert r.stderr.startswith("error: ") and word in r.stderr and len(r.stderr.strip().splitlines()) == 1


def test_json_command_error_leaves_stdout_empty():
    assert_refused(runner.invoke(app, ["metrics", "--range", "2h", "--json"]), "2h")


def test_plain_command_error_leaves_stdout_empty():
    assert_refused(runner.invoke(app, ["set", "nope", "--pin"]), "nope")


@pytest.mark.parametrize("args,word", [(["keys", "revoke", "ghost"], "ghost"), (["keys", "revoke", "_litellm"], "managed"),
                                       (["stop"], "name a model"), (["console", "nowhere"], "nowhere")])
def test_older_commands_use_the_same_error_shape(args, word):
    assert_refused(runner.invoke(app, args), word)


def test_keys_new_existing_key_is_an_error_line():
    runner.invoke(app, ["keys", "new", "web"])
    assert_refused(runner.invoke(app, ["keys", "new", "web"]), "web")


def test_usage_errors_stay_exit_2():
    assert runner.invoke(app, ["requests", "--limit", "0"]).exit_code == 2


def test_root_help_documents_exit_codes():
    out = runner.invoke(app, ["--help"]).stdout
    assert "Exit codes" in out and "bad usage" in out
