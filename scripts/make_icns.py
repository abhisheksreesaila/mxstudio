"""Make mxconsole/setup/omxconsole.icns (the Mac app's icon) from the console's favicon.svg.

Run in the `app` environment, which has Qt: `pixi run -e app python scripts/make_icns.py`. The result is committed,
so building the .app (scripts/build_mac_app.py) needs nothing but the standard library. An .icns is a header plus
one PNG per size; the tile sits on macOS's icon grid (824 of 1024 px, centred), as Big Sur-style icons do.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SVG = ROOT / "mxconsole" / "console" / "favicon.svg"
OUT = ROOT / "mxconsole" / "setup" / "omxconsole.icns"
TYPES = [(b"icp4", 16), (b"icp5", 32), (b"icp6", 64), (b"ic07", 128), (b"ic08", 256), (b"ic09", 512), (b"ic10", 1024)]
GRID = 824 / 1024


def png(size: int) -> bytes:
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    side = size * GRID
    QSvgRenderer(str(SVG)).render(painter, QRectF((size - side) / 2, (size - side) / 2, side, side))
    painter.end()
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buf, "PNG")
    return bytes(data)


def icns(chunks: list[tuple[bytes, bytes]]) -> bytes:
    body = b"".join(kind + struct.pack(">I", len(data) + 8) + data for kind, data in chunks)
    return b"icns" + struct.pack(">I", len(body) + 8) + body


if __name__ == "__main__":
    from PySide6.QtGui import QGuiApplication
    app = QGuiApplication(sys.argv[:1] + ["-platform", "offscreen"])
    OUT.write_bytes(icns([(kind, png(size)) for kind, size in TYPES]))
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")
