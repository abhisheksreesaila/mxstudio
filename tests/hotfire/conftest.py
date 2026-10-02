import pytest


@pytest.fixture(autouse=True)
def mst_home(tmp_path, monkeypatch):
    """Every test gets its own MST_HOME so nothing touches ~/.mst."""
    home = tmp_path / "mst-home"
    monkeypatch.setenv("MST_HOME", str(home))
    return home
