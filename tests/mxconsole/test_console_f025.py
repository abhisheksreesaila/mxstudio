"""F-025: a model's resource blade, Security and API keys. Pure view functions run under node; CSS is checked for balance."""
import json
import shutil
import subprocess

import pytest

from mxconsole.gateway import CONSOLE_DIR

needs_node = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
pytestmark = needs_node


def node(expr: str, module: str):
    script = f"""
      import * as m from {json.dumps((CONSOLE_DIR / module).as_uri())};
      console.log(JSON.stringify({expr}) ?? "null");
    """
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


MODEL = {"name": "qwen", "repo_id": "Qwen/Qwen2.5-0.5B-Instruct", "source": "hf", "architecture": "Qwen2ForCausalLM",
         "state": "ready", "pinned": False, "keep_alive": 600, "idle_seconds": 30, "starting_seconds": None, "in_flight": 0}
CTX = {
    "metrics": {"by_model": {"qwen": {"decode_tps_p50": 120.4, "ttft_p50_ms": 38, "requests": 14}},
                "gpus": [{"name": "NVIDIA RTX 4090", "total_mb": 24576, "used_mb": 8192}]},
    "series": {"models": {"qwen": [{"decode_tps_p50": 100}, {"decode_tps_p50": None}, {"decode_tps_p50": 120}]}},
    "info": {"endpoint": "http://127.0.0.1:11435/v1", "lan_endpoint": None},
    "system": [{"id": "model:qwen", "vram_gb": 2.0}, {"id": "model:llama", "vram_gb": 6.0}],
    "endpoint": "http://127.0.0.1:11435/v1",
}


def overview(model=None, ctx=None):
    return node(f"m.overviewView({json.dumps({**MODEL, **(model or {})})}, {json.dumps({**CTX, **(ctx or {})})})", "model-page.js")


def commands(state):
    return node(f"m.commandsFor({json.dumps(state)})", "model-page.js")


# -- command bar ---------------------------------------------------------------------------------
def test_commands_by_state():
    def keys(state):
        return [c["key"] for c in commands(state)]
    assert keys("ready")[:2] == ["stop", "restart"]
    assert keys("cold")[0] == "start"
    assert keys("crashed")[0] == "restart"
    assert keys("starting")[0] == "stop"
    for state in ("ready", "cold", "crashed", "starting"):
        k = keys(state)
        assert "copy" in k and k[-1] == "rm"
        assert not {"secure", "bench", "notebook", "logs"} & set(k)  # F-053: those are tabs or live inside them


def test_every_command_has_label_icon_and_the_remove_is_danger():
    for c in commands("ready"):
        assert c["label"] and c["icon"]
    by = {c["key"]: c for c in commands("ready")}
    assert by["rm"]["danger"] is True and by["stop"]["danger"] is False


def test_twin_names_the_model():
    assert node("m.cliTwin('stop', 'qwen')", "model-page.js") == "mxc stop qwen"
    assert node("m.cliTwin('start', 'qwen')", "model-page.js") == "mxc serve qwen"
    assert node("m.cliTwin('restart', 'qwen')", "model-page.js") == "mxc stop qwen && mxc serve qwen"
    assert node("m.cliTwin('rm', 'qwen')", "model-page.js") == "mxc rm qwen"
    assert node("m.cliTwin('secure', 'qwen')", "model-page.js") == "mxc expose"
    assert node("m.cliTwin('bench', 'qwen')", "model-page.js") == "mxc bench"


# -- overview ------------------------------------------------------------------------------------
def test_overview_essentials_and_tokens():
    v = overview()
    ess = dict(v["essentials"])
    assert ess["Repository"] == "Qwen/Qwen2.5-0.5B-Instruct"
    assert ess["Architecture"] == "Qwen2"
    assert ess["Access"].startswith("localhost only")
    assert ess["GPU memory"].startswith("2 GB")
    assert v["tokens"] == {"tps": 120, "ttft": 38, "requests": 14, "points": [100, 120]}


def test_overview_idle_model_has_no_live_numbers():
    v = overview({"state": "cold"})
    assert v["tokens"]["tps"] is None and v["tokens"]["ttft"] is None and v["tokens"]["points"] == []


def test_call_it_switches_to_the_lan_url_once_secure():
    local = overview()["call"]
    assert local["url"] == "http://127.0.0.1:11435/v1" and local["secure"] is False and local["keyHint"] == "$MXC_API_KEY"
    assert "http://127.0.0.1:11435/v1/chat/completions" in local["curl"] and '"model":"qwen"' in local["curl"]
    assert "$MXC_API_KEY" in local["curl"]
    secure = overview(ctx={"info": {"endpoint": "http://127.0.0.1:11435/v1", "lan_endpoint": "https://box.local:8443/v1"}})["call"]
    assert secure["url"] == "https://box.local:8443/v1" and secure["secure"] is True
    assert secure["curl"].startswith("curl https://box.local:8443/v1/chat/completions")


def test_hardware_fit_bar():
    fit = overview()["fit"]
    assert fit["totalGb"] == 24 and fit["thisGb"] == 2 and fit["othersGb"] == 6
    assert fit["freeGb"] == 16 and round(fit["thisPct"], 1) == 8.3
    assert [o["name"] for o in fit["others"]] == ["llama"]
    assert overview(ctx={"metrics": {"by_model": {}, "gpus": []}})["fit"]["totalGb"] is None


def test_crashed_model_is_called_out():
    assert overview({"state": "crashed"})["crashed"] is True and overview()["crashed"] is False


# -- security ------------------------------------------------------------------------------------
def sec(info, network=None):
    return node(f"m.securityView({json.dumps(info)}, {json.dumps(network)})", "blade-security.js")


def test_security_view_local_then_secure():
    local = sec({"lan_endpoint": None})
    assert local["secure"] is False and local["url"] is None
    on = sec({"lan_endpoint": "https://box.local:8443/v1"}, {"ca_path": "/h/ca.crt", "ca_exists": True, "default_host": "box.local"})
    assert on["secure"] is True and on["url"] == "https://box.local:8443/v1" and on["caPath"] == "/h/ca.crt" and on["caExists"] is True
    assert on["curl"].startswith("curl --cacert /h/ca.crt https://box.local:8443/v1/chat/completions")
    assert sec({"lan_endpoint": None}, None)["caPath"] == ""


def test_expose_steps_advance_then_finish():
    assert node("m.EXPOSE_STEPS.map((s) => s.key)", "blade-security.js") == ["cert", "key", "https"]
    # one blocking request: nothing is checked until it returns, and a failure does not say which step broke
    assert node("m.stepStates('run')", "blade-security.js") == ["active", "active", "active"]
    assert node("m.stepStates('done')", "blade-security.js") == ["done", "done", "done"]
    assert node("m.stepStates('fail')", "blade-security.js") == ["todo", "todo", "todo"]


# -- keys ----------------------------------------------------------------------------------------
def test_key_rows_and_status():
    rows = node("""m.keyRows([
        {app: "web", prefix: "mxc-ab12", created_at: 1000, last_used_at: null, revoked_at: null, status: "active"},
        {app: "old", prefix: "mxc-cd34", created_at: 900, last_used_at: 950, revoked_at: 960, status: "revoked"}])""", "blade-keys.js")
    assert [r["name"] for r in rows] == ["web", "old"]
    assert rows[0]["active"] is True and rows[0]["lastUsed"] == "never" and rows[0]["prefix"] == "mxc-ab12"
    assert rows[1]["active"] is False and rows[1]["status"] == "revoked"


def test_app_name_validation_and_twins():
    def ok(n):
        return node(f"m.validAppName({json.dumps(n)})", "blade-keys.js")
    assert ok("my-app_1.x") is True
    assert ok("") is False and ok("has space") is False and ok("_internal") is False
    assert node("m.keyTwin('new', 'web')", "blade-keys.js") == "mxc keys new web"
    assert node("m.keyTwin('rotate', 'web')", "blade-keys.js") == "mxc keys rotate web"
    assert node("m.keyTwin('revoke', 'web')", "blade-keys.js") == "mxc keys revoke web"
    assert node("m.keyTwin('ls')", "blade-keys.js") == "mxc keys ls"


def test_trust_steps_use_the_real_ca_filename():
    steps = node("m.trustSteps('/h/.mxc/pki/root.crt')", "blade-security.js")
    assert all("ca.crt" not in t[1] for t in steps) and any("root.crt" in t[1] for t in steps)
    assert node("m.trustSteps('')", "blade-security.js")


# -- css and csp ---------------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["blade-model.css", "blade-security.css", "blade-keys.css"])
def test_css_braces_balance(name):
    css = (CONSOLE_DIR / name).read_text()
    assert css.count("{") == css.count("}") and len(css) > 400


@pytest.mark.parametrize("name", ["model-page.js", "blade-security.js", "blade-keys.js"])
def test_no_innerhtml_or_style_attributes(name):
    src = (CONSOLE_DIR / name).read_text()
    assert "innerHTML" not in src and "insertAdjacentHTML" not in src and 'setAttribute("style"' not in src
