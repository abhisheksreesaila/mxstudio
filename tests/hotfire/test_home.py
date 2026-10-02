import pytest

from hotfire.store import hotfire_home


@pytest.fixture
def fake_user(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("HOTFIRE_HOME", raising=False)
    monkeypatch.delenv("MST_HOME", raising=False)
    return tmp_path


def test_legacy_mst_home_env_still_works(fake_user, monkeypatch):
    monkeypatch.setenv("MST_HOME", str(fake_user / "legacy"))
    assert hotfire_home() == fake_user / "legacy"


def test_hotfire_home_env_wins_over_mst_home(fake_user, monkeypatch):
    monkeypatch.setenv("MST_HOME", str(fake_user / "legacy"))
    monkeypatch.setenv("HOTFIRE_HOME", str(fake_user / "new"))
    assert hotfire_home() == fake_user / "new"


def test_default_home_moves_old_mst_folder(fake_user, capsys):
    (fake_user / ".mst").mkdir()
    (fake_user / ".mst" / "mst.db").write_text("data")
    assert hotfire_home() == fake_user / ".hotfire"
    assert (fake_user / ".hotfire" / "mst.db").read_text() == "data"
    assert not (fake_user / ".mst").exists()
    assert "moved" in capsys.readouterr().err


def test_no_move_when_home_env_is_set(fake_user, monkeypatch):
    (fake_user / ".mst").mkdir()
    monkeypatch.setenv("HOTFIRE_HOME", str(fake_user / "elsewhere"))
    hotfire_home()
    assert (fake_user / ".mst").exists() and not (fake_user / ".hotfire").exists()


def test_no_move_when_hotfire_folder_exists(fake_user):
    (fake_user / ".mst").mkdir()
    (fake_user / ".hotfire").mkdir()
    hotfire_home()
    assert (fake_user / ".mst").exists()


def test_mst_home_alone_stops_the_move(fake_user, monkeypatch):
    (fake_user / ".mst").mkdir()
    monkeypatch.setenv("MST_HOME", str(fake_user / "legacy"))
    hotfire_home()
    assert (fake_user / ".mst").exists() and not (fake_user / ".hotfire").exists()


def test_a_move_that_lost_a_race_carries_on(fake_user, monkeypatch, capsys):
    """Two first runs at once: the other process moved it first."""
    (fake_user / ".mst").mkdir()
    from pathlib import Path

    def raced(self, target):
        Path(target).mkdir()
        raise FileNotFoundError(self)
    monkeypatch.setattr(Path, "rename", raced)
    assert hotfire_home() == fake_user / ".hotfire"


def test_a_move_that_cannot_happen_keeps_using_mst(fake_user, monkeypatch, capsys):
    """A mount point or another device: say so once, keep working from ~/.mst."""
    (fake_user / ".mst").mkdir()
    from pathlib import Path

    def busy(self, target):
        raise OSError(16, "Device or resource busy")
    monkeypatch.setattr(Path, "rename", busy)
    assert hotfire_home() == fake_user / ".mst"
    assert "move it by hand" in capsys.readouterr().err


def test_no_move_while_an_old_daemon_holds_its_lock(fake_user, capsys):
    import fcntl
    run = fake_user / ".mst" / "run"
    run.mkdir(parents=True)
    with open(run / "daemon.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert hotfire_home() == fake_user / ".mst"
    assert "daemon" in capsys.readouterr().err


def test_port_and_command_settings_fall_back_and_prefer_hotfire(monkeypatch):
    from hotfire.settings import env
    monkeypatch.delenv("HOTFIRE_PORT", raising=False)
    monkeypatch.setenv("MST_PORT", "1111")
    assert env("PORT") == "1111"
    monkeypatch.setenv("HOTFIRE_PORT", "2222")
    assert env("PORT") == "2222"
    monkeypatch.setenv("MST_MAX_CMD", "old")
    monkeypatch.setenv("HOTFIRE_MAX_CMD", "new")
    assert env("MAX_CMD") == "new"
