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


def project_dir() -> Path:
    """The checkout holding pixi.toml (this package is installed editable from it)."""
    return Path(__file__).resolve().parents[1]


def native_command(project: Path, url: str, pixi: str | None = None) -> list[str]:
    return [pixi or shutil.which("pixi") or "pixi", "run", "--manifest-path", str(project / "pixi.toml"),
            "-e", APP_ENV, "python", str(Path(__file__).with_name("window.py")), url]


def fallback_command(url: str, platform: str = sys.platform,
                     which: Callable[[str], str | None] = shutil.which) -> list[str] | None:
    if platform == "darwin":
        return ["open", "-na", "Google Chrome", "--args", f"--app={url}"]
    for name in BROWSERS:
        found = which(name)
        if found:
            return [found, f"--app={url}"]
    return None


def open_native(url: str) -> bool:
    """Open the pywebview window and wait until it is closed. False if it could not open."""
    try:
        return subprocess.run(native_command(project_dir(), url), stdin=subprocess.DEVNULL).returncode == 0
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
        say("started the mxconsole daemon")
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


def desktop_entry(project: Path, icon: str | Path, pixi: str | None = None) -> str:
    pixi = pixi or shutil.which("pixi") or "pixi"
    return ("[Desktop Entry]\nType=Application\nName=mxconsole\nComment=Local AI runtime on Modular MAX\n"
            f"Exec={pixi} run mxc app\nPath={project}\nIcon={icon}\nTerminal=false\n"
            "Categories=Development;Utility;\n")


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
