import shutil
import subprocess

import pytest
import yaml
from typer.testing import CliRunner

from mst import expose
from mst.cli import app
from mst.store import Store

runner = CliRunner()


def test_caddyfile_checks_app_keys_then_swaps_in_the_litellm_master_key():
    text = expose.render_caddyfile(hosts=["box.local", "localhost"], port=8443, litellm=True)
    assert "https://box.local:8443, https://localhost:8443" in text
    assert "tls internal" in text
    assert "forward_auth 127.0.0.1:11435" in text
    assert "uri /auth/check" in text
    assert "reverse_proxy 127.0.0.1:4000" in text
    assert 'header_up Authorization "Bearer {env.LITELLM_MASTER_KEY}"' in text


def test_caddyfile_without_litellm_points_at_the_daemon_and_keeps_the_app_key():
    text = expose.render_caddyfile(hosts=["box.local"], port=8443, litellm=False)
    assert "reverse_proxy 127.0.0.1:11435" in text
    assert "header_up Authorization" not in text


@pytest.mark.skipif(not shutil.which("caddy"), reason="caddy not installed")
def test_caddyfile_is_valid_caddy_config(tmp_path):
    path = tmp_path / "Caddyfile"
    path.write_text(expose.render_caddyfile(hosts=["box.local"], port=8443, litellm=True))
    result = subprocess.run(["caddy", "adapt", "--config", str(path), "--adapter", "caddyfile"],
                            capture_output=True, text=True, env={"LITELLM_MASTER_KEY": "x", "PATH": "/usr/bin"})
    assert result.returncode == 0, result.stderr


def test_litellm_config_lists_each_registered_model_and_routes_it_to_the_daemon():
    store = Store.open()
    store.add_model(name="qwen2.5-0.5b", repo_id="Qwen/Qwen2.5-0.5B-Instruct", revision="r",
                    source="max-catalog", architecture="Qwen2ForCausalLM")
    config = yaml.safe_load(expose.render_litellm_config(store.models()))
    names = [m["model_name"] for m in config["model_list"]]
    assert names == ["qwen2.5-0.5b"]
    first = config["model_list"][0]["litellm_params"]
    assert first["model"] == "openai/qwen2.5-0.5b"
    assert first["api_base"] == "http://127.0.0.1:11435/v1"
    assert first["api_key"] == "os.environ/MST_UPSTREAM_KEY"
    assert config["general_settings"]["master_key"] == "os.environ/LITELLM_MASTER_KEY"


def test_expose_dry_run_writes_configs_and_prints_the_url(mst_home):
    result = runner.invoke(app, ["expose", "--dry-run", "--host", "box.local"])
    assert result.exit_code == 0, result.output
    assert "https://box.local:8443/v1" in result.output
    assert (mst_home / "expose" / "Caddyfile").exists()
    assert (mst_home / "expose" / "litellm.yaml").exists()
    assert "root.crt" in result.output  # how to trust the CA on another device


def test_expose_starts_and_stops_both_processes(mst_home, monkeypatch):
    monkeypatch.setenv("MST_CADDY_CMD", "sleep 60")
    monkeypatch.setenv("MST_LITELLM_CMD", "sleep 60")
    result = runner.invoke(app, ["expose", "--host", "box.local"])
    assert result.exit_code == 0, result.output
    running = expose.status()
    assert running == {"caddy": True, "litellm": True}

    result = runner.invoke(app, ["expose", "--stop"])
    assert result.exit_code == 0, result.output
    assert expose.status() == {"caddy": False, "litellm": False}


def test_expose_no_litellm_starts_only_caddy(mst_home, monkeypatch):
    monkeypatch.setenv("MST_CADDY_CMD", "sleep 60")
    monkeypatch.setenv("MST_LITELLM_CMD", "sleep 60")
    result = runner.invoke(app, ["expose", "--no-litellm", "--host", "box.local"])
    assert result.exit_code == 0, result.output
    assert expose.status() == {"caddy": True, "litellm": False}
    runner.invoke(app, ["expose", "--stop"])


def test_refresh_restarts_litellm_with_new_models_only_when_exposed(mst_home, monkeypatch):
    monkeypatch.setenv("MST_CADDY_CMD", "sleep 60")
    monkeypatch.setenv("MST_LITELLM_CMD", "sleep 60")
    assert expose.refresh_if_running() is False
    runner.invoke(app, ["expose", "--host", "box.local"])
    Store.open().add_model(name="smollm2-1.7b", repo_id="HuggingFaceTB/SmolLM2-1.7B-Instruct", revision="r",
                           source="hf-supported", architecture="LlamaForCausalLM")
    assert expose.refresh_if_running() is True
    config = yaml.safe_load((mst_home / "expose" / "litellm.yaml").read_text())
    assert [m["model_name"] for m in config["model_list"]] == ["smollm2-1.7b"]
    assert "https://box.local:8443" in (mst_home / "expose" / "Caddyfile").read_text()
    runner.invoke(app, ["expose", "--stop"])
