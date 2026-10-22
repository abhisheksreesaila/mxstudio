"""`mxc tray`: an omxconsole icon in the system tray (Waybar/quickshell on Linux, the menu bar on a Mac).

Same split as `mxc app`: the icon and its menu are drawn by `tray_qt.py`, a small Qt process in the pixi `app`
environment, and everything it shows or does is decided here, in the default environment, where it can be
tested without Qt. The two talk in JSON lines: this side writes the menu ({"tooltip", "busy", "items"}) or
{"message"} / {"quit"}; the Qt side writes {"click": <item id>}.

The status line follows the console's Beacon: a pull, a port, a crashed model, a model starting, models
running, else ready. Quit stops the daemon only when the tray started it.
"""
from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

POLL_SECONDS = 2.0


# --- what the tray says ----------------------------------------------------------------------------

def _running(jobs: list[dict]) -> dict | None:
    return next((j for j in jobs if j.get("status") == "running"), None)


def status_line(s: dict) -> str:
    if not s.get("up"):
        return "Server stopped"
    pull = _running(s.get("pulls", []))
    if pull:
        name, pct = pull.get("name") or pull.get("ref"), pull.get("percent")
        return f"Pulling {name}" if pct is None else f"Pulling {name} · {round(pct)}%"
    port = _running(s.get("ports", []))
    if port:
        stages = {st.get("name"): st for st in port.get("stages", [])}.values()  # a retried stage keeps its newest row
        done = sum(1 for st in stages if st.get("status") in ("done", "skipped"))
        name = port.get("name") or port.get("ref")
        return f"Porting {name} · step {min(done + 1, len(stages))} of {len(stages)}" if stages else f"Porting {name}"
    models = s.get("models", [])
    for state, text in (("crashed", "{} crashed"), ("starting", "Starting {}")):
        hit = next((x for x in models if x.get("state") == state), None)
        if hit:
            return text.format(hit["name"])
    running = [x["name"] for x in models if x.get("state") == "ready"]
    if len(running) == 1:
        return f"{running[0]} running"
    if running:
        return f"{len(running)} models running"
    return "Ready · nothing loaded"


def busy(s: dict) -> bool:
    """Something is happening: a pull or port running, or a model loaded, starting or crashed."""
    return bool(s.get("up")) and bool(
        _running(s.get("pulls", [])) or _running(s.get("ports", []))
        or any(x.get("state") in ("ready", "starting", "crashed") for x in s.get("models", [])))


def menu(s: dict, autostart: bool) -> dict:
    status = status_line(s)
    return {
        "tooltip": f"omxconsole · {status}",
        "busy": busy(s),
        "items": [
            {"id": "status", "label": status, "enabled": False},
            {"sep": True},
            {"id": "open", "label": "Open console"},
            {"id": "server", "label": "Stop server" if s.get("up") else "Start server"},
            {"id": "autostart", "label": "Start at login", "checkable": True, "checked": autostart},
            {"sep": True},
            {"id": "quit", "label": "Quit omxconsole"},
        ],
    }


# --- what a click does -----------------------------------------------------------------------------

class Controller:
    """The tray's behaviour, with every side effect injected."""

    def __init__(self, fetch: Callable[[], dict], ensure_daemon: Callable[[], bool], shutdown: Callable[[], object],
                 open_console: Callable[[], object], get_autostart: Callable[[], bool],
                 set_autostart: Callable[[bool], object]):
        self._fetch, self._ensure, self._shutdown = fetch, ensure_daemon, shutdown
        self._open, self._get_autostart, self._set_autostart = open_console, get_autostart, set_autostart
        self.started_daemon = False
        self._message: str | None = None

    def snapshot(self) -> dict:
        return self._fetch()

    def autostart(self) -> bool:
        return self._get_autostart()

    def take_message(self) -> str | None:
        message, self._message = self._message, None
        return message

    def view(self) -> dict:
        """The menu to show now. A failing autostart read becomes a message, not a crash."""
        try:
            autostart = self._get_autostart()
        except Exception as e:
            autostart, self._message = False, str(e)
        return menu(self._fetch(), autostart)

    def close(self) -> None:
        """The tray is going away without a Quit click: stop the daemon if the tray started it."""
        if self.started_daemon:
            self._try(self._stop_daemon)

    def start(self) -> None:
        self._try(self._start_daemon)

    def click(self, item: str) -> bool:
        """Do what the item says. False means the tray should exit."""
        if item == "quit":
            self.close()
            return False
        action = {"open": self._open, "server": self._toggle_server,
                  "autostart": lambda: self._set_autostart(not self._get_autostart())}.get(item)
        if action:
            self._try(action)
        return True

    def _start_daemon(self) -> None:
        if self._ensure():
            self.started_daemon = True

    def _stop_daemon(self) -> None:
        self._shutdown()
        self.started_daemon = False

    def _toggle_server(self) -> None:
        (self._stop_daemon if self._fetch().get("up") else self._start_daemon)()

    def _try(self, action: Callable[[], object]) -> None:
        try:
            action()
        except Exception as e:  # shown as a tray notification; the tray keeps running
            self._message = str(e)


# --- wiring: the real daemon and the Qt child ------------------------------------------------------

def fetch() -> dict:
    import httpx

    from mxconsole import serve
    from mxconsole.settings import daemon_url
    if not serve.daemon_running():
        return {"up": False}
    out: dict = {"up": True}
    for key, path in (("models", "models"), ("pulls", "pulls"), ("ports", "ports")):
        try:
            body = httpx.get(f"{daemon_url()}/admin/{path}", headers=serve.ADMIN, timeout=2.0).json()
        except (httpx.HTTPError, ValueError):
            body = []
        out[key] = body.get(key, []) if isinstance(body, dict) else body if isinstance(body, list) else []
    return out


def open_console() -> None:
    """`mxc app` in its own process: the window blocks until it is closed, the tray must not."""
    subprocess.Popen([sys.executable, "-m", "mxconsole", "app"], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def single_instance(lock: Path):
    """Hold an exclusive lock on `lock` for the life of the process; None if another tray holds it."""
    import fcntl
    lock.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock, "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def qt_command(project: Path, icon: Path, pixi: str | None = None) -> list[str]:
    from mxconsole.app import APP_ENV, pixi_exe
    return [pixi or pixi_exe(), "run", "--manifest-path", str(project / "pixi.toml"),
            "-e", APP_ENV, "python", str(Path(__file__).with_name("tray_qt.py")), str(icon)]


def run(controller: Controller, child: subprocess.Popen, poll: float = POLL_SECONDS) -> int | None:
    """Drive the Qt child until Quit is clicked, the child exits, or Ctrl-C/SIGTERM (both act as Quit).
    Returns the child's exit code when it went away on its own, else None."""
    clicks: queue.Queue = queue.Queue()

    def read() -> None:
        for line in child.stdout:
            try:
                item = json.loads(line).get("click")
            except (ValueError, AttributeError):
                continue
            if isinstance(item, str):
                clicks.put(item)
        clicks.put(None)  # the child is gone

    def send(obj: dict) -> None:
        try:
            child.stdin.write(json.dumps(obj) + "\n")
            child.stdin.flush()
        except (BrokenPipeError, ValueError, OSError):
            pass

    def quit_() -> None:
        send({"quit": True})
        message = controller.take_message()
        if message:
            print(message, file=sys.stderr)
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()

    threading.Thread(target=read, daemon=True).start()
    try:
        send({"tooltip": "omxconsole · starting the server", "busy": False,
              "items": [{"id": "status", "label": "Starting the server…", "enabled": False}]})
        controller.start()
        shown, pending = None, []
        while True:
            message = controller.take_message()
            current = controller.view()
            message = message or controller.take_message()
            if message:
                send({"message": message})
            if current != shown:
                send(current)
                shown = current
            if pending:
                item = pending.pop()
            else:
                try:
                    item = clicks.get(timeout=poll)
                except queue.Empty:
                    continue
            if item is None:
                controller.close()
                try:
                    return child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    return None
            if not controller.click(item):
                quit_()
                return None
            # Clicks made while that action ran were made on a stale menu (a second "Stop server" would start
            # it again): drop them, but still honour Quit and the child going away.
            while True:
                try:
                    later = clicks.get_nowait()
                except queue.Empty:
                    break
                if later is None or later == "quit":
                    pending.append(later)
                    break
    except KeyboardInterrupt:
        controller.close()
        quit_()
        return None
