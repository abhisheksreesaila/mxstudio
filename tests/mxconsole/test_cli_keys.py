from typer.testing import CliRunner

from mxconsole.cli import app

runner = CliRunner()


def test_keys_new_prints_key_once_and_ls_shows_it():
    r = runner.invoke(app, ["keys", "new", "notes"])
    assert r.exit_code == 0
    key = next(w for w in r.output.split() if w.startswith("mst-"))
    assert "not be shown again" in r.output
    ls = runner.invoke(app, ["keys", "ls"])
    assert "notes" in ls.output and key[:12] in ls.output and key not in ls.output
    assert "active" in ls.output and "never" in ls.output


def test_keys_new_twice_refused_then_rotate():
    runner.invoke(app, ["keys", "new", "notes"])
    r = runner.invoke(app, ["keys", "new", "notes"])
    assert r.exit_code == 1 and "--rotate" in r.output
    r = runner.invoke(app, ["keys", "new", "notes", "--rotate"])
    assert r.exit_code == 0 and "mst-" in r.output


def test_keys_revoke():
    runner.invoke(app, ["keys", "new", "notes"])
    r = runner.invoke(app, ["keys", "revoke", "notes"])
    assert r.exit_code == 0
    assert "revoked" in runner.invoke(app, ["keys", "ls"]).output
    assert runner.invoke(app, ["keys", "revoke", "nope"]).exit_code == 1


def test_keys_ls_empty():
    assert "no keys" in runner.invoke(app, ["keys", "ls"]).output


def test_internal_keys_are_hidden_and_managed_by_mst():
    from mxconsole.store import Store
    from mxconsole import keys
    keys.create_key(Store.open(), "_litellm", internal=True)
    assert "_litellm" not in runner.invoke(app, ["keys", "ls"]).output
    assert runner.invoke(app, ["keys", "revoke", "_litellm"]).exit_code == 1
