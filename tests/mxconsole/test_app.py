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


def test_says_nothing_about_starting_when_daemon_was_already_up():
    calls, _ = _run(ensure_daemon=lambda: False)
    assert not any(isinstance(c, tuple) and c[0] == "say" and "started" in c[1] for c in calls)


def test_falls_back_to_chromium_app_window_and_says_so():
    calls, how = _run(open_window=lambda url: False)
    assert how == "browser-app" and ("fallback", "http://127.0.0.1:1/console") in calls
    assert any(c[0] == "say" and "browser" in c[1] for c in calls if isinstance(c, tuple))


def test_no_window_at_all_raises():
    with pytest.raises(app.AppError):
        _run(open_window=lambda url: False, open_fallback=lambda url: False)


def test_native_command_goes_through_the_app_environment(tmp_path):
    cmd = app.native_command(tmp_path, "http://x/console", tmp_path / "wv", pixi="/bin/pixi")
    assert cmd[:3] == ["/bin/pixi", "run", "--manifest-path"]
    assert str(tmp_path / "pixi.toml") in cmd and "app" in cmd[cmd.index("-e") + 1]
    assert cmd[-2] == "http://x/console" and cmd[-3].endswith("window.py") and cmd[-1] == str(tmp_path / "wv")


def test_fallback_command_per_platform():
    assert app.fallback_command("http://u", platform="linux", which=lambda n: "/usr/bin/chromium" if n == "chromium" else None) \
        == ["/usr/bin/chromium", "--app=http://u"]
    assert app.fallback_command("http://u", platform="linux", which=lambda n: None) is None
    assert app.fallback_command("http://u", platform="darwin", exists=lambda p: True) \
        == ["open", "-na", "Google Chrome", "--args", "--app=http://u"]
    assert app.fallback_command("http://u", platform="darwin", exists=lambda p: False) is None


def test_desktop_entry_content(tmp_path):
    text = app.desktop_entry(tmp_path, "/home/u/.local/share/icons/mxconsole.svg", pixi="/usr/bin/pixi")
    assert "[Desktop Entry]" in text and "Name=omxconsole" in text
    assert "Path=" not in text
    assert f'Exec="/usr/bin/pixi" run --manifest-path "{tmp_path}/pixi.toml" mxc app' in text
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


def test_desktop_entry_quotes_spaces_and_escapes_percent(tmp_path):
    project = tmp_path / "my proj 100%"
    text = app.desktop_entry(project, "/i.svg", pixi="/opt/my pixi/pixi")
    assert 'Exec="/opt/my pixi/pixi" run --manifest-path "' + str(project).replace("%", "%%") + '/pixi.toml" mxc app' in text


def test_prepare_env_runs_pixi_install_for_app_env(tmp_path):
    seen = []
    app.prepare_env(tmp_path, pixi="/p", runner=lambda c: seen.append(c) or 0)
    assert seen[0][:2] == ["/p", "install"] and seen[0][-2:] == ["-e", "app"]
    with pytest.raises(app.AppError, match="failed"):
        app.prepare_env(tmp_path, pixi="/p", runner=lambda c: 1)


# --- the `mxc app` command -------------------------------------------------------------------------
from typer.testing import CliRunner  # noqa: E402

from mxconsole import cli  # noqa: E402


def test_cli_opens_window(monkeypatch):
    from mxconsole import serve
    monkeypatch.setattr(serve, "ensure_daemon", lambda: False)
    monkeypatch.setattr(app, "open_native", lambda url: True)
    r = CliRunner().invoke(cli.app, ["app"])
    assert r.exit_code == 0 and "window: native" in r.output


def test_cli_app_error_fails(monkeypatch):
    from mxconsole import serve
    monkeypatch.setattr(serve, "ensure_daemon", lambda: False)
    monkeypatch.setattr(app, "open_native", lambda url: False)
    monkeypatch.setattr(app, "open_browser_app", lambda url: False)
    r = CliRunner().invoke(cli.app, ["app"])
    assert r.exit_code != 0 and "could not open a window" in r.output


def test_cli_install_and_uninstall_routing(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(app, "prepare_env", lambda project: calls.append("prepare"))
    monkeypatch.setattr(app, "install", lambda project: calls.append("install") or (tmp_path / "d", tmp_path / "i"))
    monkeypatch.setattr(app, "uninstall", lambda: calls.append("uninstall") or [tmp_path / "d"])
    r = CliRunner().invoke(cli.app, ["app", "--install"])
    assert r.exit_code == 0 and calls == ["prepare", "install"] and "installed" in r.output
    calls.clear()
    r = CliRunner().invoke(cli.app, ["app", "--uninstall"])
    assert r.exit_code == 0 and calls == ["uninstall"] and "removed" in r.output


def test_cli_install_stops_when_env_setup_fails(monkeypatch):
    def boom(project):
        raise app.AppError("setting up the app environment failed")
    monkeypatch.setattr(app, "prepare_env", boom)
    monkeypatch.setattr(app, "install", lambda project: pytest.fail("must not install"))
    r = CliRunner().invoke(cli.app, ["app", "--install"])
    assert r.exit_code != 0 and "failed" in r.output


def test_cli_rejects_install_with_uninstall():
    r = CliRunner().invoke(cli.app, ["app", "--install", "--uninstall"])
    assert r.exit_code != 0 and "pass one of" in r.output


def test_cli_install_on_mac_refuses_before_downloading_the_app_env(monkeypatch):
    calls = []
    monkeypatch.setattr(app.sys, "platform", "darwin")
    monkeypatch.setattr(app, "prepare_env", lambda project: calls.append("prepare"))
    r = CliRunner().invoke(cli.app, ["app", "--install"])
    assert r.exit_code != 0 and ".app bundle comes later" in r.output
    assert calls == []


def test_pixi_is_found_without_path(tmp_path):
    """Under systemd or launchd PATH has no ~/.pixi/bin: pixi's own PIXI_EXE comes first, then PATH, then ~/.pixi."""
    assert app.pixi_exe(env={"PIXI_EXE": "/x/pixi"}, which=lambda n: "/usr/bin/pixi") == "/x/pixi"
    assert app.pixi_exe(env={}, which=lambda n: "/usr/bin/pixi") == "/usr/bin/pixi"
    home_pixi = tmp_path / ".pixi/bin/pixi"
    home_pixi.parent.mkdir(parents=True)
    home_pixi.write_text("")
    assert app.pixi_exe(env={}, which=lambda n: None, home=tmp_path) == str(home_pixi)
    assert app.pixi_exe(env={}, which=lambda n: None, home=tmp_path / "nobody") == "pixi"
