from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from notebooks.mxstudio.api import app
from notebooks.mxstudio.registry import get
from notebooks.mxstudio.servers import ServerManager


class JobApiReliabilityTests(unittest.TestCase):
    def test_job_start_reads_json_body(self) -> None:
        response = TestClient(app).post(
            "/api/jobs/start",
            json={"kind": "not-a-real-job"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("unknown job kind", response.json()["detail"])

    @patch("notebooks.mxstudio.api.SERVERS.start")
    @patch("notebooks.mxstudio.api.downloads.is_installed", return_value=True)
    def test_verified_olmoe_model_can_start(
        self,
        _installed,
        server_start,
    ) -> None:
        server_start.return_value = {"state": "starting"}

        response = TestClient(app).post(
            "/api/server/start",
            json={"model_id": "allenai/OLMoE-1B-7B-0125-Instruct"},
        )

        self.assertEqual(response.status_code, 200)
        entry = server_start.call_args.args[0]
        self.assertTrue(entry.serve_enabled)
        self.assertEqual(entry.custom_arch, "ports/olmoe_1b_7b_0125_instruct")

    @patch.object(ServerManager, "status", return_value={"state": "starting"})
    @patch("notebooks.mxstudio.servers.state.update")
    @patch("notebooks.mxstudio.servers.state.load", return_value={})
    @patch("notebooks.mxstudio.servers.subprocess.Popen")
    @patch("builtins.open", new_callable=MagicMock)
    def test_olmoe_start_uses_current_max_arguments(
        self,
        _open,
        popen,
        _state_load,
        _state_update,
        _status,
    ) -> None:
        popen.return_value.pid = 1234
        entry = get("allenai/OLMoE-1B-7B-0125-Instruct")
        self.assertIsNotNone(entry)

        ServerManager().start(entry)

        command = popen.call_args.args[0]
        self.assertIn("--model", command)
        self.assertNotIn("--model-path", command)
        self.assertIn("--custom-architectures", command)
        self.assertEqual(command[command.index("--max-batch-size") + 1], "1")


class SetupApiTests(unittest.TestCase):
    @patch("notebooks.mxstudio.api.setup.acknowledge")
    @patch("notebooks.mxstudio.api.compat.env_status")
    def test_setup_confirms_current_project_environment(self, env_status, acknowledge) -> None:
        env_status.return_value = {
            "project_environment": True,
            "environment_kind": "pixi",
            "environment_path": "/project/.pixi/envs/default",
            "setup_confirmed": False,
        }

        response = TestClient(app).put("/api/setup")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["setup_confirmed"])
        acknowledge.assert_called_once_with("pixi", "/project/.pixi/envs/default")

    @patch("notebooks.mxstudio.api.setup.acknowledge")
    @patch("notebooks.mxstudio.api.compat.env_status")
    def test_setup_rejects_system_environment(self, env_status, acknowledge) -> None:
        env_status.return_value = {
            "project_environment": False,
            "environment_kind": "system",
            "environment_path": "/usr",
        }

        response = TestClient(app).put("/api/setup")

        self.assertEqual(response.status_code, 409)
        self.assertIn("Pixi", response.json()["detail"]["message"])
        acknowledge.assert_not_called()


class FolderApiReliabilityTests(unittest.TestCase):
    @patch("notebooks.mxstudio.api.architectures.open_package_folder")
    def test_architecture_folder_returns_windows_path(self, open_folder) -> None:
        open_folder.return_value = {
            "ok": True,
            "opened": True,
            "target": "/home/user/project/ports/demo",
            "target_windows": r"\\wsl.localhost\Ubuntu\home\user\project\ports\demo",
            "opened_with": "explorer.exe",
            "error": None,
        }

        response = TestClient(app).post(
            "/api/architectures/open-folder",
            json={"model_id": "acme/demo"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["opened"])
        self.assertTrue(response.json()["target_windows"].startswith("\\\\wsl.localhost"))

    @patch("notebooks.mxstudio.api.notebooks.open_notebook_folder")
    def test_notebook_folder_route(self, open_folder) -> None:
        open_folder.return_value = {
            "ok": True,
            "opened": True,
            "target": "/home/user/project/notebooks",
        }

        response = TestClient(app).post(
            "/api/notebooks/demo.ipynb/open-folder",
        )

        self.assertEqual(response.status_code, 200)
        open_folder.assert_called_once_with("demo.ipynb")


if __name__ == "__main__":
    unittest.main()
