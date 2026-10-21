"""F-043: the Beacon's priority (pull or port > crash > running > idle GPU) is a pure function; these run it under node."""
from test_console_blades import needs_node, node

GPU = "[{index:0,name:'NVIDIA GeForce RTX 3090',used_mb:1024,total_mb:24576}]"
PULL = ("{id:3,ref:'x/SmolLM3-3B',name:'smollm3-3b',status:'running',step:'download',percent:64.2,speed:41200000,"
        "steps:[{id:'resolve',label:'Resolve',state:'done',detail:'MAX catalog'},{id:'download',label:'Download',state:'now',detail:''},"
        "{id:'register',label:'Register',state:'next',detail:''}]}")
PORT = ("{id:7,ref:'a/olmoe',name:'olmoe',status:'running',stage:'build',stages:[{name:'workspace',status:'done',detail:'',attempt:null},"
        "{name:'build',status:'running',detail:'attempt 2',attempt:2},{name:'parity',status:'pending',detail:'',attempt:2},"
        "{name:'register',status:'pending',detail:'',attempt:null}]}")
RUN = "[{name:'qwen',state:'ready'},{name:'smol',state:'ready'},{name:'cold-one',state:'cold'}]"
SERIES = "{models:{qwen:[{decode_tps_p50:100},{decode_tps_p50:141.4}],smol:[{decode_tps_p50:40.6}]}}"
CRASH = "[{name:'phi-2',state:'crashed'},{name:'qwen',state:'ready'}]"


def state(**parts):
    base = {"models": "[]", "pulls": "[]", "ports": "[]", "gpus": GPU, "crew": "[]", "series": "null", "error": "null", **parts}
    body = ", ".join(f"{k}: {v}" for k, v in base.items())
    return node(f"m.beaconState({{{body}}})", "beacon.js")


@needs_node
def test_a_pull_in_progress_wins_and_shows_its_percent():
    b = state(pulls=f"[{PULL}]", ports=f"[{PORT}]", models=CRASH)
    assert (b["kind"], b["label"], b["value"], b["pct"]) == ("pull", "Pulling smollm3-3b", "64%", 64.2)
    assert [s["state"] for s in b["steps"]] == ["ok", "run", "next"]
    assert [a["label"] for a in b["actions"]] == ["Open pull", "Cancel"]
    assert b["actions"][0]["go"] == "#add" and b["actions"][1]["post"] == "/admin/pulls/3/cancel"


@needs_node
def test_a_pull_that_is_finished_does_not_count():
    b = state(pulls=f"[{PULL.replace('status:' + chr(39) + 'running' + chr(39), 'status:' + chr(39) + 'done' + chr(39))}]", models=RUN, series=SERIES)
    assert b["kind"] == "running"


@needs_node
def test_a_port_in_progress_beats_a_crash_and_shows_its_step():
    b = state(ports=f"[{PORT}]", models=CRASH)
    assert (b["kind"], b["label"], b["value"]) == ("port", "Porting olmoe", "step 2 of 4")
    assert [a["label"] for a in b["actions"]] == ["Open port", "Cancel"]
    assert b["actions"][1]["post"] == "/admin/ports/7/cancel"


@needs_node
def test_a_crashed_model_beats_running_ones_and_offers_open_restart_logs():
    b = state(models=CRASH, series=SERIES)
    assert (b["kind"], b["label"]) == ("crash", "phi-2 crashed")
    assert [(a["label"], a.get("go"), a.get("post")) for a in b["actions"]] == [
        ("Open phi-2", "#model/phi-2", None), ("Restart", None, "/admin/models/phi-2/load"), ("View logs", "#logs/phi-2", None)]


@needs_node
def test_running_models_show_their_count_and_the_live_total_tok_per_s():
    b = state(models=RUN, series=SERIES, crew="[{id:'model:qwen',vram_gb:1.2},{id:'model:smol',vram_gb:1.9}]")
    assert (b["kind"], b["label"], b["value"]) == ("running", "2 running", "182 tok/s")
    assert [s["label"] for s in b["steps"]] == ["qwen", "smol"]
    assert b["steps"][0]["detail"] == "141 tok/s · 1.2 GB"
    assert b["actions"][0] == {"label": "Open qwen", "go": "#model/qwen", "primary": True}


@needs_node
def test_running_with_no_traffic_yet_says_so_instead_of_zero():
    b = state(models=RUN)
    assert (b["kind"], b["label"], b["value"]) == ("running", "2 running", "0 tok/s")


@needs_node
def test_idle_shows_gpu_memory_in_use_and_offers_add_a_model():
    b = state(models="[{name:'a',state:'cold'}]")
    assert (b["kind"], b["label"], b["value"]) == ("idle", "RTX 3090", "1/24 GB")
    assert b["pct"] == 1024 / 24576 * 100 and b["actions"] == [{"label": "Add a model", "go": "#add", "primary": True}]


@needs_node
def test_without_a_gpu_or_with_the_daemon_down_the_beacon_still_says_one_true_thing():
    assert state(gpus="[]")["label"] == "No GPU"
    off = state(error="true", models="null")
    assert (off["kind"], off["label"]) == ("offline", "daemon offline")
    assert state(gpus="null", models="null")["label"] == "…"


@needs_node
def test_cancelling_a_pull_or_port_from_the_beacon_takes_a_second_tap():
    """Cancel is the only Beacon action that throws work away, so it asks first (review of F-043)."""
    for b in (state(pulls=f"[{PULL}]"), state(ports=f"[{PORT}]")):
        cancel = next(a for a in b["actions"] if a["label"] == "Cancel")
        assert cancel["confirm"].startswith("Tap again")
    crash = state(models=CRASH)
    assert all("confirm" not in a for a in crash["actions"])  # open, restart and logs are safe


def test_job_polling_waits_for_each_poll_keeps_the_last_value_on_error_and_rests_when_hidden():
    from mxconsole.gateway import CONSOLE_DIR
    js = (CONSOLE_DIR / "beacon.js").read_text()
    assert "setInterval(" not in js and "document.hidden" in js  # chained setTimeout: no stacked requests, idle in a hidden tab
    assert "jobs.pulls = []" not in js and "jobs.ports = []" not in js  # a failed poll keeps what we last knew


@needs_node
def test_a_model_that_is_starting_shows_instead_of_idle_and_loses_to_a_crash():
    """Found in the live pull (omxconsole finish, D-07): a minute of compile used to read as idle."""
    b = state(models="[{name:'smollm2-360m',state:'starting'},{name:'qwen',state:'ready'}]")
    assert (b["kind"], b["label"]) == ("starting", "Starting smollm2-360m")
    assert b["actions"][0]["go"] == "#model/smollm2-360m"
    crash = state(models="[{name:'phi-2',state:'crashed'},{name:'smol',state:'starting'}]")
    assert crash["kind"] == "crash"
    pull = state(pulls=f"[{PULL}]", models="[{name:'smol',state:'starting'}]")
    assert pull["kind"] == "pull"
