"""F-024: /admin/system reports the GPU name, VRAM, and VRAM per running model (nvidia-smi and /proc are fakes)."""
from mxconsole import metrics, system
from mxconsole.gateway import create_app
from test_console import ADMIN
from test_gateway import client, key, store, sup  # noqa: F401  (shared fixtures)

GPU = [{"index": 0, "name": "RTX Fake 3090", "used_mb": 10240, "total_mb": 24576, "util": 5}]


def gpu_app(store, sup, gpus=lambda: GPU, procs=lambda: {}, ppid=lambda pid: None):
    return create_app(store, sup, api_deps={"system": {"gpus": gpus, "gpu_procs": procs, "ppid": ppid}})


async def test_system_reports_the_gpu_and_vram_per_running_model(store, sup):
    async with client(gpu_app(store, sup)) as http:
        await http.post("/admin/models/tiny/load", headers=ADMIN)
    pid = next(s["pid"] for s in sup.status() if s["name"] == "tiny")
    worker = pid + 1000  # MAX computes in a worker under the model's process: its memory counts for the model
    async with client(gpu_app(store, sup, procs=lambda: {worker: 3072, 4242: 9999},
                              ppid=lambda p: {worker: pid}.get(p))) as http:
        body = (await http.get("/admin/system", headers=ADMIN)).json()
    assert body["gpu"] == {"name": "RTX Fake 3090", "count": 1, "vram_total_gb": 24.0, "vram_used_gb": 10.0}
    assert body["gpus"] == [{"index": 0, "name": "RTX Fake 3090", "vram_total_gb": 24.0, "vram_used_gb": 10.0}]
    by = {c["id"]: c for c in body["crew"]}
    assert by["model:tiny"]["vram_gb"] == 3.0  # the unrelated process 4242 is not counted
    assert "vram_gb" not in by["daemon"]


async def test_vram_is_null_when_the_driver_does_not_list_the_model(store, sup):
    async with client(gpu_app(store, sup)) as http:
        await http.post("/admin/models/tiny/load", headers=ADMIN)
        body = (await http.get("/admin/system", headers=ADMIN)).json()
    assert body["gpu"]["name"] == "RTX Fake 3090"
    assert {c["id"]: c for c in body["crew"]}["model:tiny"]["vram_gb"] is None


async def test_no_gpu_means_null_not_zero(store, sup):
    async with client(gpu_app(store, sup, gpus=lambda: [])) as http:
        await http.post("/admin/models/tiny/load", headers=ADMIN)
        body = (await http.get("/admin/system", headers=ADMIN)).json()
    assert body["gpu"] is None and body["gpus"] == []
    assert {c["id"]: c for c in body["crew"]}["model:tiny"]["vram_gb"] is None


def test_mxc_ps_json_has_vram_per_model_too(mxc_home, monkeypatch):
    import json
    import subprocess
    import sys

    from typer.testing import CliRunner

    from mxconsole.cli import app
    monkeypatch.setenv("MXC_PORT", "1")
    monkeypatch.setenv("MXC_MAX_CMD", f"{sys.executable} -c 'import time; time.sleep(30)'")
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", "serve", "--port", "6001"])
    try:
        (mxc_home / "run").mkdir(parents=True)
        (mxc_home / "run" / f"tiny.{child.pid}.pid").write_text(f"{child.pid} {child.pid}")
        monkeypatch.setattr(metrics, "gpus", lambda: GPU)
        monkeypatch.setattr(metrics, "gpu_processes", lambda: {child.pid: 2048})
        crew = {c["id"]: c for c in json.loads(CliRunner().invoke(app, ["ps", "--json"]).output)}
        assert crew["model:tiny"]["vram_gb"] == 2.0
    finally:
        child.kill()
        child.wait()


def test_gpu_summary_and_process_parsing():
    two = GPU + [{"index": 1, "name": "RTX Fake 3090", "used_mb": 0, "total_mb": 24576, "util": 0}]
    summary, rows = system.gpu_summary(two)
    assert summary["name"] == "RTX Fake 3090 x2" and summary["vram_total_gb"] == 48.0 and len(rows) == 2
    assert metrics.gpu_processes(lambda: "123, 2048\n123, 1024\n456, [N/A]\n") == {123: 3072}

    def missing():
        raise FileNotFoundError("nvidia-smi")
    assert metrics.gpu_processes(missing) == {}
