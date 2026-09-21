from __future__ import annotations

import json
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from notebooks.mxstudio.api import app
from notebooks.mxstudio.registry import get
from notebooks.mxstudio.servers import ServerManager


class IsolatedServerState(unittest.TestCase):
    def setUp(self):
        from notebooks.mxstudio import state
        home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for name, value in (("HOME", home), ("LOGS", home / "logs"), ("STATE_FILE", home / "state.json")):
            self.enterContext(patch.object(state, name, value))
        self.enterContext(patch("notebooks.mxstudio.servers._process_identity", return_value="test"))


class JobApiReliabilityTests(IsolatedServerState):
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
        self.assertNotIn("--host", command)
        self.assertEqual(popen.call_args.kwargs["env"]["MAX_SERVE_HOST"], "127.0.0.1")
        self.assertNotIn("--model-path", command)
        self.assertIn("--custom-architectures", command)
        self.assertEqual(command[command.index("--max-batch-size") + 1], "1")

    @patch("notebooks.mxstudio.servers.platform.system", return_value="Darwin")
    @patch.object(ServerManager, "status", return_value={"state": "starting"})
    @patch("notebooks.mxstudio.servers.state.update")
    @patch("notebooks.mxstudio.servers.state.load", return_value={})
    @patch("notebooks.mxstudio.servers.subprocess.Popen")
    @patch("builtins.open", new_callable=MagicMock)
    def test_macos_start_uses_cpu_without_cuda_environment(
        self,
        _open,
        popen,
        _state_load,
        _state_update,
        _status,
        _platform_system,
    ) -> None:
        popen.return_value.pid = 1234
        entry = get("Qwen/Qwen2.5-0.5B-Instruct")
        self.assertIsNotNone(entry)

        ServerManager().start(entry)

        command = popen.call_args.args[0]
        environment = popen.call_args.kwargs["env"]
        self.assertEqual(command[command.index("--devices") + 1], "cpu")
        self.assertNotIn("CUDA_VISIBLE_DEVICES", environment)


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


class AgentApiTests(IsolatedServerState):
    @patch("notebooks.mxstudio.agents.dispatch_tool")
    @patch("notebooks.mxstudio.api.AsyncOpenAI")
    @patch("notebooks.mxstudio.api.SERVERS.status")
    def test_agent_returns_tool_trace_and_final_answer(
        self,
        server_status,
        openai_client,
        dispatch_tool,
    ) -> None:
        server_status.return_value = {"state": "running", "model_id": "acme/demo"}
        dispatch_tool.return_value = {"state": "running", "model_id": "acme/demo"}
        openai_client.return_value.close = AsyncMock()

        tool_message = MagicMock()
        tool_message.content = None
        tool_message.tool_calls = [
            SimpleNamespace(
                id="call-1",
                function=SimpleNamespace(name="get_server_status", arguments="{}"),
            )
        ]
        tool_message.model_dump.return_value = {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "get_server_status", "arguments": "{}"},
                }
            ],
        }
        final_message = MagicMock()
        final_message.content = "The local MAX server is running."
        final_message.tool_calls = []

        create = AsyncMock(
            side_effect=[
                SimpleNamespace(choices=[SimpleNamespace(message=tool_message)]),
                SimpleNamespace(choices=[SimpleNamespace(message=final_message)]),
            ]
        )
        openai_client.return_value.chat.completions.create = create

        response = TestClient(app).post(
            "/api/agent",
            json={"messages": [{"role": "user", "content": "Is MAX running?"}]},
        )

        self.assertEqual(response.status_code, 200)
        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        self.assertEqual(
            [event["type"] for event in events],
            ["start", "step", "tool-call", "tool-result", "step", "delta", "finish"],
        )
        self.assertEqual(events[2]["name"], "get_server_status")
        self.assertEqual(events[3]["result"]["state"], "running")
        self.assertEqual(events[5]["delta"], "The local MAX server is running.")
        second_messages = create.await_args_list[1].kwargs["messages"]
        self.assertEqual(second_messages[-1]["role"], "tool")
        self.assertEqual(second_messages[-1]["tool_call_id"], "call-1")

    @patch("notebooks.mxstudio.agents.dispatch_tool", return_value={"state": "running"})
    @patch("notebooks.mxstudio.api.AsyncOpenAI")
    @patch("notebooks.mxstudio.api.SERVERS.status")
    def test_agent_stops_repeated_tool_calls_at_step_limit(
        self,
        server_status,
        openai_client,
        _dispatch_tool,
    ) -> None:
        server_status.return_value = {"state": "running", "model_id": "acme/demo"}
        openai_client.return_value.close = AsyncMock()
        tool_message = MagicMock()
        tool_message.content = None
        tool_message.tool_calls = [
            SimpleNamespace(
                id="call-loop",
                function=SimpleNamespace(name="get_server_status", arguments="{}"),
            )
        ]
        tool_message.model_dump.return_value = {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call-loop",
                    "type": "function",
                    "function": {"name": "get_server_status", "arguments": "{}"},
                }
            ],
        }
        create = AsyncMock(
            return_value=SimpleNamespace(
                choices=[SimpleNamespace(message=tool_message)]
            )
        )
        openai_client.return_value.chat.completions.create = create

        response = TestClient(app).post(
            "/api/agent",
            json={
                "messages": [{"role": "user", "content": "Keep checking."}],
                "max_steps": 2,
            },
        )

        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        self.assertEqual(create.await_count, 2)
        self.assertEqual(events[-1]["type"], "finish")
        self.assertEqual(events[-1]["reason"], "max_steps")
        self.assertEqual(events[-1]["steps"], 2)


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
