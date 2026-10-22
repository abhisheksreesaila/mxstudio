"""Start the tray at login, and put `mxc` on PATH.

Linux: a systemd user unit, part of the graphical session (so the bar and the Wayland/D-Bus variables are there),
restarted on failure because the tray can come up before the bar's tray host does. Mac: a LaunchAgent that runs
at login. Both run `pixi run --manifest-path <project>/pixi.toml mxc tray`. Switching on doesn't start a tray now
(the one clicking the checkbox is already running); it takes effect at the next login.

The shim is `~/.local/bin/mxc`, a two-line script that runs `mxc` through the project's pixi, so `mxc` works in
any terminal and keeps the current directory. It never replaces an `mxc` it didn't write.
"""
from __future__ import annotations

import plistlib
import subprocess
import sys
from pathlib import Path
from typing import Callable

UNIT = "omxconsole-tray.service"
LABEL = "com.omxconsole.tray"
PLIST = f"{LABEL}.plist"
ALREADY_RUNNING = 3  # `mxc tray`'s exit code when another tray holds the lock: not worth a restart
SHIM_MARK = "# written by `mxc app --install` (omxconsole)"


class AutostartError(RuntimeError):
    pass


def _pixi(pixi: str | None) -> str:
    from mxconsole.app import pixi_exe
    return pixi or pixi_exe()


def tray_command(project: Path, pixi: str | None = None) -> list[str]:
    return [_pixi(pixi), "run", "--manifest-path", str(Path(project) / "pixi.toml"), "mxc", "tray"]


def _path(platform: str, home: Path) -> Path | None:
    if platform.startswith("linux"):
        return home / ".config/systemd/user" / UNIT
    if platform == "darwin":
        return home / "Library/LaunchAgents" / PLIST
    return None


def _sd_arg(value: str) -> str:
    """One ExecStart argument: double-quoted, with systemd's specifiers (% and $) and C escapes neutralised."""
    out = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$")
    return f'"{out}"'


def unit_text(project: Path, pixi: str | None = None) -> str:
    cmd = tray_command(project, pixi)
    exec_line = " ".join([_sd_arg(cmd[0]), "run", "--manifest-path", _sd_arg(cmd[3]), "mxc", "tray"])
    return ("[Unit]\nDescription=omxconsole tray icon\nPartOf=graphical-session.target\n"
            "After=graphical-session.target\n\n"
            f"[Service]\nExecStart={exec_line}\nRestart=on-failure\nRestartSec=5\n"
            f"RestartPreventExitStatus={ALREADY_RUNNING}\n\n"
            "[Install]\nWantedBy=graphical-session.target\n")


def plist_bytes(project: Path, pixi: str | None = None) -> bytes:
    return plistlib.dumps({"Label": LABEL, "ProgramArguments": tray_command(project, pixi), "RunAtLoad": True,
                           "ProcessType": "Interactive"})


def enabled(platform: str = sys.platform, home: Path | None = None,
            runner: Callable[[list[str]], int] | None = None) -> bool:
    """On Linux, systemd's own answer (a manual `systemctl --user disable` counts); on a Mac, the plist."""
    path = _path(platform, home or Path.home())
    if not (path and path.exists()):
        return False
    if platform.startswith("linux"):
        try:
            return (runner or _default_runner)(["systemctl", "--user", "is-enabled", "--quiet", UNIT]) == 0
        except OSError:
            return True  # no systemctl to ask: the unit file is the best answer
    return True


def _default_runner(cmd: list[str]) -> int:
    return subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode


def set_enabled(on: bool, project: Path, platform: str = sys.platform, home: Path | None = None,
                pixi: str | None = None, runner: Callable[[list[str]], int] | None = None) -> None:
    path = _path(platform, home or Path.home())
    if path is None:
        raise AutostartError(f"start at login isn't supported on {platform}")
    run = runner or _default_runner
    if on and not Path(_pixi(pixi)).is_absolute():
        raise AutostartError("can't find pixi (looked at PIXI_EXE, PATH and ~/.pixi/bin); at login PATH won't "
                             "have it either. Run this through `pixi run mxc ...`")

    def systemctl(*args: str) -> None:
        cmd = ["systemctl", "--user", *args]
        try:
            code = run(cmd)
        except OSError as e:
            raise AutostartError(f"could not run systemctl: {e}")
        if code != 0:
            raise AutostartError(f"`{' '.join(cmd)}` failed (exit {code})")

    linux = platform.startswith("linux")
    if on:
        path.parent.mkdir(parents=True, exist_ok=True)
        if linux:
            existed = path.exists()
            path.write_text(unit_text(project, pixi))
            try:
                systemctl("daemon-reload")
                systemctl("enable", UNIT)
            except AutostartError:
                if not existed:  # undo only what this call wrote; an earlier enablement stays as it was
                    path.unlink(missing_ok=True)
                raise
        else:
            path.write_bytes(plist_bytes(project, pixi))
        return
    if not path.exists():
        return
    if linux:
        systemctl("disable", UNIT)
        path.unlink()
        systemctl("daemon-reload")
    else:
        path.unlink()


# --- the `mxc` shim --------------------------------------------------------------------------------

def _sh_arg(value: str) -> str:
    out = value
    for ch in ("\\", '"', "$", "`"):
        out = out.replace(ch, "\\" + ch)
    return f'"{out}"'


def shim_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".local/bin/mxc"


def _ours(path: Path) -> bool:
    try:
        return SHIM_MARK in path.read_text(errors="replace")
    except OSError:
        return False


def install_shim(project: Path, home: Path | None = None, pixi: str | None = None) -> Path:
    path = shim_path(home)
    if (path.exists() or path.is_symlink()) and not _ours(path):
        raise AutostartError(f"{path} already exists and wasn't written by omxconsole; left it alone")
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest = str(Path(project) / "pixi.toml")
    path.write_text(f'#!/bin/sh\n{SHIM_MARK}\nexec {_sh_arg(_pixi(pixi))} run --manifest-path {_sh_arg(manifest)} mxc "$@"\n')
    path.chmod(0o755)
    return path


def remove_shim(home: Path | None = None) -> Path | None:
    path = shim_path(home)
    if path.exists() and _ours(path):
        path.unlink()
        return path
    return None


def first_run(project: Path, platform: str = sys.platform, home: Path | None = None, pixi: str | None = None,
              runner: Callable[[list[str]], int] | None = None) -> str:
    """What the first-run setup does once everything is installed: start at login, and `mxc` on PATH.
    A foreign `mxc` is left alone (and said so); failing to turn on start at login raises."""
    set_enabled(True, project, platform=platform, home=home, pixi=pixi, runner=runner)
    try:
        shim = f"`mxc` in {install_shim(project, home=home, pixi=pixi).parent}"
    except AutostartError as e:
        shim = str(e)
    return f"on from the next login; {shim}"


if __name__ == "__main__":  # `python -m mxconsole.autostart`, run by the first-run setup (mxconsole/setup/install.sh)
    from mxconsole.app import project_dir
    try:
        print(first_run(project_dir()))
    except AutostartError as e:
        sys.exit(str(e))
