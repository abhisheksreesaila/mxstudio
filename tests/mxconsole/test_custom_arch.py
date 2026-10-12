import sqlite3

from mxconsole.store import Store
from mxconsole.supervisor import Supervisor


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


def test_a_big_model_gets_a_gpu_share_that_fits_its_weights(tmp_path):
    """A fixed 35% of a 24 GB card is 8.4 GB; OLMoE-1B-7B's weights alone are 12.9 GB (first real port)."""
    from mxconsole.supervisor import memory_fraction
    gb = 1024 ** 3
    assert memory_fraction(1 * gb, 24576) == "0.35"                 # small models keep the shared default
    big = float(memory_fraction(int(12.9 * gb), 24576))
    assert 0.7 <= big <= 0.9                                       # weights + headroom for the KV cache
    assert memory_fraction(40 * gb, 24576) == "0.9"                 # capped; MAX reports what doesn't fit
    assert memory_fraction(None, 24576) == "0.35" and memory_fraction(12 * gb, None) == "0.35"


def test_the_serve_command_sizes_the_share_from_the_local_weights(tmp_path):
    s = Store(tmp_path / "mst.db")
    w = tmp_path / "snap"
    w.mkdir()
    with open(w / "model.safetensors", "wb") as f:
        f.truncate(int(12.9 * 1024 ** 3))  # sparse: no real disk used
    m = s.add_model(name="big", repo_id="o/big", revision="r", source="port", architecture="X",
                    weights_path=str(w), custom_arch_path="/ports/big")
    sup = Supervisor(s)
    sup.gpus = lambda: [{"index": 0, "total_mb": 24576, "used_mb": 0}]
    cmd = sup._command(m, 9000, gpu=0)
    assert float(cmd[cmd.index("--device-memory-utilization") + 1]) >= 0.7


def test_the_port_parity_server_uses_the_supervisors_launch_flags():
    """Device graph capture crashes MAX on this machine; the supervisor turns it off, so the port's own
    `max serve` must too (OLMoE's parity server died with "Failed to capture graph")."""
    from mxconsole.supervisor import launch_flags
    assert "--no-device-graph-capture" in launch_flags()
