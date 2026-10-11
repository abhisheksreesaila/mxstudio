"""F-024: GET /admin/learn/{name} and POST /admin/learn/{name}/open. No Jupyter, no network."""
import json
from pathlib import Path

import nbformat
from typer.testing import CliRunner

from mxconsole import cli, learn
from mxconsole.gateway import create_app
from mxconsole.jupyter import JupyterFailed, JupyterLauncher, JupyterMissing
from test_console import ADMIN
from test_gateway import client, key, store, sup  # noqa: F401  (shared fixtures)
from test_learn import FakeHF

runner = CliRunner()


class FakeLauncher:
    def __init__(self):
        self.opened = []

    def __call__(self, path: Path) -> str:
        self.opened.append(path)
        return f"http://127.0.0.1:8899/lab/tree/{path.name}?token=t"


def deps(launcher=None, source=None):
    return {"learn_source": source or FakeHF(), "jupyter_launcher": launcher or FakeLauncher()}


async def test_get_generates_the_notebook_once_and_returns_cells(store, sup, mxc_home):
    source = FakeHF()
    async with client(create_app(store, sup, api_deps=deps(source=source))) as http:
        r = await http.get("/admin/learn/tiny", headers=ADMIN)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["name"] == "tiny" and body["path"] == str(mxc_home / "notebooks" / "tiny.ipynb")
        assert Path(body["path"]).exists() and body["generated"] is True
        assert body["kernelspec"]["name"] == "python3"
        kinds = {c["type"] for c in body["cells"]}
        assert kinds == {"markdown", "code"}
        assert all(set(c) == {"type", "source", "outputs"} and isinstance(c["source"], str) for c in body["cells"])
        assert "org/tiny" in body["cells"][0]["source"]
        arch = body["architecture"]
        assert arch["architecture"] == "Qwen2ForCausalLM"
        assert {"field": "hidden_size", "value": 896}.items() <= next(r for r in arch["rows"] if r["field"] == "hidden_size").items()
        assert arch["parameters"]["layers"] == 24 and arch["safetensors_total"] == 494_032_768
        calls = len(source.calls)
        again = (await http.get("/admin/learn/tiny", headers=ADMIN)).json()
        assert again["generated"] is False and len(source.calls) == calls  # read from disk, no refetch


async def test_saved_outputs_come_back(store, sup, mxc_home):
    async with client(create_app(store, sup, api_deps=deps())) as http:
        await http.get("/admin/learn/tiny", headers=ADMIN)
        path = mxc_home / "notebooks" / "tiny.ipynb"
        nb = nbformat.read(path, as_version=4)
        code = next(c for c in nb.cells if c.cell_type == "code")
        code.outputs = [nbformat.v4.new_output("stream", name="stdout", text="hello\n")]
        nbformat.write(nb, path)
        cells = (await http.get("/admin/learn/tiny", headers=ADMIN)).json()["cells"]
        out = next(c for c in cells if c["type"] == "code")["outputs"]
        assert out[0]["output_type"] == "stream" and out[0]["text"] == "hello\n"


async def test_unknown_model_and_generation_failures(store, sup):
    class Broken(FakeHF):
        def info(self, repo_id, revision):
            raise RuntimeError("no such model on Hugging Face: org/tiny (use owner/repo)")
    async with client(create_app(store, sup, api_deps=deps())) as http:
        assert (await http.get("/admin/learn/nope", headers=ADMIN)).status_code == 404
        assert (await http.post("/admin/learn/nope/open", headers=ADMIN)).status_code == 404
    async with client(create_app(store, sup, api_deps=deps(source=Broken()))) as http:
        r = await http.get("/admin/learn/tiny", headers=ADMIN)
        assert r.status_code == 502 and "no such model" in r.json()["error"]["message"]


async def test_open_starts_jupyter_through_the_launcher_and_returns_the_url(store, sup, mxc_home):
    launcher = FakeLauncher()
    async with client(create_app(store, sup, api_deps=deps(launcher))) as http:
        r = await http.post("/admin/learn/tiny/open", headers=ADMIN)
        assert r.status_code == 200, r.text
        assert r.json() == {"name": "tiny", "path": str(mxc_home / "notebooks" / "tiny.ipynb"),
                            "url": "http://127.0.0.1:8899/lab/tree/tiny.ipynb?token=t"}
        assert launcher.opened == [mxc_home / "notebooks" / "tiny.ipynb"]  # generated first, then opened


async def test_open_reports_jupyter_problems(store, sup):
    def missing(path):
        raise JupyterMissing("jupyter was not found. Install it with `pixi add jupyterlab`")
    async with client(create_app(store, sup, api_deps=deps(missing))) as http:
        r = await http.post("/admin/learn/tiny/open", headers=ADMIN)
        assert r.status_code == 501 and "pixi add jupyterlab" in r.json()["error"]["message"]

    def failed(path):
        raise JupyterFailed("Jupyter Lab did not start")
    async with client(create_app(store, sup, api_deps=deps(failed))) as http:
        assert (await http.post("/admin/learn/tiny/open", headers=ADMIN)).status_code == 502


async def test_learn_routes_are_admin_only(store, sup):
    launcher = FakeLauncher()
    async with client(create_app(store, sup, api_deps=deps(launcher))) as http:
        for method, path in (("GET", "/admin/learn/tiny"), ("POST", "/admin/learn/tiny/open")):
            assert (await http.request(method, path)).status_code == 403
            assert (await http.request(method, path, headers={**ADMIN, "X-Forwarded-For": "10.0.0.2"})).status_code == 403
            assert (await http.request(method, path, headers={**ADMIN, "Origin": "https://evil.example"})).status_code == 403
    assert launcher.opened == []


class FakeProc:
    def __init__(self, alive=True):
        self.alive = alive

    def poll(self):
        return None if self.alive else 1

    def terminate(self):
        self.alive = False


def test_the_launcher_binds_loopback_with_a_token_and_reuses_its_server(tmp_path):
    spawned = []

    def popen(cmd, **kw):
        spawned.append((cmd, kw))
        return FakeProc()
    launcher = JupyterLauncher(popen=popen, free_port=lambda: 8901, port_open=lambda p: True)
    nb = tmp_path / "tiny.ipynb"
    url = launcher(nb)
    cmd, kw = spawned[0]
    assert cmd[:2] == ["jupyter", "lab"] and "--no-browser" in cmd and "--ServerApp.ip=127.0.0.1" in cmd
    assert f"--ServerApp.root_dir={tmp_path}" in cmd and kw["start_new_session"] is True
    token = kw["env"]["JUPYTER_TOKEN"]
    assert len(token) >= 20 and url == f"http://127.0.0.1:8901/lab/tree/tiny.ipynb?token={token}"
    assert launcher(tmp_path / "other.ipynb").endswith(f"/lab/tree/other.ipynb?token={token}")
    assert len(spawned) == 1  # same server, second notebook
    launcher.proc.alive = False
    launcher(nb)
    assert len(spawned) == 2  # the old server died: start a new one


def test_the_launcher_names_the_fix_when_jupyter_is_missing_or_dies(tmp_path):
    def missing(cmd, **kw):
        raise FileNotFoundError("jupyter")
    try:
        JupyterLauncher(popen=missing)(tmp_path / "a.ipynb")
        assert False
    except JupyterMissing as e:
        assert "pixi add jupyterlab" in str(e)
    try:
        JupyterLauncher(popen=lambda cmd, **kw: FakeProc(alive=False), free_port=lambda: 1, port_open=lambda p: False)(tmp_path / "a.ipynb")
        assert False
    except JupyterFailed:
        pass


def test_cli_learn_json_prints_the_same_payload(inproc_daemon, monkeypatch):
    from mxconsole.store import Store
    Store.open().add_model(name="tiny", repo_id="org/tiny", revision="abc", source="hf", architecture="X")
    monkeypatch.setattr("mxconsole.learn.default_source", lambda: FakeHF())
    r = runner.invoke(cli.app, ["learn", "tiny", "--json"])
    assert r.exit_code == 0, r.output
    body = json.loads(r.output)
    assert body["name"] == "tiny" and body["cells"] and body["architecture"]["architecture"] == "Qwen2ForCausalLM"
    assert "url" not in body  # --json never launches Jupyter
