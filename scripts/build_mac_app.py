"""Build dist/omxconsole-mac.zip: omxconsole.app for Apple-silicon Macs (unsigned).

The app is a thin launcher, not a compiled binary: Contents/MacOS/omxconsole runs mxconsole/setup/install.sh from
Contents/Resources. The first launch sets everything up in ~/.mxc/runtime (with a setup page in the browser); later
launches start the tray and open the console. It is a menu-bar app (LSUIElement: no Dock icon). Standard library
only, so it builds anywhere: `pixi run python scripts/build_mac_app.py [--source <tar.gz URL>]`.

Unsigned: macOS asks once. On macOS 15 and later, open it, then System Settings → Privacy & Security → Open Anyway.
"""
from __future__ import annotations

import argparse
import plistlib
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "mxconsole" / "setup"
RESOURCES = ("install.sh", "setup_server.py", "setup.html", "omxconsole.icns")
DEFAULT_SOURCE = "https://github.com/abhisheksreesaila/mxstudio/archive/refs/heads/main.tar.gz"
BUNDLE_ID = "com.omxconsole.app"


def version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


def info_plist(ver: str) -> bytes:
    return plistlib.dumps({
        "CFBundleName": "omxconsole", "CFBundleDisplayName": "omxconsole", "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleExecutable": "omxconsole", "CFBundleIconFile": "omxconsole", "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": ver, "CFBundleVersion": ver, "CFBundleInfoDictionaryVersion": "6.0",
        "LSMinimumSystemVersion": "13.0", "LSArchitecturePriority": ["arm64"], "LSUIElement": True,
        "NSHighResolutionCapable": True,
    })


def launcher(source: str) -> str:
    # Finder starts apps with a bare PATH and no terminal; install.sh only needs /usr/bin tools (curl, tar, open,
    # osascript). The source is baked in at build time so an app always sets up the code it was built for.
    quoted = source.replace("'", "'\\''")
    return ("#!/bin/sh\n"
            "# omxconsole.app: everything happens in Resources/install.sh (first run: setup; later: tray + console).\n"
            'HERE=$(cd "$(dirname "$0")/../Resources" && pwd)\n'
            f"BUILT_FOR='{quoted}'\n"
            'MXC_SOURCE="${MXC_SOURCE:-$BUILT_FOR}"\n'
            'export MXC_SOURCE MXC_SETUP_RESOURCES="$HERE"\n'
            'mkdir -p "$HOME/.mxc/runtime"\n'
            'exec /bin/sh "$HERE/install.sh" >> "$HOME/.mxc/runtime/app-launch.log" 2>&1\n')


def build(out: Path, source: str = DEFAULT_SOURCE) -> Path:
    """Write the zip. Unix modes are stored in each entry so Finder's unzip keeps the launcher executable."""
    out.parent.mkdir(parents=True, exist_ok=True)
    ver = version()

    def add(zf: zipfile.ZipFile, name: str, data: bytes, mode: int) -> None:
        info = zipfile.ZipInfo(f"omxconsole.app/{name}", date_time=(2026, 1, 1, 0, 0, 0))
        info.external_attr = (0o100000 | mode) << 16  # a regular file with these permissions
        info.compress_type = zipfile.ZIP_DEFLATED
        info.create_system = 3  # Unix, so the modes are honoured
        zf.writestr(info, data)

    with zipfile.ZipFile(out, "w") as zf:
        add(zf, "Contents/Info.plist", info_plist(ver), 0o644)
        add(zf, "Contents/PkgInfo", b"APPL????", 0o644)
        add(zf, "Contents/MacOS/omxconsole", launcher(source).encode(), 0o755)
        for name in RESOURCES:
            add(zf, f"Contents/Resources/{name}", (SETUP / name).read_bytes(), 0o755 if name.endswith(".sh") else 0o644)
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", default=DEFAULT_SOURCE, help="the omxconsole .tar.gz the app sets up")
    parser.add_argument("--out", type=Path, default=ROOT / "dist" / "omxconsole-mac.zip")
    args = parser.parse_args()
    print(f"built {build(args.out, args.source)} (omxconsole {version()}, source {args.source})")
