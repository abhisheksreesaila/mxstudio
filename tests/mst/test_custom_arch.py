import sqlite3

from mst.store import Store
from mst.supervisor import Supervisor


def test_add_model_remembers_custom_arch_path(tmp_path):
    s = Store(tmp_path / "mst.db")
    m = s.add_model(name="p", repo_id="o/p", revision="r", source="port", architecture="X",
                    custom_arch_path="/ports/p")
    assert m.custom_arch_path == "/ports/p"
    assert s.add_model(name="q", repo_id="o/q", revision="r", source="hf", architecture="X").custom_arch_path is None


def test_existing_database_gains_the_column(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE models (name TEXT PRIMARY KEY, repo_id TEXT NOT NULL, revision TEXT NOT NULL,
        source TEXT NOT NULL, architecture TEXT NOT NULL, pinned INTEGER NOT NULL DEFAULT 0,
        keep_alive INTEGER NOT NULL DEFAULT 300, weights_path TEXT, created_at REAL NOT NULL)""")
    db.execute("INSERT INTO models VALUES ('a','o/a','r','hf','X',0,300,NULL,0)")
    db.commit()
    db.close()
    s = Store(path)
    assert s.get_model("a").custom_arch_path is None
    Store(path)  # reopening is harmless


def test_supervisor_passes_custom_architectures(tmp_path):
    s = Store(tmp_path / "mst.db")
    m = s.add_model(name="p", repo_id="o/p", revision="r", source="port", architecture="X",
                    custom_arch_path="/ports/p")
    cmd = Supervisor(s)._command(m, 9000)
    assert cmd[cmd.index("--custom-architectures") + 1] == "/ports/p"
    plain = s.add_model(name="q", repo_id="o/q", revision="r", source="hf", architecture="X")
    assert "--custom-architectures" not in Supervisor(s)._command(plain, 9000)
