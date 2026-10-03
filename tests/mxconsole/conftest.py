import pytest


@pytest.fixture(autouse=True)
def mxc_home(tmp_path, monkeypatch):
    """Every test gets its own MXC_HOME so nothing touches ~/.mxc."""
    home = tmp_path / "mxc-home"
    monkeypatch.setenv("MXC_HOME", str(home))
    return home
