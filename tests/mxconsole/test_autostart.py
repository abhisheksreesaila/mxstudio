"""Start at login (a systemd user unit on Linux, a LaunchAgent on a Mac) and the `mxc` shim on PATH."""
from __future__ import annotations

import plistlib
from pathlib import Path

import pytest

from mxconsole import autostart

PROJECT = Path("/home/me/My Projects/mxstudio")
PIXI = "/home/me/.pixi/bin/pixi"


def runner():
    calls = []
    return calls, lambda cmd: calls.append(cmd) or 0


# --- Linux -----------------------------------------------------------------------------------------

def test_unit_runs_the_tray_through_pixi_in_the_graphical_session():
    unit = autostart.unit_text(PROJECT, PIXI)
    assert 'ExecStart="/home/me/.pixi/bin/pixi" run --manifest-path "/home/me/My Projects/mxstudio/pixi.toml" mxc tray' in unit
    assert "WantedBy=graphical-session.target" in unit and "PartOf=graphical-session.target" in unit
    assert "Restart=on-failure" in unit
    assert f"RestartPreventExitStatus={autostart.ALREADY_RUNNING}" in unit


def test_unit_escapes_percent_and_dollar():
    unit = autostart.unit_text(Path("/p/100%$x"), PIXI)
    assert '"/p/100%%$$x/pixi.toml"' in unit


def test_on_writes_the_unit_and_enables_it(tmp_path):
    calls, run = runner()
    autostart.set_enabled(True, PROJECT, platform="linux", home=tmp_path, pixi=PIXI, runner=run)
    unit = tmp_path / ".config/systemd/user" / autostart.UNIT
    assert unit.read_text() == autostart.unit_text(PROJECT, PIXI)
    assert calls == [["systemctl", "--user", "daemon-reload"], ["systemctl", "--user", "enable", autostart.UNIT]]
    assert autostart.enabled(platform="linux", home=tmp_path, runner=run)


def test_off_disables_and_removes_the_unit(tmp_path):
    _, run = runner()
    autostart.set_enabled(True, PROJECT, platform="linux", home=tmp_path, pixi=PIXI, runner=run)
    calls, run = runner()
    autostart.set_enabled(False, PROJECT, platform="linux", home=tmp_path, pixi=PIXI, runner=run)
    assert calls == [["systemctl", "--user", "disable", autostart.UNIT], ["systemctl", "--user", "daemon-reload"]]
    assert not autostart.enabled(platform="linux", home=tmp_path, runner=run)


def test_off_when_already_off_does_nothing(tmp_path):
    calls, run = runner()
    autostart.set_enabled(False, PROJECT, platform="linux", home=tmp_path, pixi=PIXI, runner=run)
    assert calls == []


def test_systemctl_failing_is_an_error_and_leaves_it_off(tmp_path):
    with pytest.raises(autostart.AutostartError, match="systemctl"):
        autostart.set_enabled(True, PROJECT, platform="linux", home=tmp_path, pixi=PIXI, runner=lambda cmd: 1)
    assert not (tmp_path / ".config/systemd/user" / autostart.UNIT).exists()


def test_a_failed_reenable_leaves_an_existing_unit_in_place(tmp_path):
    _, run = runner()
    autostart.set_enabled(True, PROJECT, platform="linux", home=tmp_path, pixi=PIXI, runner=run)
    with pytest.raises(autostart.AutostartError):
        autostart.set_enabled(True, PROJECT, platform="linux", home=tmp_path, pixi=PIXI, runner=lambda cmd: 1)
    assert (tmp_path / ".config/systemd/user" / autostart.UNIT).exists()


def test_status_asks_systemd_so_a_manual_disable_shows_off(tmp_path):
    _, run = runner()
    autostart.set_enabled(True, PROJECT, platform="linux", home=tmp_path, pixi=PIXI, runner=run)
    asked = []
    disabled = lambda cmd: asked.append(cmd) or 1
    assert autostart.enabled(platform="linux", home=tmp_path, runner=disabled) is False
    assert asked == [["systemctl", "--user", "is-enabled", "--quiet", autostart.UNIT]]


def test_refuses_to_write_a_login_entry_with_a_bare_pixi(tmp_path):
    calls, run = runner()
    with pytest.raises(autostart.AutostartError, match="can't find pixi"):
        autostart.set_enabled(True, PROJECT, platform="linux", home=tmp_path, pixi="pixi", runner=run)
    assert calls == [] and not (tmp_path / ".config/systemd/user").exists()


def test_tests_cannot_reach_the_real_systemd():
    with pytest.raises(AssertionError, match="inject a runner"):
        autostart._default_runner(["systemctl", "--user", "disable", autostart.UNIT])


# --- Mac -------------------------------------------------------------------------------------------

def test_mac_writes_a_launch_agent_that_runs_at_login(tmp_path):
    calls, run = runner()
    autostart.set_enabled(True, PROJECT, platform="darwin", home=tmp_path, pixi=PIXI, runner=run)
    path = tmp_path / "Library/LaunchAgents" / autostart.PLIST
    agent = plistlib.loads(path.read_bytes())
    assert agent["ProgramArguments"] == [PIXI, "run", "--manifest-path", str(PROJECT / "pixi.toml"), "mxc", "tray"]
    assert agent["RunAtLoad"] is True and agent["Label"] == autostart.LABEL
    assert calls == []  # loaded at the next login; the tray is already running now
    assert autostart.enabled(platform="darwin", home=tmp_path)
    autostart.set_enabled(False, PROJECT, platform="darwin", home=tmp_path, pixi=PIXI, runner=run)
    assert not path.exists()


def test_other_platforms_are_refused(tmp_path):
    with pytest.raises(autostart.AutostartError):
        autostart.set_enabled(True, PROJECT, platform="win32", home=tmp_path, pixi=PIXI)
    assert autostart.enabled(platform="win32", home=tmp_path) is False


# --- the `mxc` shim --------------------------------------------------------------------------------

def test_shim_runs_mxc_through_pixi_and_keeps_the_arguments(tmp_path):
    path = autostart.install_shim(PROJECT, home=tmp_path, pixi=PIXI)
    assert path == tmp_path / ".local/bin/mxc"
    text = path.read_text()
    assert text.startswith("#!/bin/sh\n") and autostart.SHIM_MARK in text
    assert 'exec "/home/me/.pixi/bin/pixi" run --manifest-path "/home/me/My Projects/mxstudio/pixi.toml" mxc "$@"' in text
    assert path.stat().st_mode & 0o111


def test_shim_quotes_shell_specials(tmp_path):
    text = autostart.install_shim(Path('/p/a"b$c`d\\e'), home=tmp_path, pixi=PIXI).read_text()
    assert '"/p/a\\"b\\$c\\`d\\\\e/pixi.toml"' in text


def test_shim_never_overwrites_someone_elses_mxc(tmp_path):
    other = tmp_path / ".local/bin/mxc"
    other.parent.mkdir(parents=True)
    other.write_text("#!/bin/sh\necho mine\n")
    with pytest.raises(autostart.AutostartError, match="already"):
        autostart.install_shim(PROJECT, home=tmp_path, pixi=PIXI)
    assert autostart.remove_shim(home=tmp_path) is None
    assert other.read_text() == "#!/bin/sh\necho mine\n"


def test_shim_reinstall_and_remove(tmp_path):
    autostart.install_shim(PROJECT, home=tmp_path, pixi=PIXI)
    autostart.install_shim(PROJECT, home=tmp_path, pixi=PIXI)  # ours: replaced in place
    assert autostart.remove_shim(home=tmp_path) == tmp_path / ".local/bin/mxc"
    assert not (tmp_path / ".local/bin/mxc").exists()


# --- `mxc app --autostart` -------------------------------------------------------------------------

def _cli(monkeypatch, tmp_path, *args):
    from typer.testing import CliRunner

    from mxconsole import cli
    calls = []
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(autostart, "_default_runner", lambda cmd: calls.append(cmd) or 0)
    monkeypatch.setattr(autostart.sys, "platform", "linux")
    return calls, CliRunner().invoke(cli.app, ["app", *args])


def test_cli_autostart_on_status_off(monkeypatch, tmp_path):
    calls, r = _cli(monkeypatch, tmp_path, "--autostart", "on")
    assert r.exit_code == 0 and "start at login: on (from the next login)" in r.output
    assert ["systemctl", "--user", "enable", autostart.UNIT] in calls
    _, r = _cli(monkeypatch, tmp_path, "--autostart", "status")
    assert "start at login: on" in r.output
    _, r = _cli(monkeypatch, tmp_path, "--autostart", "off")
    assert r.exit_code == 0 and "start at login: off" in r.output


def test_cli_autostart_rejects_other_words(monkeypatch, tmp_path):
    calls, r = _cli(monkeypatch, tmp_path, "--autostart", "yes")
    assert r.exit_code != 0 and "on, off or status" in r.output and calls == []


def test_tests_never_see_the_real_home():
    assert "mxc-home" not in str(Path.home()) and Path.home().name == "home" and "pytest" in str(Path.home())


def test_first_run_turns_on_login_and_the_shim(tmp_path):
    calls, run = runner()
    said = autostart.first_run(PROJECT, platform="linux", home=tmp_path, pixi=PIXI, runner=run)
    assert ["systemctl", "--user", "enable", autostart.UNIT] in calls
    assert (tmp_path / ".local/bin/mxc").exists() and "on from the next login" in said


def test_first_run_leaves_a_foreign_mxc_and_says_so(tmp_path):
    other = tmp_path / ".local/bin/mxc"
    other.parent.mkdir(parents=True)
    other.write_text("mine")
    _, run = runner()
    said = autostart.first_run(PROJECT, platform="darwin", home=tmp_path, pixi=PIXI, runner=run)
    assert other.read_text() == "mine" and "left it alone" in said
