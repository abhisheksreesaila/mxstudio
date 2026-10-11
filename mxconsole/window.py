"""The native window itself. Runs in the `app` pixi environment (pywebview + Qt), never imported by the
rest of mxconsole, so the default environment stays light. Run as a script: `python window.py <url>`."""
from __future__ import annotations

import os
import sys


def main(url: str, storage: str) -> int:
    # Hardware GPU compositing rendered a blank page on Wayland/Hyprland (NVIDIA); the console is light, so
    # software rendering costs nothing. Override with QTWEBENGINE_CHROMIUM_FLAGS.
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
    import webview  # only present in the `app` environment

    # Browser storage persists in `storage` (under ~/.mxc): the console keeps its theme and view
    # preferences in localStorage. Nothing else is remembered (no window size or position).
    webview.create_window("mxconsole", url, width=1440, height=900)
    webview.start(gui="qt", private_mode=False, storage_path=storage)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: window.py <url> <storage-dir>")
    sys.exit(main(sys.argv[1], sys.argv[2]))
