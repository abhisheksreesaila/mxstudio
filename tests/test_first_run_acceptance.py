from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from notebooks.mxstudio import compat, notebooks, settings, state
from notebooks.mxstudio.api import app
from scripts import first_run_live


MODEL_ID = "Qwen/Qwen2.5-0.5B-Instruct"


async def _chat_stream():
    yield SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content="Hello from MAX"))]
    )


class FirstRunAcceptanceTests(unittest.TestCase):
    def test_environment_detection_uses_repository_pixi_directory(self) -> None:
        project_root = Path(__file__).resolve().parents[1]

        self.assertEqual(compat.PROJECT_ROOT, project_root)
        self.assertEqual(
            compat.PROJECT_ENVIRONMENT,
            project_root / ".pixi" / "envs" / "default",
        )

    @patch("scripts.first_run_live.TestClient")
    def test_live_journey_refuses_active_model_before_hf_requests(self, test_client) -> None:
        client = MagicMock()
        test_client.return_value = client

        def response(payload):
            result = MagicMock(status_code=200, text="")
            result.json.return_value = payload
            return result

        client.get.side_effect = [
            response({"hf_token_set": False}),
            response({"project_environment": True, "max_cli": True}),
            response({"state": "running", "model_id": "allenai/OLMoE"}),
        ]

        with self.assertRaisesRegex(RuntimeError, "another model is active"):
            first_run_live.run(MODEL_ID, timeout=1, keep_server=False)

        client.post.assert_not_called()

    def test_public_model_journey_without_hf_token(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            notebooks_dir = root / "notebooks"
            secrets_file = root / ".mxstudio" / "secrets.json"
            compatibility = {
                "ok": True,
                "model_id": MODEL_ID,
                "gated": False,
                "can_download": True,
                "modular_compatible": True,
                "architecture": "Qwen2ForCausalLM",
                "hardware": {"level": "ok", "message": "Fits for this test."},
            }
            environment = {
                "hf_token_set": False,
                "hf_token_hint": "missing - gated models will fail; set token in Settings",
                "ready": True,
                "project_environment": True,
                "environment_kind": "pixi",
                "environment_path": str(root / ".pixi" / "envs" / "default"),
                "setup_confirmed": False,
            }
            queued_download = {
                "job_id": "job-download",
                "kind": "download",
                "state": "processing",
            }

            with (
                patch.dict(
                    "os.environ",
                    {"HF_TOKEN": "", "HUGGING_FACE_HUB_TOKEN": ""},
                ),
                patch.object(state, "HOME", secrets_file.parent),
                patch.object(settings, "SECRETS_FILE", secrets_file),
                patch.object(notebooks, "NOTEBOOKS_DIR", notebooks_dir),
                patch(
                    "notebooks.mxstudio.modelinfo.summarize_model_card",
                    return_value="",
                ),
                patch(
                    "notebooks.mxstudio.compat.hardware_fit",
                    return_value=compatibility["hardware"],
                ),
            ):
                client = TestClient(app)

                settings_response = client.get("/api/settings")
                self.assertEqual(settings_response.status_code, 200)
                self.assertFalse(settings_response.json()["hf_token_set"])

                with patch(
                    "notebooks.mxstudio.api.compat.env_status",
                    return_value=environment,
                ):
                    env_response = client.get("/api/env")
                self.assertTrue(env_response.json()["ready"])
                self.assertFalse(env_response.json()["hf_token_set"])

                with patch(
                    "notebooks.mxstudio.api.compat.check_hf_model",
                    return_value=compatibility,
                ):
                    check_response = client.post(
                        "/api/models/check",
                        json={"model_id": MODEL_ID, "sync": True},
                    )
                report = check_response.json()
                self.assertFalse(report["gated"])
                self.assertTrue(report["can_download"])
                self.assertTrue(report["modular_compatible"])

                with patch(
                    "notebooks.mxstudio.api.jobs.start_kind",
                    return_value=queued_download,
                ) as start_kind:
                    download_response = client.post(
                        f"/api/models/{MODEL_ID}/download",
                        json={"force": False},
                    )
                self.assertEqual(download_response.status_code, 200)
                start_kind.assert_called_once_with(
                    "download", model_id=MODEL_ID, force=False, curated=True
                )

                notebook_response = client.post(f"/api/models/{MODEL_ID}/notebook")
                self.assertEqual(notebook_response.status_code, 200)
                notebook_name = notebook_response.json()["name"]
                loaded_notebook = client.get(f"/api/notebooks/{notebook_name}")
                self.assertEqual(loaded_notebook.status_code, 200)
                self.assertGreater(len(loaded_notebook.json()["cells"]), 2)

                slides_response = client.get(
                    f"/api/notebooks/{notebook_name}/slides"
                )
                self.assertEqual(slides_response.status_code, 200)
                self.assertGreaterEqual(len(slides_response.json()["slides"]), 8)

                with (
                    patch(
                        "notebooks.mxstudio.api.downloads.is_installed",
                        return_value=True,
                    ),
                    patch(
                        "notebooks.mxstudio.api.SERVERS.start",
                        return_value={
                            "state": "starting",
                            "model_id": MODEL_ID,
                        },
                    ) as server_start,
                ):
                    serve_response = client.post(
                        "/api/server/start", json={"model_id": MODEL_ID}
                    )
                self.assertEqual(serve_response.status_code, 200)
                self.assertEqual(server_start.call_args.args[0].id, MODEL_ID)

                with (
                    patch(
                        "notebooks.mxstudio.api.SERVERS.status",
                        return_value={"state": "running", "model_id": MODEL_ID},
                    ),
                    patch("notebooks.mxstudio.api.AsyncOpenAI") as openai_client,
                ):
                    openai_client.return_value.chat.completions.create = AsyncMock(
                        return_value=_chat_stream()
                    )
                    openai_client.return_value.close = AsyncMock()
                    chat_response = client.post(
                        "/api/chat",
                        json={
                            "messages": [{"role": "user", "content": "Say hello"}],
                            "max_tokens": 16,
                        },
                    )
                self.assertEqual(chat_response.status_code, 200)
                openai_client.return_value.close.assert_awaited_once()
                self.assertIn("Hello from MAX", chat_response.text)
                self.assertIn('"done": true', chat_response.text)


if __name__ == "__main__":
    unittest.main()