from __future__ import annotations

import unittest
from unittest.mock import patch
import os
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock

from typer.testing import CliRunner

from notebooks.mxstudio import cli, deployments, inventory


class DeploymentCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    @patch.object(deployments, "get_run")
    @patch.object(deployments, "enqueue")
    @patch.object(deployments, "create_run")
    @patch("notebooks.mxstudio.cli.time.sleep")
    def test_up_uses_local_mode_emits_id_and_waits_for_completion(
        self, _sleep, create_run, enqueue, get_run
    ) -> None:
        create_run.return_value = {"run_id": "run-1", "state": "created"}
        enqueue.return_value = {"run_id": "run-1", "job_id": "job-1", "state": "running"}
        get_run.return_value = {
            "run_id": "run-1", "job_id": "job-1", "state": "local_ready",
            "stage": "generation_verified", "model": "org/model",
        }

        result = self.runner.invoke(
            cli.app,
            [
                "up", "org/model", "--local-only", "--revision", "abc123",
                "--hostname", "models.example.test", "--ca-file", "ca.pem",
            ],
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Run ID: run-1", result.output)
        self.assertIn("State: local_ready", result.output)
        create_run.assert_called_once_with(
            "org/model",
            revision="abc123",
            mode="local",
            hostname="models.example.test",
            ca_file="ca.pem",
        )
        enqueue.assert_called_once_with("run-1")
        get_run.assert_called_once_with("run-1")

    @patch.object(deployments, "enqueue")
    @patch.object(deployments, "create_run")
    @patch.object(deployments, "get_run")
    def test_pull_queues_pull_mode_and_no_wait_returns(
        self, get_run, create_run, enqueue
    ) -> None:
        create_run.return_value = {"run_id": "pull-1", "state": "created"}
        enqueue.return_value = {"run_id": "pull-1", "state": "queued", "job_id": "job-1"}

        result = self.runner.invoke(
            cli.app, ["pull", "org/model", "--revision", "v2", "--no-wait"]
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("pull-1", result.output)
        self.assertIn("queued", result.output)
        create_run.assert_called_once_with(
            "org/model",
            revision="v2",
            mode="pull",
            hostname="api.mxstudio.local",
            ca_file=None,
        )
        enqueue.assert_called_once_with("pull-1")
        get_run.assert_not_called()

    @patch.object(deployments, "enqueue")
    @patch.object(deployments, "create_run")
    def test_needs_setup_prints_recovery_and_requires_approval(
        self, create_run, enqueue
    ) -> None:
        create_run.return_value = {"run_id": "run-setup", "state": "created"}
        enqueue.return_value = {
            "run_id": "run-setup",
            "state": "needs_setup",
            "recovery": {"next": "review the service plan"},
        }

        result = self.runner.invoke(cli.app, ["up", "org/model"])

        self.assertEqual(result.exit_code, 1)
        self.assertIn("First-time service installation requires explicit approval.", result.output)
        self.assertIn("review the service plan", result.output)
        self.assertIn("pixi run mxstudio runs resume run-setup", result.output)
        self.assertNotIn("sudo", result.output.lower())
        self.assertNotIn("--apply", result.output)

    @patch.object(deployments, "enqueue")
    @patch.object(deployments, "create_run")
    def test_busy_blocked_run_exits_nonzero(self, create_run, enqueue) -> None:
        create_run.return_value = {"run_id": "run-busy", "state": "created"}
        enqueue.return_value = {
            "run_id": "run-busy", "state": "blocked", "error": "busy"
        }

        result = self.runner.invoke(cli.app, ["up", "org/model", "--no-wait"])

        self.assertEqual(result.exit_code, 1)
        self.assertIn("State: blocked", result.output)
        self.assertIn("Error: busy", result.output)

    @patch.object(deployments, "get_run")
    @patch.object(deployments, "enqueue")
    @patch.object(deployments, "create_run")
    @patch("notebooks.mxstudio.cli.time.sleep")
    def test_up_waits_through_verifying_until_failed(
        self, sleep, create_run, enqueue, get_run
    ) -> None:
        create_run.return_value = {"run_id": "run-verify", "state": "created"}
        enqueue.return_value = {
            "run_id": "run-verify", "job_id": "job-verify", "state": "running",
        }
        get_run.side_effect = [
            {"run_id": "run-verify", "state": "verifying", "stage": "https_verify"},
            {
                "run_id": "run-verify", "state": "failed",
                "error": "Endpoint verification failed",
            },
        ]

        result = self.runner.invoke(cli.app, ["up", "org/model"])

        self.assertEqual(result.exit_code, 1)
        self.assertIn("State: failed", result.output)
        self.assertIn("Error: Endpoint verification failed", result.output)
        self.assertEqual(get_run.call_count, 2)
        self.assertEqual(sleep.call_count, 2)

    @patch.object(deployments, "get_run")
    def test_needs_input_is_terminal_failure(self, get_run) -> None:
        get_run.return_value = {
            "run_id": "run-input",
            "state": "needs_input",
            "recovery": "Choose one of the listed model candidates.",
        }

        result = self.runner.invoke(cli.app, ["runs", "inspect", "run-input"])

        self.assertEqual(result.exit_code, 1)
        self.assertIn("State: needs_input", result.output)
        self.assertIn("Choose one", result.output)

    @patch.object(
        deployments,
        "get_run",
        return_value={
            "run_id": "run-client",
            "state": "awaiting_client_verification",
            "recovery": "Run endpoint probe from a second device.",
        },
    )
    def test_awaiting_client_verification_is_not_reported_as_complete(
        self, _get_run
    ) -> None:
        result = self.runner.invoke(cli.app, ["runs", "inspect", "run-client"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("State: awaiting_client_verification", result.output)
        self.assertIn("second device", result.output)

    @patch.object(
        deployments,
        "resume",
        return_value={
            "run_id": "run-interrupted",
            "state": "interrupted",
            "recovery": "Inspect stage history before retrying.",
        },
    )
    def test_interrupted_resume_is_terminal_and_nonzero(self, _resume) -> None:
        result = self.runner.invoke(
            cli.app, ["runs", "resume", "run-interrupted"]
        )
        self.assertEqual(result.exit_code, 1)
        self.assertIn("State: interrupted", result.output)
        self.assertIn("Inspect stage history", result.output)

    @patch.object(deployments, "get_run")
    @patch.object(deployments, "resume")
    @patch("notebooks.mxstudio.cli.time.sleep")
    def test_runs_resume_waits_for_the_resumed_run(
        self, _sleep, resume, get_run
    ) -> None:
        resume.return_value = {"run_id": "run-2", "state": "queued", "job_id": "job-2"}
        get_run.return_value = {"run_id": "run-2", "state": "ready", "model": "org/model"}

        result = self.runner.invoke(cli.app, ["runs", "resume", "run-2"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("State: ready", result.output)
        resume.assert_called_once_with("run-2")
        get_run.assert_called_once_with("run-2")

    @patch.object(deployments, "get_run", return_value={"run_id": "run-3", "state": "ready"})
    @patch.object(deployments, "list_runs", return_value=[{"run_id": "run-3", "state": "ready"}])
    def test_run_inspect_and_list(self, list_runs, get_run) -> None:
        inspected = self.runner.invoke(cli.app, ["runs", "inspect", "run-3", "--json"])
        listed = self.runner.invoke(cli.app, ["runs", "list", "--json"])
        self.assertEqual(inspected.exit_code, 0, inspected.output)
        self.assertEqual(listed.exit_code, 0, listed.output)
        self.assertIn('"run_id": "run-3"', inspected.output)
        self.assertIn('"state": "ready"', listed.output)
        get_run.assert_called_once_with("run-3")
        list_runs.assert_called_once_with()

    @patch.object(inventory, "catalog", return_value={"source": "max", "models": []})
    def test_catalog_delegates_to_inventory(self, catalog) -> None:
        result = self.runner.invoke(cli.app, ["catalog", "--json"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn('"source": "max"', result.output)
        catalog.assert_called_once_with()

    @patch.object(deployments, "stop_endpoint", return_value={"state": "stopped"})
    @patch.object(deployments, "verify_endpoint", return_value={"state": "awaiting_client_verification"})
    @patch.object(deployments, "list_endpoints", return_value=[{"run_id": "run-4"}])
    def test_endpoint_commands_delegate(self, list_endpoints, verify_endpoint, stop_endpoint) -> None:
        listed = self.runner.invoke(cli.app, ["endpoint", "list", "--json"])
        verified = self.runner.invoke(
            cli.app, ["endpoint", "verify", "run-4", "--ca-file", "ca.pem"]
        )
        stopped = self.runner.invoke(cli.app, ["endpoint", "stop", "run-4"])

        self.assertEqual(listed.exit_code, 0, listed.output)
        self.assertEqual(verified.exit_code, 0, verified.output)
        self.assertEqual(stopped.exit_code, 0, stopped.output)
        list_endpoints.assert_called_once_with()
        verify_endpoint.assert_called_once_with("run-4", ca_file="ca.pem")
        stop_endpoint.assert_called_once_with("run-4")

    @patch.object(deployments, "probe")
    def test_endpoint_probe_uses_env_key_and_prints_client_proof_without_secrets(
        self, probe
    ) -> None:
        proof = {
            "base_url": "https://api.example.test/v1",
            "model_id": "org/model",
            "content_bytes": 5,
            "stream_complete": True,
            "vantage": "server",
            "machine_fingerprint": "sha256:client-1",
            "nested": {"authorization": "never-print-this"},
        }
        probe.return_value = proof
        with patch.dict(os.environ, {"MXSTUDIO_NUCLEUS_API_KEY": "test-secret-key"}):
            result = self.runner.invoke(
                cli.app,
                [
                    "endpoint", "probe", "--url", "https://api.example.test/v1",
                    "--model", "org/model", "--ca-file", "client-ca.pem",
                ],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn('"vantage": "client"', result.output)
        self.assertIn('"machine_fingerprint": "sha256:client-1"', result.output)
        self.assertNotIn("test-secret-key", result.output)
        self.assertNotIn("never-print-this", result.output)
        probe.assert_called_once_with(
            "https://api.example.test/v1",
            "org/model",
            "test-secret-key",
            ca_file="client-ca.pem",
            require_https=True,
        )

    @patch.object(deployments, "probe")
    def test_endpoint_probe_requires_environment_key(self, probe) -> None:
        with patch.dict(os.environ, {}, clear=True):
            result = self.runner.invoke(
                cli.app,
                [
                    "endpoint", "probe", "--url", "https://api.example.test/v1",
                    "--model", "org/model",
                ],
            )
        self.assertEqual(result.exit_code, 1)
        self.assertIn("MXSTUDIO_NUCLEUS_API_KEY", result.output)
        probe.assert_not_called()

    def test_accept_client_reads_bounded_json_and_posts_authenticated_proof(self) -> None:
        proof = {
            "base_url": "https://api.example.test/v1",
            "model_id": "org/model",
            "content_bytes": 4,
            "stream_complete": True,
            "vantage": "client",
            "machine_fingerprint": "sha256:client-1",
        }
        response = MagicMock(status_code=200)
        response.json.return_value = {"run_id": "run-5", "state": "ready"}
        client = MagicMock()
        client.__enter__.return_value = client
        client.post.return_value = response

        with (
            patch("notebooks.mxstudio.cli._read_client_evidence", return_value=proof),
            patch("httpx.Client", return_value=client) as http_client,
            patch.dict(os.environ, {"MXSTUDIO_NUCLEUS_API_KEY": "test-secret-key"}),
        ):
            result = self.runner.invoke(
                cli.app,
                ["endpoint", "accept-client", "run-5", "--evidence", "proof.json"],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn('"state": "ready"', result.output)
        self.assertNotIn("test-secret-key", result.output)
        http_client.assert_called_once_with(
            base_url="http://127.0.0.1:8080",
            timeout=10,
            trust_env=False,
        )
        client.post.assert_called_once_with(
            "/nucleus/endpoints/run-5/client-evidence",
            headers={"Authorization": "Bearer test-secret-key"},
            json=proof,
        )

    def test_client_evidence_reader_enforces_size_limit_without_disk_io(self) -> None:
        valid = b'{"vantage":"client","machine_fingerprint":"fp","stream_complete":true,"content_bytes":1}'
        with patch.object(Path, "open", return_value=BytesIO(valid)):
            self.assertEqual(cli._read_client_evidence("not-created.json")["vantage"], "client")
        with patch.object(Path, "open", return_value=BytesIO(b"x" * (64 * 1024 + 1))):
            result = self.runner.invoke(
                cli.app,
                ["endpoint", "accept-client", "run-5", "--evidence", "oversized.json"],
            )
        self.assertEqual(result.exit_code, 1)
        self.assertIn("64 KiB limit", result.output)


if __name__ == "__main__":
    unittest.main()
