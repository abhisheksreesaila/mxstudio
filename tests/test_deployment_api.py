from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, call, patch

import httpx
from fastapi.testclient import TestClient

from notebooks.mxstudio import api, deployments


class DeploymentApiTests(unittest.TestCase):
    headers = {"Authorization": "Bearer deployment-test-key"}

    def setUp(self) -> None:
        self.environment = patch.dict(
            os.environ, {"MXSTUDIO_NUCLEUS_API_KEY": "deployment-test-key"}
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.client = TestClient(api.app)

    def test_management_routes_require_nucleus_auth(self) -> None:
        response = self.client.get("/nucleus/runs")
        self.assertEqual(response.status_code, 401)
        response = self.client.get("/nucleus/endpoints")
        self.assertEqual(response.status_code, 401)
        response = self.client.post(
            "/nucleus/runs",
            json={"query": "org/model"},
        )
        self.assertEqual(response.status_code, 401)
        response = self.client.post(
            "/nucleus/endpoints/run-1/client-evidence",
            json={"vantage": "client"},
        )
        self.assertEqual(response.status_code, 401)

    @patch.object(deployments, "enqueue")
    @patch.object(deployments, "create_run")
    def test_create_run_is_accepted_and_enqueued(
        self, create_run, enqueue
    ) -> None:
        create_run.return_value = {"run_id": "run-1", "state": "created"}
        enqueue.return_value = {
            "run_id": "run-1", "job_id": "job-1", "state": "queued"
        }

        response = self.client.post(
            "/nucleus/runs",
            headers=self.headers,
            json={
                "query": " org/model ",
                "revision": "abc123",
                "mode": "lan",
                "hostname": "models.example.test",
                "ca_file": "/etc/ssl/private/ca.pem",
            },
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["run_id"], "run-1")
        self.assertEqual(response.json()["job_id"], "job-1")
        create_run.assert_called_once_with(
            "org/model",
            revision="abc123",
            mode="lan",
            hostname="models.example.test",
            ca_file="/etc/ssl/private/ca.pem",
        )
        enqueue.assert_called_once_with("run-1")

    def test_create_run_rejects_invalid_options_before_domain_call(self) -> None:
        with patch.object(deployments, "create_run") as create_run:
            response = self.client.post(
                "/nucleus/runs",
                headers=self.headers,
                json={"query": "org/model", "mode": "public"},
            )
        self.assertEqual(response.status_code, 422)
        create_run.assert_not_called()

        response = self.client.post(
            "/nucleus/runs",
            headers=self.headers,
            json={"query": "   "},
        )
        self.assertEqual(response.status_code, 400)

    @patch.object(deployments, "list_runs", return_value=[{"run_id": "run-1"}])
    @patch.object(deployments, "get_run", return_value={"run_id": "run-1", "state": "ready"})
    @patch.object(deployments, "resume", return_value={"run_id": "run-1", "job_id": "job-2"})
    def test_runs_list_inspect_and_resume(self, resume, get_run, list_runs) -> None:
        self.assertEqual(
            self.client.get("/nucleus/runs", headers=self.headers).json(),
            [{"run_id": "run-1"}],
        )
        self.assertEqual(
            self.client.get("/nucleus/runs/run-1", headers=self.headers).json()["state"],
            "ready",
        )
        response = self.client.post(
            "/nucleus/runs/run-1/resume", headers=self.headers
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["job_id"], "job-2")
        resume.assert_called_once_with("run-1")
        get_run.assert_called_once_with("run-1")
        list_runs.assert_called_once_with()

    @patch.object(deployments, "get_run", return_value=None)
    def test_missing_run_is_not_found(self, _get_run) -> None:
        response = self.client.get("/nucleus/runs/missing", headers=self.headers)
        self.assertEqual(response.status_code, 404)

    def test_verify_queue_errors_are_safe(self) -> None:
        with (
            patch.object(
                deployments,
                "get_run",
                return_value={
                    "run_id": "run-1",
                    "endpoint": {"base_url": "https://example.test/v1"},
                },
            ),
            patch(
                "notebooks.mxstudio.api.jobs.enqueue_process",
                side_effect=RuntimeError("private credential detail"),
            ),
        ):
            response = self.client.post(
                "/nucleus/endpoints/run-1/verify",
                headers=self.headers,
                json={},
            )
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private credential detail", response.text)

    @patch.object(deployments, "list_endpoints", return_value=[{"run_id": "run-1"}])
    @patch("notebooks.mxstudio.api.jobs.enqueue_process")
    def test_endpoint_inventory_verify_and_stop(
        self, enqueue_process, list_endpoints
    ) -> None:
        enqueue_process.side_effect = [
            {"job_id": "job-verify"},
            {"job_id": "job-stop"},
        ]
        self.assertEqual(
            self.client.get("/nucleus/endpoints", headers=self.headers).json(),
            [{"run_id": "run-1"}],
        )
        with patch.object(
            deployments,
            "get_run",
            return_value={"run_id": "run-1", "endpoint": {"base_url": "https://example.test/v1"}},
        ):
            response = self.client.post(
                "/nucleus/endpoints/run-1/verify",
                headers=self.headers,
                json={"ca_file": "/etc/ssl/cert.pem"},
            )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["job_id"], "job-verify")
        with patch.object(
            deployments,
            "get_run",
            return_value={"run_id": "run-1", "endpoint": {"base_url": "https://example.test/v1"}},
        ):
            response = self.client.post(
                "/nucleus/endpoints/run-1/stop", headers=self.headers
            )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["job_id"], "job-stop")
        list_endpoints.assert_called_once_with()
        enqueue_process.assert_has_calls(
            [
                call(
                    "endpoint_verify",
                    "Verify endpoint run-1",
                    {"run_id": "run-1", "ca_file": "/etc/ssl/cert.pem"},
                ),
                call(
                    "endpoint_stop",
                    "Stop endpoint run-1",
                    {"run_id": "run-1"},
                ),
            ]
        )

    def test_client_evidence_is_bound_and_authenticated(self) -> None:
        proof = {
            "base_url": "https://api.example.test/v1",
            "model_id": "org/model",
            "content_bytes": 5,
            "stream_complete": True,
            "authentication_verified": True,
            "tls_verified": True,
            "private_routes_verified": True,
            "vantage": "client",
            "machine_fingerprint": "sha256:client-machine",
        }
        with patch.object(
            deployments,
            "record_client_evidence",
            return_value={"run_id": "run-1", "state": "ready"},
            create=True,
        ) as record:
            response = self.client.post(
                "/nucleus/endpoints/run-1/client-evidence",
                headers=self.headers,
                json=proof,
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["state"], "ready")
        record.assert_called_once_with("run-1", proof)

    def test_client_evidence_rejects_boolean_only_claim(self) -> None:
        with patch.object(
            deployments, "record_client_evidence", create=True
        ) as record:
            response = self.client.post(
                "/nucleus/endpoints/run-1/client-evidence",
                headers=self.headers,
                json={"vantage": "client", "tls_verified": True},
            )
        self.assertEqual(response.status_code, 400)
        record.assert_not_called()

    def test_authenticated_model_listing_keeps_canonical_id_and_revision(self) -> None:
        with (
            patch.object(
                api.SERVERS,
                "status",
                return_value={
                    "state": "running",
                    "model_id": "org/model",
                    "revision": "abc123def",
                    "advertised_model_id": "snapshot-derived-id",
                },
            ),
            patch.object(api.nucleus, "model_id", return_value="org/model"),
            patch.object(api.SERVERS, "start") as start,
            patch.object(api.nucleus.MANAGER, "prepare") as prepare,
        ):
            response = self.client.get("/v1/models", headers=self.headers)

        self.assertEqual(response.status_code, 200)
        record = response.json()["data"][0]
        self.assertEqual(record["id"], "org/model")
        self.assertEqual(record["model_id"], "org/model")
        self.assertEqual(record["revision"], "abc123def")
        start.assert_not_called()
        prepare.assert_not_called()

    def test_chat_maps_advertised_input_to_canonical_lease_and_backend_id(self) -> None:
        class FakeLease:
            async def __aenter__(self):
                return {
                    "model_id": "org/model",
                    "server": {
                        "model_id": "org/model",
                        "advertised_model_id": "snapshot-derived-id",
                    },
                }

            async def __aexit__(self, *_exc):
                return None

        upstream = httpx.Response(
            200, json={"object": "chat.completion", "choices": []}
        )
        with (
            patch.object(
                api.SERVERS,
                "status",
                return_value={
                    "state": "running",
                    "model_id": "org/model",
                    "revision": "abc123def",
                    "advertised_model_id": "snapshot-derived-id",
                },
            ),
            patch.object(api.nucleus.MANAGER, "lease", return_value=FakeLease()) as lease,
            patch.object(
                api.nucleus,
                "proxy_openai",
                new=AsyncMock(return_value=upstream),
            ) as proxy,
        ):
            response = self.client.post(
                "/v1/chat/completions",
                headers=self.headers,
                json={
                    "model": "snapshot-derived-id",
                    "messages": [{"role": "user", "content": "test"}],
                },
            )

        self.assertEqual(response.status_code, 200)
        lease.assert_called_once_with("org/model")
        self.assertEqual(
            proxy.call_args.args[1]["model"], "snapshot-derived-id"
        )


if __name__ == "__main__":
    unittest.main()
