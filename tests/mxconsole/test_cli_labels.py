"""mxc label / unlabel / labels / ls --label --kind / set --kind."""
import json

import pytest
from typer.testing import CliRunner

from mxconsole.cli import app
from mxconsole.store import Store

runner = CliRunner()


@pytest.fixture
def store():
    s = Store.open()
    for name in ("alpha", "beta", "gamma"):
        s.add_model(name=name, repo_id=f"org/{name}", revision="r", source="hf-supported", architecture="Qwen2ForCausalLM")
    s.set_kind("gamma", "coder")
    return s


def run(*args, code=0):
    r = runner.invoke(app, list(args))
    assert r.exit_code == code, r.output
    return r.output


def test_label_puts_labels_on_a_model_and_creates_them(store):
    out = run("label", "alpha", "work", "home")
    assert out.strip() == "alpha: home, work"
    assert store.get_model("alpha").labels == ("home", "work")
    assert [l.name for l in store.labels()] == ["work", "home"]


def test_unlabel_takes_them_off(store):
    run("label", "alpha", "work", "home")
    assert run("unlabel", "alpha", "work").strip() == "alpha: home"
    assert run("unlabel", "alpha", "home").strip() == "alpha: no labels"


def test_label_errors_are_one_line_and_exit_1(store):
    cases = [(["label", "alpha", "Work"], "lower-case"), (["label", "ghost", "work"], "no model named ghost"),
             (["unlabel", "alpha", "nope"], "no label named nope"), (["unlabel", "ghost", "x"], "no model named ghost")]
    for args, word in cases:
        r = runner.invoke(app, args)
        assert r.exit_code == 1 and word in r.output and len(r.output.strip().splitlines()) == 1, (args, r.output)
    assert store.labels() == []


def test_labels_lists_in_order_with_counts(store):
    assert run("labels").strip() == "no labels yet. Try: mxc label <model> <label>"
    run("label", "alpha", "work")
    run("label", "beta", "work", "home")
    out = run("labels").splitlines()
    assert out[0].split() == ["LABEL", "MODELS"]
    assert [l.split() for l in out[1:3]] == [["work", "2"], ["home", "1"]]
    assert json.loads(run("labels", "--json")) == [
        {"name": "work", "position": 0, "count": 2}, {"name": "home", "position": 1, "count": 1}]


def test_labels_rename_and_delete(store):
    run("label", "alpha", "work", "home")
    assert run("labels", "--rename", "work", "office").strip() == "renamed work to office"
    assert store.get_model("alpha").labels == ("home", "office")
    assert run("labels", "--delete", "home").strip() == "deleted home (it was on 1 model)"
    assert store.get_model("alpha").labels == ("office",)
    for args, word in [(["labels", "--rename", "nope", "x"], "no label named nope"),
                       (["labels", "--rename", "office", "Bad"], "lower-case"),
                       (["labels", "--delete", "nope"], "no label named nope"),
                       (["labels", "--rename", "office", "x", "--delete", "office"], "one at a time")]:
        r = runner.invoke(app, args)
        assert r.exit_code == 1 and word in r.output and len(r.output.strip().splitlines()) == 1, (args, r.output)


def test_ls_shows_kind_and_labels(store):
    run("label", "alpha", "work", "home")
    lines = run("ls").splitlines()
    header = lines[0].split()
    assert header[:2] == ["NAME", "KIND"] and "LABELS" in header
    rows = {l.split()[0]: l for l in lines[1:] if l.strip() and not l.startswith("console")}
    assert "chat" in rows["alpha"].split() and "home,work" in rows["alpha"].split()
    assert "coder" in rows["gamma"].split()


def test_ls_json_has_kind_and_labels(store):
    run("label", "alpha", "work")
    rows = {r["name"]: r for r in json.loads(run("ls", "--json"))}
    assert (rows["alpha"]["kind"], rows["alpha"]["labels"]) == ("chat", ["work"])
    assert (rows["gamma"]["kind"], rows["gamma"]["labels"]) == ("coder", [])


def test_ls_filters_by_label_and_kind(store):
    run("label", "alpha", "work")
    run("label", "gamma", "work")
    assert [r["name"] for r in json.loads(run("ls", "--label", "work", "--json"))] == ["alpha", "gamma"]
    assert [r["name"] for r in json.loads(run("ls", "--kind", "coder", "--json"))] == ["gamma"]
    assert [r["name"] for r in json.loads(run("ls", "--label", "work", "--kind", "chat", "--json"))] == ["alpha"]
    out = run("ls", "--label", "work", "--kind", "chat")
    assert "alpha" in out and "gamma" not in out and "beta" not in out
    assert "no models match" in run("ls", "--kind", "image")


def test_ls_filter_errors(store):
    for args, word in [(["ls", "--kind", "fast"], "chat, coder, vision, embeddings, image"),
                       (["ls", "--label", "nope"], "no label named nope")]:
        r = runner.invoke(app, args)
        assert r.exit_code == 1 and word in r.output and len(r.output.strip().splitlines()) == 1, (args, r.output)


def test_set_kind_corrects_a_models_kind(store):
    assert "kind coder" in run("set", "alpha", "--kind", "coder")
    assert store.get_model("alpha").kind == "coder"
    assert json.loads(run("set", "alpha", "--kind", "vision", "--json"))["kind"] == "vision"
    r = runner.invoke(app, ["set", "alpha", "--kind", "fast"])
    assert r.exit_code == 1 and "chat, coder, vision, embeddings, image" in r.output
    assert store.get_model("alpha").kind == "vision"
