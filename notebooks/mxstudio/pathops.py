"""Cross-platform file-manager handoff for Linux, WSL, macOS, and Windows."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from notebooks.mxstudio import hwinfo


def _inside(target: Path, roots: tuple[Path, ...]) -> bool:
    return any(target == root or target.is_relative_to(root) for root in roots)


def _windows_path(target: Path) -> tuple[str | None, str | None]:
    try:
        proc = subprocess.run(
            ["wslpath", "-w", str(target)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    converted = (proc.stdout or "").strip()
    if proc.returncode != 0 or not converted:
        error = (proc.stderr or "wslpath returned no Windows path").strip()
        return None, error
    return converted, None


def _launch(command: list[str]) -> tuple[bool, str | None]:
    try:
        proc = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=os.environ.copy(),
        )
        time.sleep(0.15)
        code = proc.poll()
        if code not in (None, 0):
            return False, f"{command[0]} exited with code {code}"
        return True, None
    except OSError as exc:
        return False, str(exc)


def reveal_path(
    target: Path,
    *,
    allowed_roots: tuple[Path, ...] = (),
) -> dict:
    """Open a directory (or a file's parent) in the host file manager."""
    resolved = target.expanduser().resolve(strict=False)
    roots = tuple(root.expanduser().resolve(strict=False) for root in allowed_roots)
    if roots and not _inside(resolved, roots):
        return {
            "ok": False,
            "opened": False,
            "target": str(resolved),
            "target_windows": None,
            "opened_with": None,
            "error": "path is outside the allowed mxstudio directories",
        }
    if not resolved.exists():
        return {
            "ok": False,
            "opened": False,
            "target": str(resolved),
            "target_windows": None,
            "opened_with": None,
            "error": "path does not exist",
        }
    reveal_target = resolved if resolved.is_dir() else resolved.parent

    if hwinfo.is_wsl():
        win_path, conversion_error = _windows_path(reveal_target)
        if not win_path:
            return {
                "ok": False,
                "opened": False,
                "target": str(reveal_target),
                "target_windows": None,
                "opened_with": None,
                "error": f"could not translate WSL path: {conversion_error}",
            }
        launchers: list[tuple[str, list[str]]] = []
        explorer = shutil.which("explorer.exe")
        if not explorer and Path("/mnt/c/Windows/explorer.exe").is_file():
            explorer = "/mnt/c/Windows/explorer.exe"
        if explorer:
            launchers.append(("explorer.exe", [explorer, win_path]))
        cmd = shutil.which("cmd.exe")
        if not cmd and Path("/mnt/c/Windows/System32/cmd.exe").is_file():
            cmd = "/mnt/c/Windows/System32/cmd.exe"
        if cmd:
            launchers.append(("cmd.exe", [cmd, "/c", "start", "", win_path]))
        errors = []
        for name, command in launchers:
            opened, error = _launch(command)
            if opened:
                return {
                    "ok": True,
                    "opened": True,
                    "target": str(reveal_target),
                    "target_windows": win_path,
                    "opened_with": name,
                    "error": None,
                }
            if error:
                errors.append(error)
        return {
            "ok": False,
            "opened": False,
            "target": str(reveal_target),
            "target_windows": win_path,
            "opened_with": None,
            "error": "; ".join(errors) or "Windows Explorer is unavailable from WSL",
        }

    if os.name == "nt":
        try:
            os.startfile(str(reveal_target))  # type: ignore[attr-defined]
            return {
                "ok": True,
                "opened": True,
                "target": str(reveal_target),
                "target_windows": str(reveal_target),
                "opened_with": "os.startfile",
                "error": None,
            }
        except OSError as exc:
            error = str(exc)
    else:
        opener = shutil.which("open" if sys_platform_is_macos() else "xdg-open")
        if opener:
            opened, launch_error = _launch([opener, str(reveal_target)])
            if opened:
                return {
                    "ok": True,
                    "opened": True,
                    "target": str(reveal_target),
                    "target_windows": None,
                    "opened_with": Path(opener).name,
                    "error": None,
                }
            error = launch_error or "file manager exited unsuccessfully"
        else:
            error = "no supported file manager command was found"
    return {
        "ok": False,
        "opened": False,
        "target": str(reveal_target),
        "target_windows": None,
        "opened_with": None,
        "error": error,
    }


def sys_platform_is_macos() -> bool:
    import sys

    return sys.platform == "darwin"
