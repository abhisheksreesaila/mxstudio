"""The tray icon itself. Runs in the `app` pixi environment (PySide6), never imported by the rest of mxconsole.
It only draws: `tray.py` sends the menu as JSON lines on stdin, and each click goes back as {"click": id} on
stdout. Run as a script: `python tray_qt.py <icon.svg>`."""
from __future__ import annotations

import json
import sys
import threading

GREEN, GREY = "#16a34a", "#8a8a90"


def main(icon_path: str) -> int:
    from PySide6.QtCore import QObject, Qt, Signal
    from PySide6.QtGui import QAction, QIcon, QPixmap
    from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("omxconsole")
    svg = open(icon_path, encoding="utf-8").read()

    def icon(busy: bool) -> QIcon:
        pixmap = QPixmap()
        pixmap.loadFromData(svg.replace(GREEN, GREEN if busy else GREY).encode(), "SVG")
        return QIcon(pixmap)

    icons = {True: icon(True), False: icon(False)}
    tray = QSystemTrayIcon(icons[False])
    tray.setToolTip("omxconsole")
    menu = QMenu()
    tray.setContextMenu(menu)

    def click(item: str) -> None:
        print(json.dumps({"click": item}), flush=True)

    # A left click opens the console; the menu is on the right click (and on a Mac, any click).
    tray.activated.connect(lambda reason: click("open") if reason == QSystemTrayIcon.ActivationReason.Trigger else None)

    def show(msg: dict) -> None:
        if msg.get("quit"):
            app.quit()
            return
        if "message" in msg:
            tray.showMessage("omxconsole", msg["message"], icons[False], 8000)
            return
        tray.setToolTip(msg.get("tooltip", "omxconsole"))
        tray.setIcon(icons[bool(msg.get("busy"))])
        menu.clear()
        for item in msg.get("items", []):
            if item.get("sep"):
                menu.addSeparator()
                continue
            action = QAction(item["label"], menu)
            action.setEnabled(item.get("enabled", True))
            if item.get("checkable"):
                action.setCheckable(True)
                action.setChecked(bool(item.get("checked")))
            action.triggered.connect(lambda _=False, i=item["id"]: click(i))
            menu.addAction(action)

    class Inbox(QObject):
        got = Signal(dict)

    inbox = Inbox()
    inbox.got.connect(show, Qt.ConnectionType.QueuedConnection)  # always run on the GUI thread

    def read() -> None:
        for line in sys.stdin:
            try:
                inbox.got.emit(json.loads(line))
            except ValueError:
                pass
        inbox.got.emit({"quit": True})  # tray.py is gone

    threading.Thread(target=read, daemon=True).start()
    tray.show()
    return app.exec()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: tray_qt.py <icon.svg>")
    sys.exit(main(sys.argv[1]))
