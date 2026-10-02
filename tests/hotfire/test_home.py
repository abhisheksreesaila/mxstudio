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
