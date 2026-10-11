"""`mxc app`: the console in its own window. No GUI here: window launching and daemon start are injected."""
from __future__ import annotations

import pytest

from mxconsole import app


def _run(**kw):
    calls = []
    defaults = dict(
        ensure_daemon=lambda: calls.append("daemon") or True,
        open_window=lambda url: calls.append(("window", url)) or True,
        open_fallback=lambda url: calls.append(("fallback", url)) or True,
        url="http://127.0.0.1:1/console",
        say=lambda m: calls.append(("say", m)),
    )
    defaults.update(kw)
    return calls, app.run(**defaults)


def test_starts_daemon_then_opens_native_window():
    calls, how = _run()
    assert calls[0] == "daemon" and ("window", "http://127.0.0.1:1/console") in calls
    assert how == "native"


def test_ensure_daemon_is_responsible_for_not_restarting():
    # run() always asks; ensure_daemon (serve.ensure_daemon) is a no-op when the daemon answers
    from mxconsole import serve
    assert serve.ensure_daemon.__doc__ and "unless it already answers" in serve.ensure_daemon.__doc__


def test_falls_back_to_chromium_app_window_and_says_so():
    calls, how = _run(open_window=lambda url: False)
    assert how == "browser-app" and ("fallback", "http://127.0.0.1:1/console") in calls
    assert any(c[0] == "say" and "browser" in c[1] for c in calls if isinstance(c, tuple))


def test_no_window_at_all_raises():
    with pytest.raises(app.AppError):
        _run(open_window=lambda url: False, open_fallback=lambda url: False)


def test_native_command_goes_through_the_app_environment(tmp_path):
    cmd = app.native_command(tmp_path, "http://x/console", pixi="/bin/pixi")
    assert cmd[:3] == ["/bin/pixi", "run", "--manifest-path"]
    assert str(tmp_path / "pixi.toml") in cmd and "app" in cmd[cmd.index("-e") + 1]
    assert cmd[-1] == "http://x/console" and cmd[-2].endswith("window.py")


def test_fallback_command_per_platform():
    assert app.fallback_command("http://u", platform="linux", which=lambda n: "/usr/bin/chromium" if n == "chromium" else None) \
        == ["/usr/bin/chromium", "--app=http://u"]
    assert app.fallback_command("http://u", platform="linux", which=lambda n: None) is None
    assert app.fallback_command("http://u", platform="darwin", which=lambda n: None) \
        == ["open", "-na", "Google Chrome", "--args", "--app=http://u"]


def test_desktop_entry_content(tmp_path):
    text = app.desktop_entry(tmp_path, "/home/u/.local/share/icons/mxconsole.svg", pixi="/usr/bin/pixi")
    assert "[Desktop Entry]" in text and "Name=mxconsole" in text
    assert f"Path={tmp_path}" in text
    assert "Exec=/usr/bin/pixi run mxc app" in text
    assert "Icon=/home/u/.local/share/icons/mxconsole.svg" in text
    assert "Terminal=false" in text


def test_install_and_uninstall(tmp_path):
    home = tmp_path / "home"
    project = tmp_path / "proj"
    project.mkdir()
    desktop, icon = app.install(project, home=home, platform="linux", pixi="/usr/bin/pixi")
    assert desktop == home / ".local/share/applications/mxconsole.desktop" and desktop.exists()
    assert icon.exists() and icon.read_text().startswith("<svg")
    assert str(icon) in desktop.read_text()
    removed = app.uninstall(home=home, platform="linux")
    assert desktop in removed and not desktop.exists() and not icon.exists()
    assert app.uninstall(home=home, platform="linux") == []  # idempotent


def test_install_on_mac_explains_bundle_comes_later(tmp_path):
    with pytest.raises(app.AppError, match=r"\.app"):
        app.install(tmp_path, home=tmp_path, platform="darwin", pixi="/p")
