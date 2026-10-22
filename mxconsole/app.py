"""`mxc app`: the console in its own native window.

Window backend: pywebview with its Qt backend (PySide6 / QtWebEngine), from conda-forge, in a separate
pixi environment `app`. Why not GTK: pywebview's GTK backend needs PyGObject + WebKit2GTK in the same
Python, which on Linux means system packages the pixi Python can't import; Qt from conda-forge is
self-contained and the same on Linux and macOS. The heavy GUI deps stay out of the default environment,
so `mxc app` runs the window as a child: `pixi run -e app python window.py <url>`.
If that can't open (no `app` env, no pixi, no display), it falls back to a Chromium/Chrome `--app=<url>`
window (Mac: Google Chrome via `open`), and says which path it took.

Closing the window stops nothing: the daemon is started detached and keeps running.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

APP_ENV = "app"
DESKTOP_NAME = "mxconsole.desktop"
ICON_NAME = "mxconsole.svg"
BROWSERS = ("chromium", "chromium-browser", "google-chrome-stable", "google-chrome", "chrome", "brave-browser")


class AppError(RuntimeError):
    pass


def pixi_exe(env: dict | None = None, which: Callable[[str], str | None] = shutil.which,
             home: Path | None = None) -> str:
    """The pixi binary. Under systemd or launchd PATH has no ~/.pixi/bin, so pixi's own PIXI_EXE (set for
    everything `pixi run` starts) comes first, then PATH, then pixi's default install place."""
    found = (os.environ if env is None else env).get("PIXI_EXE") or which("pixi")
    if found:
        return found
    default = (home or Path.home()) / ".pixi/bin/pixi"
    return str(default) if default.exists() else "pixi"


def project_dir() -> Path:
    """The checkout holding pixi.toml (this package is installed editable from it)."""
    return Path(__file__).resolve().parents[1]


def storage_dir() -> Path:
    """Where the window keeps its browser storage (theme, view prefs), next to the rest of ~/.mxc."""
    from mxconsole.store import mxc_home
    return mxc_home() / "webview"


def native_command(project: Path, url: str, storage: Path | str, pixi: str | None = None) -> list[str]:
    return [pixi or pixi_exe(), "run", "--manifest-path", str(project / "pixi.toml"),
            "-e", APP_ENV, "python", str(Path(__file__).with_name("window.py")), url, str(storage)]


MAC_CHROME = ("/Applications/Google Chrome.app", "~/Applications/Google Chrome.app")


def fallback_command(url: str, platform: str = sys.platform,
                     which: Callable[[str], str | None] = shutil.which,
                     exists: Callable[[str], bool] = lambda p: Path(p).expanduser().exists()) -> list[str] | None:
    if platform == "darwin":
        if not any(exists(p) for p in MAC_CHROME):
            return None
        return ["open", "-na", "Google Chrome", "--args", f"--app={url}"]
    for name in BROWSERS:
        found = which(name)
        if found:
            return [found, f"--app={url}"]
    return None


def open_native(url: str) -> bool:
    """Open the pywebview window and wait until it is closed. False if it could not open."""
    try:
        return subprocess.run(native_command(project_dir(), url, storage_dir()), stdin=subprocess.DEVNULL).returncode == 0
    except OSError:
        return False


def open_browser_app(url: str) -> bool:
    cmd = fallback_command(url)
    if not cmd:
        return False
    try:
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except OSError:
        return False
    return True


def run(ensure_daemon: Callable[[], bool], open_window: Callable[[str], bool],
        open_fallback: Callable[[str], bool], url: str, say: Callable[[str], None] = print) -> str:
    """Make sure the daemon is up (a no-op when it already answers), then open the window.
    Returns "native" or "browser-app"."""
    if ensure_daemon():
        say("started the omxconsole daemon")
    if open_window(url):
        return "native"
    say("native window unavailable (is the pixi `app` environment installed?); using a browser app window")
    if open_fallback(url):
        return "browser-app"
    raise AppError("could not open a window: no pywebview and no Chromium/Chrome found. "
                   f"Open {url} in a browser.")


# --- Linux launcher -------------------------------------------------------------------------------

def _require_linux(platform: str) -> None:
    if platform == "darwin":
        raise AppError("on macOS a .app bundle comes later; for now run `mxc app` from a terminal")
    if not platform.startswith("linux"):
        raise AppError(f"launchers are not supported on {platform}")


def _paths(home: Path) -> tuple[Path, Path]:
    return (home / ".local/share/applications" / DESKTOP_NAME, home / ".local/share/icons" / ICON_NAME)


def _arg(value: str | Path) -> str:
    """One Exec argument per the Desktop Entry spec: double-quoted, " ` $ and backslash escaped, % doubled.
    (The spec's string escaping then doubles each backslash again.)"""
    out = str(value).replace("\\", "\\\\").replace("%", "%%")
    for ch in '"`$':
        out = out.replace(ch, "\\" + ch)
    return '"' + out.replace("\\", "\\\\") + '"'


def desktop_entry(project: Path, icon: str | Path, pixi: str | None = None) -> str:
    pixi = pixi or pixi_exe()
    exec_line = f"{_arg(pixi)} run --manifest-path {_arg(Path(project) / 'pixi.toml')} mxc app"
    return ("[Desktop Entry]\nType=Application\nName=omxconsole\nComment=Local AI runtime on Modular MAX\n"
            f"Exec={exec_line}\nIcon={icon}\nTerminal=false\n"
            "Categories=Development;Utility;\n")


def prepare_env(project: Path, pixi: str | None = None, runner: Callable[[list[str]], int] | None = None) -> None:
    """`pixi install -e app`, output visible, so the first launch from the menu isn't a silent wait."""
    cmd = [pixi or pixi_exe(), "install", "--manifest-path", str(project / "pixi.toml"),
           "-e", APP_ENV]
    run_ = runner or (lambda c: subprocess.run(c).returncode)
    try:
        code = run_(cmd)
    except OSError as e:
        raise AppError(f"could not run pixi to set up the app environment: {e}")
    if code != 0:
        raise AppError(f"setting up the app environment failed (`{' '.join(cmd)}` exited {code}); "
                       "fix that and run `mxc app --install` again")


def install(project: Path, home: Path | None = None, platform: str = sys.platform,
            pixi: str | None = None) -> tuple[Path, Path]:
    _require_linux(platform)
    desktop, icon = _paths(home or Path.home())
    icon.parent.mkdir(parents=True, exist_ok=True)
    desktop.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(__file__).with_name("console") / "favicon.svg", icon)
    desktop.write_text(desktop_entry(project, icon, pixi))
    return desktop, icon


def uninstall(home: Path | None = None, platform: str = sys.platform) -> list[Path]:
    _require_linux(platform)
    removed = []
    for path in _paths(home or Path.home()):
        if path.exists():
            path.unlink()
            removed.append(path)
    return removed
