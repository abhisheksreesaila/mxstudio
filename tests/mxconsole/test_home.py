import pytest

from mxconsole.store import mxc_home


@pytest.fixture
def fake_user(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    for v in ("MXC_HOME", "HOTFIRE_HOME", "MST_HOME"):
        monkeypatch.delenv(v, raising=False)
    return tmp_path


def test_legacy_mst_home_env_still_works(fake_user, monkeypatch):
    monkeypatch.setenv("MST_HOME", str(fake_user / "legacy"))
    assert mxc_home() == fake_user / "legacy"


def test_legacy_hotfire_home_env_still_works(fake_user, monkeypatch):
    monkeypatch.setenv("HOTFIRE_HOME", str(fake_user / "hf"))
    assert mxc_home() == fake_user / "hf"


def test_mxc_home_env_takes_precedence(fake_user, monkeypatch):
    monkeypatch.setenv("MST_HOME", str(fake_user / "legacy"))
    monkeypatch.setenv("HOTFIRE_HOME", str(fake_user / "hf"))
    monkeypatch.setenv("MXC_HOME", str(fake_user / "new"))
    assert mxc_home() == fake_user / "new"


def test_hotfire_home_env_wins_over_mst_home(fake_user, monkeypatch):
    monkeypatch.setenv("MST_HOME", str(fake_user / "legacy"))
    monkeypatch.setenv("HOTFIRE_HOME", str(fake_user / "hf"))
    assert mxc_home() == fake_user / "hf"


def test_default_home_moves_old_hotfire_folder(fake_user, capsys):
    (fake_user / ".hotfire").mkdir()
    (fake_user / ".hotfire" / "mst.db").write_text("data")
    assert mxc_home() == fake_user / ".mxc"
    assert (fake_user / ".mxc" / "mst.db").read_text() == "data"
    assert not (fake_user / ".hotfire").exists()
    assert "moved" in capsys.readouterr().err


def test_default_home_moves_old_mst_folder_when_no_hotfire(fake_user):
    (fake_user / ".mst").mkdir()
    (fake_user / ".mst" / "mst.db").write_text("data")
    assert mxc_home() == fake_user / ".mxc"
    assert (fake_user / ".mxc" / "mst.db").read_text() == "data"
    assert not (fake_user / ".mst").exists()


def test_hotfire_folder_is_preferred_over_mst(fake_user):
    (fake_user / ".hotfire").mkdir()
    (fake_user / ".hotfire" / "mst.db").write_text("hotfire")
    (fake_user / ".mst").mkdir()
    (fake_user / ".mst" / "mst.db").write_text("mst")
    assert mxc_home() == fake_user / ".mxc"
    assert (fake_user / ".mxc" / "mst.db").read_text() == "hotfire"
    assert (fake_user / ".mst").exists()


@pytest.mark.parametrize("var", ["MXC_HOME", "HOTFIRE_HOME", "MST_HOME"])
def test_any_home_env_stops_the_move(fake_user, monkeypatch, var):
    (fake_user / ".hotfire").mkdir()
    (fake_user / ".mst").mkdir()
    monkeypatch.setenv(var, str(fake_user / "elsewhere"))
    mxc_home()
    assert (fake_user / ".hotfire").exists() and (fake_user / ".mst").exists()
    assert not (fake_user / ".mxc").exists()


def test_no_move_when_mxc_folder_exists(fake_user):
    (fake_user / ".hotfire").mkdir()
    (fake_user / ".mxc").mkdir()
    mxc_home()
    assert (fake_user / ".hotfire").exists()


def test_mst_demo_is_never_touched(fake_user):
    (fake_user / ".mst-demo").mkdir()
    mxc_home()
    assert (fake_user / ".mst-demo").exists() and not (fake_user / ".mxc" / "x").exists()


def test_a_move_that_lost_a_race_carries_on(fake_user, monkeypatch, capsys):
    """Two first runs at once: the other process moved it first."""
    (fake_user / ".hotfire").mkdir()
    from pathlib import Path

    def raced(self, target):
        Path(target).mkdir()
        raise FileNotFoundError(self)
    monkeypatch.setattr(Path, "rename", raced)
    assert mxc_home() == fake_user / ".mxc"


def test_a_move_that_cannot_happen_keeps_using_the_old_folder(fake_user, monkeypatch, capsys):
    """A mount point or another device: say so once, keep working from ~/.mst."""
    (fake_user / ".hotfire").mkdir()
    from pathlib import Path

    def busy(self, target):
        raise OSError(16, "Device or resource busy")
    monkeypatch.setattr(Path, "rename", busy)
    assert mxc_home() == fake_user / ".hotfire"
    assert "move it by hand" in capsys.readouterr().err


def test_no_move_while_an_old_daemon_holds_its_lock(fake_user, capsys):
    import fcntl
    run = fake_user / ".hotfire" / "run"
    run.mkdir(parents=True)
    with open(run / "daemon.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert mxc_home() == fake_user / ".hotfire"
    assert "daemon" in capsys.readouterr().err


def test_port_and_command_settings_fall_back_in_order(monkeypatch):
    from mxconsole.settings import env
    for v in ("MXC_PORT", "HOTFIRE_PORT", "MST_PORT"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("MST_PORT", "1111")
    assert env("PORT") == "1111"
    monkeypatch.setenv("HOTFIRE_PORT", "2222")
    assert env("PORT") == "2222"
    monkeypatch.setenv("MXC_PORT", "3333")
    assert env("PORT") == "3333"


def test_a_race_with_both_old_folders_ends_up_in_mxc(fake_user, monkeypatch, capsys):
    """Another first run moved ~/.hotfire into ~/.mxc while we picked ~/.mst: use ~/.mxc, don't fall back."""
    import errno
    from pathlib import Path
    (fake_user / ".mst").mkdir()
    (fake_user / ".hotfire").mkdir()

    def raced(self, target):
        Path(target).mkdir(exist_ok=True)
        raise OSError(errno.ENOTEMPTY, "Directory not empty")
    monkeypatch.setattr(Path, "rename", raced)
    assert mxc_home() == fake_user / ".mxc"
    assert "move it by hand" not in capsys.readouterr().err


def test_no_move_while_an_old_daemon_holds_the_mst_lock(fake_user, capsys):
    import fcntl
    run = fake_user / ".mst" / "run"
    run.mkdir(parents=True)
    with open(run / "daemon.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert mxc_home() == fake_user / ".mst"
