#!/usr/bin/env python3
"""Exercise the first-run mxstudio journey with real Hugging Face and MAX I/O."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
NOTEBOOKS_ROOT = ROOT / "notebooks"
sys.path.insert(0, str(ROOT))
os.environ["PYTHONPATH"] = os.pathsep.join(
    part for part in (str(NOTEBOOKS_ROOT), os.environ.get("PYTHONPATH", "")) if part
)

from notebooks.mxstudio import downloads, jobs  # noqa: E402
from notebooks.mxstudio.api import app  # noqa: E402


DEFAULT_MODEL = "Qwen/Qwen2.5-0.5B-Instruct"


def _require(response, stage: str) -> dict:
    if response.status_code >= 400:
        raise RuntimeError(f"{stage} failed ({response.status_code}): {response.text}")
    return response.json()


def _wait_for_job(job_id: str, timeout: float) -> dict:
    deadline = time.time() + timeout
    last_detail = None
    while time.time() < deadline:
        job = jobs.get(job_id)
        if job is None:
            raise RuntimeError(f"job disappeared: {job_id}")
        detail = job.get("detail")
        if detail and detail != last_detail:
            print(f"  {detail}")
            last_detail = detail
        if job["state"] == "done":
            return job
        if job["state"] == "failed":
            raise RuntimeError(job.get("error") or f"job failed: {job_id}")
        time.sleep(1)
    raise TimeoutError(f"job did not finish within {timeout:.0f}s: {job_id}")


def _wait_for_server(client: TestClient, model_id: str, timeout: float) -> dict:
    deadline = time.time() + timeout
    last_state = None
    while time.time() < deadline:
        status = _require(client.get("/api/server"), "server status")
        if status.get("state") != last_state:
            print(f"  server: {status.get('state')}")
            last_state = status.get("state")
        if status.get("state") == "running" and status.get("model_id") == model_id:
            return status
        if status.get("state") == "crashed":
            raise RuntimeError(status.get("log_tail") or status.get("crash_reason"))
        time.sleep(2)
    raise TimeoutError(f"MAX server did not become ready within {timeout:.0f}s")


def run(model_id: str, timeout: float, keep_server: bool) -> None:
    client = TestClient(app)
    started_server = False

    print("[1/7] Checking clean-user settings and environment")
    public_settings = _require(client.get("/api/settings"), "settings")
    environment = _require(client.get("/api/env"), "environment")
    print(
        "  HF token: "
        + ("configured" if public_settings["hf_token_set"] else "not configured (OK for public models)")
    )
    if not environment.get("project_environment"):
        raise RuntimeError(
            "mxstudio is not running in the repository Pixi environment; use `pixi run`."
        )
    if not environment.get("max_cli"):
        raise RuntimeError("MAX CLI is unavailable in the Pixi environment")
    current = _require(client.get("/api/server"), "current server")
    if current.get("state") in {"running", "starting"} and current.get("model_id") != model_id:
        raise RuntimeError(
            f"another model is active ({current.get('model_id')}); stop it before this test"
        )

    print(f"[2/7] Inspecting public Hugging Face model: {model_id}")
    report = _require(
        client.post("/api/models/check", json={"model_id": model_id, "sync": True}),
        "Hugging Face inspection",
    )
    if report.get("gated") and not public_settings["hf_token_set"]:
        raise RuntimeError("model is gated and no Hugging Face token is configured")
    if not report.get("can_download"):
        raise RuntimeError(report.get("hint") or report.get("error") or "model cannot download")
    if not report.get("modular_compatible"):
        raise RuntimeError(
            f"{report.get('architecture') or 'model architecture'} is not built into MAX"
        )
    print(f"  architecture: {report.get('architecture')} (built into MAX)")

    print("[3/7] Downloading model through mxstudio")
    if downloads.is_installed(model_id):
        print("  already cached; reusing the complete snapshot")
    else:
        queued = _require(
            client.post(f"/api/models/{model_id}/download", json={"force": False}),
            "download dispatch",
        )
        _wait_for_job(queued["job_id"], timeout)
    local = _require(client.get(f"/api/models/{model_id}/local"), "local model")
    if not local.get("installed"):
        raise RuntimeError("download finished without a complete safetensors snapshot")
    print(f"  cache: {local['path']}")

    print("[4/7] Opening model details and model card")
    card = _require(client.get(f"/api/models/{model_id}/card"), "model card")
    if not str(card.get("markdown") or "").strip():
        raise RuntimeError("model details returned an empty Hugging Face card")

    print("[5/7] Generating notebook and presentation")
    notebook = _require(
        client.post(f"/api/models/{model_id}/notebook"), "notebook generation"
    )
    loaded = _require(client.get(f"/api/notebooks/{notebook['name']}"), "notebook load")
    slides = _require(
        client.get(f"/api/notebooks/{notebook['name']}/slides"), "slides load"
    )
    if len(loaded.get("cells") or []) < 3 or len(slides.get("slides") or []) < 8:
        raise RuntimeError("learning artifacts are incomplete")
    print(f"  notebook: {notebook['name']} ({len(loaded['cells'])} cells)")
    print(f"  slides: {len(slides['slides'])}")

    try:
        print("[6/7] Starting MAX endpoint")
        if current.get("state") != "running":
            _require(
                client.post("/api/server/start", json={"model_id": model_id}),
                "server start",
            )
            started_server = True
        _wait_for_server(client, model_id, timeout)

        print("[7/7] Sending a streaming chat request")
        chat = client.post(
            "/api/chat",
            json={
                "messages": [
                    {
                        "role": "user",
                        "content": "Reply with exactly: mxstudio first run works",
                    }
                ],
                "temperature": 0,
                "max_tokens": 24,
            },
        )
        if chat.status_code >= 400:
            raise RuntimeError(f"chat failed ({chat.status_code}): {chat.text}")
        events = [
            json.loads(line.removeprefix("data: "))
            for line in chat.text.splitlines()
            if line.startswith("data: ")
        ]
        errors = [event["error"] for event in events if event.get("error")]
        answer = "".join(str(event.get("delta") or "") for event in events).strip()
        if errors:
            raise RuntimeError(errors[0])
        if not answer:
            raise RuntimeError("chat completed without response text")
        print(f"  response: {answer}")
        print("PASS: download, details, notebook, slides, serve, and chat all worked")
    finally:
        if started_server and not keep_server:
            client.post("/api/server/stop")
            print("  stopped the test server")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--keep-server", action="store_true")
    args = parser.parse_args()
    run(args.model, args.timeout, args.keep_server)


if __name__ == "__main__":
    main()