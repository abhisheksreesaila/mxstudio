"""F-050: on a Mac there is no nvidia-smi; GPU memory is the machine's unified memory (sysctl + vm_stat)."""
from __future__ import annotations

import subprocess

from mxconsole import metrics, report
from mxconsole.store import Store
from mxconsole.supervisor import Supervisor

# Recorded from an Apple-silicon Mac (16 GB, 16 KB pages), trimmed.
VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                               12345.
Pages active:                            300000.
Pages inactive:                          290000.
Pages speculative:                         5000.
Pages throttled:                              0.
Pages wired down:                        150000.
Pages purgeable:                           2000.
"Translation faults":                 123456789.
File-backed pages:                       200000.
Anonymous pages:                         390000.
Pages stored in compressor:              100000.
Pages occupied by compressor:             40000.
"""
SYSCTL = {"hw.memsize": "17179869184\n", "machdep.cpu.brand_string": "Apple M2 Pro\n"}


def sysctl(name):
    return SYSCTL[name]


def test_unified_memory_is_one_gpu_entry():
    [g] = metrics.mac_memory(sysctl=sysctl, vm_stat=lambda: VM_STAT)
    assert g["index"] == 0 and g["name"] == "Apple M2 Pro" and g["unified"] is True
    assert g["total_mb"] == 16384
    assert g["used_mb"] == (300000 + 150000 + 40000) * 16384 // 2**20  # active + wired + compressed
    assert g["util"] is None  # no GPU utilisation without root on a Mac


def test_mac_reader_failing_means_no_gpu():
    def boom(*a):
        raise subprocess.CalledProcessError(1, "sysctl")
    assert metrics.mac_memory(sysctl=boom, vm_stat=lambda: VM_STAT) == []
    assert metrics.mac_memory(sysctl=sysctl, vm_stat=lambda: "garbage") == []


def test_gpus_picks_the_reader_by_platform():
    mac = lambda: [{"index": 0, "name": "Apple M2 Pro", "used_mb": 1, "total_mb": 2, "util": None, "unified": True}]
    nvidia = lambda: "0, NVIDIA GeForce RTX 3090, 1000, 24576, 5\n"
    assert metrics.gpus(runner=nvidia, platform="darwin", mac=mac)[0]["unified"] is True
    assert metrics.gpus(runner=nvidia, platform="linux", mac=mac)[0]["name"] == "NVIDIA GeForce RTX 3090"


def test_metrics_text_shows_a_dash_for_unknown_utilisation():
    gpus = metrics.mac_memory(sysctl=sysctl, vm_stat=lambda: VM_STAT)
    text = report.metrics_text(metrics.summary(Store.open()), "5m", gpus)
    line = next(l for l in text.splitlines() if "Apple M2 Pro" in l)
    assert line.rstrip().endswith(" -") and "None" not in line


def test_supervisor_lets_max_choose_the_device_on_unified_memory_but_sizes_from_it():
    store = Store.open()
    store.add_model(name="tiny", repo_id="x/tiny", revision="main", source="hf-supported", architecture="Qwen2ForCausalLM")
    unified = lambda: [{"index": 0, "name": "Apple M2 Pro", "used_mb": 8000, "total_mb": 16384, "util": None,
                        "unified": True}]
    sup = Supervisor(store, max_command=("max",), gpus=unified)
    cmd = sup._command(store.get_model("tiny"), 9999, sup._pick_gpu())
    assert "--devices" not in cmd
    assert "--device-memory-utilization" in cmd


def test_nvidia_still_gets_a_device():
    store = Store.open()
    store.add_model(name="tiny", repo_id="x/tiny", revision="main", source="hf-supported", architecture="Qwen2ForCausalLM")
    nvidia = lambda: [{"index": 1, "name": "RTX", "used_mb": 0, "total_mb": 24576, "util": 0}]
    sup = Supervisor(store, max_command=("max",), gpus=nvidia)
    cmd = sup._command(store.get_model("tiny"), 9999, sup._pick_gpu())
    assert cmd[cmd.index("--devices") + 1] == "gpu:1"
