"""`mxc tray`: the status line, the menu and what each click does. No Qt here: the daemon calls are injected."""
from __future__ import annotations

from mxconsole import tray


def snap(up=True, models=(), pulls=(), ports=()):
    return {"up": up, "models": list(models), "pulls": list(pulls), "ports": list(ports)}


def m(name, state):
    return {"name": name, "state": state}


# --- status line: the Beacon's order ---------------------------------------------------------------

def test_server_down_says_stopped():
    assert tray.status_line(snap(up=False)) == "Server stopped"


def test_idle_server_says_ready():
    assert tray.status_line(snap(models=[m("a", "cold")])) == "Ready · nothing loaded"


def test_one_running_model_is_named():
    assert tray.status_line(snap(models=[m("qwen", "ready"), m("b", "cold")])) == "qwen running"


def test_several_running_models_are_counted():
    assert tray.status_line(snap(models=[m("a", "ready"), m("b", "ready")])) == "2 models running"


def test_starting_beats_running():
    assert tray.status_line(snap(models=[m("a", "ready"), m("b", "starting")])) == "Starting b"


def test_crash_beats_starting():
    assert tray.status_line(snap(models=[m("a", "starting"), m("b", "crashed")])) == "b crashed"


def test_port_beats_crash_and_shows_its_step():
    port = {"status": "running", "name": "olmoe", "stages": [
        {"name": "study", "status": "done"}, {"name": "write", "status": "running"}, {"name": "check", "status": "todo"}]}
    assert tray.status_line(snap(models=[m("b", "crashed")], ports=[port])) == "Porting olmoe · step 2 of 3"


def test_pull_beats_everything_and_shows_percent():
    pull = {"status": "running", "name": "qwen", "ref": "Qwen/Qwen2.5", "percent": 41.6}
    port = {"status": "running", "name": "olmoe", "stages": []}
    assert tray.status_line(snap(pulls=[pull], ports=[port])) == "Pulling qwen · 42%"


def test_pull_without_a_percent_yet():
    assert tray.status_line(snap(pulls=[{"status": "running", "ref": "x/y", "percent": None}])) == "Pulling x/y"


def test_finished_jobs_are_ignored():
    assert tray.status_line(snap(pulls=[{"status": "done", "name": "q", "percent": 100}])) == "Ready · nothing loaded"


# --- the menu --------------------------------------------------------------------------------------

def ids(menu):
    return [i.get("id") for i in menu["items"] if not i.get("sep")]


def test_menu_when_up():
    menu = tray.menu(snap(models=[m("qwen", "ready")]), autostart=False)
    assert ids(menu) == ["status", "open", "server", "autostart", "quit"]
    items = {i["id"]: i for i in menu["items"] if "id" in i}
    assert items["status"] == {"id": "status", "label": "qwen running", "enabled": False}
    assert items["server"]["label"] == "Stop server"
    assert items["autostart"]["checkable"] and items["autostart"]["checked"] is False
    assert menu["tooltip"] == "omxconsole · qwen running"
    assert menu["busy"] is True


def test_menu_when_down_offers_start_and_is_not_busy():
    menu = tray.menu(snap(up=False), autostart=True)
    items = {i["id"]: i for i in menu["items"] if "id" in i}
    assert items["server"]["label"] == "Start server"
    assert items["autostart"]["checked"] is True
    assert menu["busy"] is False


# --- clicks ----------------------------------------------------------------------------------------

class Fake:
    def __init__(self, up=False):
        self.up, self.calls, self.autostart = up, [], False

    def controller(self):
        return tray.Controller(
            fetch=lambda: snap(up=self.up),
            ensure_daemon=self._ensure, shutdown=self._shutdown,
            open_console=lambda: self.calls.append("open"),
            get_autostart=lambda: self.autostart, set_autostart=self._set_autostart)

    def _ensure(self):
        self.calls.append("ensure")
        started = not self.up
        self.up = True
        return started

    def _shutdown(self):
        self.calls.append("shutdown")
        self.up = False

    def _set_autostart(self, on):
        self.calls.append(("autostart", on))
        self.autostart = on


def test_launch_starts_the_daemon_and_remembers_it_did():
    f = Fake(up=False)
    c = f.controller()
    c.start()
    assert f.calls == ["ensure"] and c.started_daemon


def test_quit_stops_a_daemon_the_tray_started():
    f = Fake(up=False)
    c = f.controller()
    c.start()
    assert c.click("quit") is False
    assert f.calls[-1] == "shutdown"


def test_quit_leaves_a_daemon_it_found_running():
    f = Fake(up=True)
    c = f.controller()
    c.start()
    assert c.click("quit") is False
    assert "shutdown" not in f.calls


def test_stop_then_quit_does_not_stop_twice():
    f = Fake(up=False)
    c = f.controller()
    c.start()
    c.click("server")
    c.click("quit")
    assert f.calls.count("shutdown") == 1


def test_server_toggles_on_the_live_state():
    f = Fake(up=True)
    c = f.controller()
    c.start()
    assert c.click("server") is True and f.up is False
    c.click("server")
    assert f.up is True and c.started_daemon  # started from the tray, so Quit stops it


def test_open_and_autostart():
    f = Fake(up=True)
    c = f.controller()
    c.click("open")
    c.click("autostart")
    assert f.calls == ["open", ("autostart", True)]
    assert tray.menu(c.snapshot(), c.autostart())["items"]


def test_unknown_click_is_ignored():
    f = Fake(up=True)
    assert f.controller().click("nope") is True and f.calls == []


def test_a_failing_action_becomes_a_message_not_a_crash():
    def boom():
        raise RuntimeError("daemon did not start")
    c = tray.Controller(fetch=lambda: snap(up=False), ensure_daemon=boom, shutdown=lambda: None,
                        open_console=lambda: None, get_autostart=lambda: False, set_autostart=lambda on: None)
    assert c.click("server") is True
    assert "daemon did not start" in c.take_message()
    assert c.take_message() is None


# --- the JSON-lines conversation with the Qt child -------------------------------------------------

class FakeChild:
    def __init__(self, clicks):
        import io
        self.stdout = io.StringIO("".join(f'{{"click": "{c}"}}\n' for c in clicks))
        self.stdin = io.StringIO()
        self.waited = False
        self.returncode = 0

    def wait(self, timeout=None):
        self.waited = True
        return self.returncode


def sent(child):
    import json
    return [json.loads(line) for line in child.stdin.getvalue().splitlines()]


def test_run_shows_the_menu_does_the_clicks_and_quits():
    f = Fake(up=False)
    child = FakeChild(["open", "quit"])
    tray.run(f.controller(), child, poll=0.01)
    out = sent(child)
    assert out[0]["items"][0]["label"] == "Starting the server…"
    assert any(o.get("items") and o["items"][0]["label"] == "Ready · nothing loaded" for o in out)
    assert out[-1] == {"quit": True} and child.waited
    assert f.calls == ["ensure", "open", "shutdown"]


def test_run_ends_when_the_child_goes_away():
    f = Fake(up=True)
    child = FakeChild([])
    assert tray.run(f.controller(), child, poll=0.01) == 0
    assert "shutdown" not in f.calls and {"quit": True} not in sent(child)


def test_only_one_tray_at_a_time(tmp_path):
    first = tray.single_instance(tmp_path / "tray.lock")
    assert first is not None
    assert tray.single_instance(tmp_path / "tray.lock") is None
    first.close()
    assert tray.single_instance(tmp_path / "tray.lock") is not None


def test_child_dying_stops_a_daemon_the_tray_started_and_reports_it():
    f = Fake(up=False)
    child = FakeChild([])
    child.returncode = 1
    assert tray.run(f.controller(), child, poll=0.01) == 1
    assert f.calls == ["ensure", "shutdown"]


def test_ctrl_c_acts_like_quit():
    f = Fake(up=False)
    c = f.controller()
    c._open = lambda: (_ for _ in ()).throw(KeyboardInterrupt())
    child = FakeChild(["open"])
    tray.run(c, child, poll=0.01)
    assert f.calls[-1] == "shutdown" and sent(child)[-1] == {"quit": True}


def test_clicks_made_while_an_action_ran_are_dropped_except_quit():
    import time
    f = Fake(up=True)
    c = f.controller()
    slow = f._shutdown
    c._shutdown = lambda: (time.sleep(0.2), slow())
    child = FakeChild(["server", "server", "quit"])
    tray.run(c, child, poll=0.01)
    assert f.calls.count("shutdown") == 1 and "ensure" not in f.calls[1:]
    assert sent(child)[-1] == {"quit": True}


def test_lines_that_are_not_clicks_are_ignored():
    import io
    f = Fake(up=True)
    child = FakeChild([])
    child.stdout = io.StringIO('{"hello": 1}\nnot json\n{"click": "quit"}\n')
    tray.run(f.controller(), child, poll=0.01)
    assert sent(child)[-1] == {"quit": True}


def test_a_failing_autostart_read_does_not_crash_the_view():
    def broken():
        raise OSError("systemctl missing")
    c = tray.Controller(fetch=lambda: snap(), ensure_daemon=lambda: False, shutdown=lambda: None,
                        open_console=lambda: None, get_autostart=broken, set_autostart=lambda on: None)
    assert c.view()["items"][0]["label"] == "Ready · nothing loaded"
    assert "systemctl missing" in c.take_message()


def test_busy_follows_the_state_not_the_wording():
    assert tray.menu(snap(models=[m("a", "cold")]), False)["busy"] is False
    assert tray.menu(snap(models=[m("a", "starting")]), False)["busy"] is True
    assert tray.menu(snap(pulls=[{"status": "running", "ref": "x"}]), False)["busy"] is True
