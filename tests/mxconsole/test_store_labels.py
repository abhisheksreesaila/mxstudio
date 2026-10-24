"""Kind and your labels in the store: create, rename, reorder, delete, many-at-once, migration backfill."""
import sqlite3

import pytest

from mxconsole.kinds import LabelError, derive_kind
from mxconsole.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "mst.db")
    for name in ("alpha", "beta", "gamma"):
        s.add_model(name=name, repo_id=f"org/{name}", revision="r", source="hf-supported", architecture="Qwen2ForCausalLM")
    return s


def names(store):
    return [(l.name, l.count) for l in store.labels()]


def test_a_new_model_gets_the_kind_it_is_given_or_a_guess_from_its_name(store):
    store.add_model(name="x", repo_id="o/x", revision="r", source="s", architecture="A", kind="vision")
    store.add_model(name="qwen-coder", repo_id="o/qwen-coder", revision="r", source="s", architecture="A")
    assert store.get_model("x").kind == "vision"
    assert store.get_model("qwen-coder").kind == "coder"
    assert store.get_model("alpha").kind == "chat"


def test_pulling_again_keeps_a_kind_you_corrected(store):
    store.set_kind("alpha", "coder")
    store.add_model(name="alpha", repo_id="org/alpha", revision="r2", source="hf-supported", architecture="Qwen2ForCausalLM", kind="chat")
    assert store.get_model("alpha").kind == "coder"


def test_set_kind_refuses_an_unknown_kind_and_an_unknown_model(store):
    with pytest.raises(LabelError, match="chat, coder, vision, embeddings, image"):
        store.set_kind("alpha", "fast")
    with pytest.raises(LabelError, match="no model named nope"):
        store.set_kind("nope", "chat")


def test_labelling_creates_labels_in_order_and_counts_models(store):
    store.add_labels(["alpha", "beta"], ["work"])
    store.add_labels(["beta"], ["home"])
    assert names(store) == [("work", 2), ("home", 1)]
    assert store.get_model("beta").labels == ("home", "work")  # a model's labels are sorted by name
    assert [m.name for m in store.models() if "work" in m.labels] == ["alpha", "beta"]


def test_labelling_twice_is_harmless_and_unlabel_removes(store):
    store.add_labels(["alpha"], ["work"])
    store.add_labels(["alpha"], ["work"])
    assert names(store) == [("work", 1)]
    store.remove_labels(["alpha", "beta"], ["work"])  # beta never had it
    assert names(store) == [("work", 0)]
    assert store.get_model("alpha").labels == ()


def test_unknown_model_or_label_changes_nothing(store):
    with pytest.raises(LabelError, match="no model named nope"):
        store.add_labels(["alpha", "nope"], ["work"])
    assert names(store) == []  # alpha was not labelled and `work` was not created
    with pytest.raises(LabelError, match="no label named work"):
        store.remove_labels(["alpha"], ["work"])


def test_bad_names_are_refused(store):
    with pytest.raises(LabelError, match="lower-case"):
        store.add_labels(["alpha"], ["Work"])
    with pytest.raises(LabelError, match="lower-case"):
        store.create_label("my label")
    assert names(store) == []


def test_create_label_appends_and_refuses_a_duplicate(store):
    store.create_label("work")
    store.create_label("home")
    assert names(store) == [("work", 0), ("home", 0)]
    with pytest.raises(LabelError, match="already exists") as e:
        store.create_label("work")
    assert e.value.code == "label_exists"


def test_rename_updates_every_model_and_keeps_the_position(store):
    store.add_labels(["alpha", "beta"], ["work"])
    store.create_label("home")
    store.rename_label("work", "office")
    assert names(store) == [("office", 2), ("home", 0)]
    assert store.get_model("alpha").labels == ("office",)
    with pytest.raises(LabelError, match="already exists"):
        store.rename_label("office", "home")
    with pytest.raises(LabelError, match="no label named nope"):
        store.rename_label("nope", "x")
    with pytest.raises(LabelError, match="lower-case"):
        store.rename_label("office", "Office")


def test_reorder_needs_every_label_exactly_once(store):
    for n in ("a", "b", "c"):
        store.create_label(n)
    store.reorder_labels(["c", "a", "b"])
    assert [l.name for l in store.labels()] == ["c", "a", "b"]
    store.create_label("d")  # a new label goes last
    assert [l.name for l in store.labels()] == ["c", "a", "b", "d"]
    for bad in (["a", "b"], ["a", "a", "b", "c", "d"], ["a", "b", "c", "x"]):
        with pytest.raises(LabelError, match="every label exactly once"):
            store.reorder_labels(bad)
    assert [l.name for l in store.labels()] == ["c", "a", "b", "d"]


def test_delete_label_removes_it_from_models(store):
    store.add_labels(["alpha", "beta"], ["work", "home"])
    store.delete_label("work")
    assert names(store) == [("home", 2)]
    assert store.get_model("alpha").labels == ("home",)
    with pytest.raises(LabelError, match="no label named work"):
        store.delete_label("work")


def test_removing_a_model_removes_its_label_links_and_a_new_model_of_that_name_starts_clean(store):
    store.add_labels(["alpha", "beta"], ["work"])
    assert store.remove_model("alpha")
    assert names(store) == [("work", 1)]
    store.add_model(name="alpha", repo_id="org/alpha", revision="r", source="s", architecture="A")
    assert store.get_model("alpha").labels == ()


def test_an_old_database_gains_kind_without_the_network_and_keeps_its_models(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript("""CREATE TABLE models (name TEXT PRIMARY KEY, repo_id TEXT NOT NULL, revision TEXT NOT NULL,
        source TEXT NOT NULL, architecture TEXT NOT NULL, pinned INTEGER NOT NULL DEFAULT 0,
        keep_alive INTEGER NOT NULL DEFAULT 300, weights_path TEXT, created_at REAL NOT NULL);""")
    for name, arch in (("qwen2.5-coder-7b", "Qwen2ForCausalLM"), ("qwen2-vl-2b", "Qwen2VLForConditionalGeneration"),
                       ("bge-m3", "XLMRobertaModel"), ("tiny", "Qwen2ForCausalLM")):
        db.execute("INSERT INTO models VALUES (?, ?, 'r', 's', ?, 0, 300, NULL, 1.0)", (name, f"o/{name}", arch))
    db.commit()
    db.close()
    s = Store(path)
    assert {m.name: m.kind for m in s.models()} == {
        "qwen2.5-coder-7b": "coder", "qwen2-vl-2b": "vision", "bge-m3": "embeddings", "tiny": "chat"}
    s.set_kind("tiny", "image")
    assert Store(path).get_model("tiny").kind == "image"  # reopening does not backfill over a correction


def old_db(path):
    db = sqlite3.connect(path)
    db.executescript("""CREATE TABLE models (name TEXT PRIMARY KEY, repo_id TEXT NOT NULL, revision TEXT NOT NULL,
        source TEXT NOT NULL, architecture TEXT NOT NULL, pinned INTEGER NOT NULL DEFAULT 0,
        keep_alive INTEGER NOT NULL DEFAULT 300, weights_path TEXT, created_at REAL NOT NULL);""")
    for name in ("a-coder", "b-coder"):
        db.execute("INSERT INTO models VALUES (?, ?, 'r', 's', 'X', 0, 300, NULL, 1.0)", (name, f"o/{name}"))
    db.commit()
    db.close()


def test_a_crash_mid_backfill_leaves_the_old_schema_so_a_reopen_backfills(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    old_db(path)
    real, calls = derive_kind, []

    def flaky(**kw):
        calls.append(kw["name"])
        if len(calls) == 2:
            raise RuntimeError("crash")
        return real(**kw)

    monkeypatch.setattr("mxconsole.store.derive_kind", flaky)
    with pytest.raises(RuntimeError):
        Store(path)
    monkeypatch.undo()
    assert {m.name: m.kind for m in Store(path).models()} == {"a-coder": "coder", "b-coder": "coder"}


def test_labels_left_by_an_older_daemon_never_count_or_come_back(tmp_path):
    s = Store(tmp_path / "mst.db")
    s.add_model(name="alpha", repo_id="o/alpha", revision="r", source="s", architecture="A")
    s.add_labels(["alpha"], ["work"])
    s.db.execute("DELETE FROM models WHERE name = 'alpha'")  # what an older remove_model did: links stay behind
    assert [(l.name, l.count) for l in s.labels()] == [("work", 0)]
    s.add_model(name="alpha", repo_id="o/alpha", revision="r", source="s", architecture="A")
    assert s.get_model("alpha").labels == ()
    assert [(l.name, l.count) for l in s.labels()] == [("work", 0)]


def test_a_repull_of_a_registered_model_keeps_its_labels(store):
    store.add_labels(["alpha"], ["work"])
    store.add_model(name="alpha", repo_id="org/alpha", revision="r2", source="s", architecture="A")
    assert store.get_model("alpha").labels == ("work",)
