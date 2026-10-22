"""F-051: dist/omxconsole-mac.zip holds a well-formed omxconsole.app whose launcher runs the first-run setup."""
from __future__ import annotations

import importlib.util
import json
import plistlib
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("build_mac_app", ROOT / "scripts" / "build_mac_app.py")
build_mac_app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_mac_app)

from test_setup import free_port, setup_server, tarball  # noqa: E402  (same folder)

APP = "omxconsole.app/Contents"


def built(tmp_path, source="https://example.invalid/src.tar.gz"):
    return zipfile.ZipFile(build_mac_app.build(tmp_path / "omxconsole-mac.zip", source))


def test_info_plist_makes_a_menu_bar_app_for_apple_silicon(tmp_path):
    info = plistlib.loads(built(tmp_path).read(f"{APP}/Info.plist"))
    assert info["CFBundleExecutable"] == "omxconsole" and info["CFBundleIconFile"] == "omxconsole"
    assert info["CFBundleIdentifier"] == "com.omxconsole.app" and info["CFBundlePackageType"] == "APPL"
    assert info["LSUIElement"] is True and info["LSArchitecturePriority"] == ["arm64"]
    assert info["CFBundleShortVersionString"] == build_mac_app.version()


def test_bundle_has_the_launcher_and_the_setup_files_with_their_modes(tmp_path):
    zf = built(tmp_path)
    modes = {i.filename: (i.external_attr >> 16) & 0o777 for i in zf.infolist()}
    assert modes[f"{APP}/MacOS/omxconsole"] == 0o755 and modes[f"{APP}/Resources/install.sh"] == 0o755
    assert modes[f"{APP}/Info.plist"] == 0o644
    for name in build_mac_app.RESOURCES:
        assert zf.read(f"{APP}/Resources/{name}") == (ROOT / "mxconsole" / "setup" / name).read_bytes()


def test_launcher_bakes_in_the_source_and_is_valid_sh(tmp_path):
    text = built(tmp_path, "https://x/it's.tar.gz").read(f"{APP}/MacOS/omxconsole").decode()
    assert text.startswith("#!/bin/sh\n")
    assert subprocess.run(["sh", "-n"], input=text, text=True).returncode == 0
    out = subprocess.run(["sh", "-c", text.split("export")[0] + 'printf %s "$MXC_SOURCE"'], capture_output=True,
                         text=True, env={"PATH": "/usr/bin:/bin"}).stdout
    assert out == "https://x/it's.tar.gz"


def test_icon_is_an_icns_with_every_size():
    data = (ROOT / "mxconsole" / "setup" / "omxconsole.icns").read_bytes()
    assert data[:4] == b"icns" and struct.unpack(">I", data[4:8])[0] == len(data)
    kinds, i = {}, 8
    while i < len(data):
        kind, size = data[i:i + 4], struct.unpack(">I", data[i + 4:i + 8])[0]
        kinds[kind] = data[i + 8:i + 16]
        i += size
    assert set(kinds) == {b"icp4", b"icp5", b"icp6", b"ic07", b"ic08", b"ic09", b"ic10"}
    assert all(head == b"\x89PNG\r\n\x1a\n" for head in kinds.values())


def test_the_unzipped_app_sets_up_from_nothing(tmp_path):
    """As Finder would: unzip (keeping modes), then run Contents/MacOS/omxconsole with a fake pixi."""
    zf = built(tmp_path, str(tarball(tmp_path / "src.tar.gz")))
    apps = tmp_path / "Applications"
    for info in zf.infolist():
        target = apps / info.filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(zf.read(info))
        target.chmod((info.external_attr >> 16) & 0o777)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "pixi").write_text(f"#!/bin/sh\nexec {sys.executable} {Path(__file__).with_name('fake_pixi.py')} \"$@\"\n")
    (bin_dir / "pixi").chmod(0o755)
    home = tmp_path / "home"
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(home), "MXC_PORT": str(free_port()),
           "MXC_SETUP_PORT": str(free_port()), "MXC_SETUP_NO_BROWSER": "1", "MXC_SETUP_NO_LOGIN": "1",
           "MXC_SETUP_LINGER": "0", "FAKE_PIXI_CALLS": str(tmp_path / "calls.jsonl"), "FAKE_TRAY_SECONDS": "8"}
    r = subprocess.run([str(apps / APP / "MacOS" / "omxconsole")], env=env, capture_output=True, text=True, timeout=120)
    rt = home / ".mxc" / "runtime"
    assert r.returncode == 0, (rt / "app-launch.log").read_text()
    assert setup_server.read_state((rt / "setup.log").read_text(), 0)["state"] == "done"
    calls = [json.loads(l) for l in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert ["run", "--manifest-path", str(rt / "app" / "pixi.toml"), "mxc", "tray"] in calls
