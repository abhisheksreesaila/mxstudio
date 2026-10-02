import pytest


@pytest.fixture(autouse=True)
def hotfire_home(tmp_path, monkeypatch):
    """Every test gets its own HOTFIRE_HOME so nothing touches ~/.hotfire."""
    home = tmp_path / "hotfire-home"
    monkeypatch.setenv("HOTFIRE_HOME", str(home))
    return home
