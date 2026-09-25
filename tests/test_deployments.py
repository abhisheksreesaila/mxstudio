from __future__ import annotations

from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import httpx

from notebooks.mxstudio import deployments, inventory, jobs, jobs_db, registry, servers, state


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.home = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(state, "HOME", self.home))
        self.stack.enter_context(patch.object(state, "LOGS", self.home / "logs"))
        self.stack.enter_context(patch.object(state, "STATE_FILE", self.home / "state.json"))
        self.stack.enter_context(patch.object(jobs_db, "DB_PATH", self.home / "jobs.db"))
        self.stack.enter_context(patch.dict(os.environ, {"MXSTUDIO_NUCLEUS_API_KEY": "test-only-key"}))
        self.stack.enter_context(patch.object(deployments, "_installed_service", return_value={"unit_state": "active"}))

    def test_creation_validates_before_persisting(self):
        for kwargs in ({"query": ""}, {"query": "a/b", "mode": "public"},
                       {"query": "a/b", "hostname": "https://example.test"},
                       {"query": "a/b", "hostname": "bad{host}"}):
            with self.assertRaises(ValueError):
                deployments.create_run(**kwargs)
        self.assertEqual(deployments.list_runs(), [])

    def test_events_are_append_only_and_separate_from_run_payload(self):
        run = deployments.create_run("example/model", mode="pull")
        deployments._event(run, "resolve", "done", {"revision": "a" * 40})
        deployments._event(run, "resolve", "done", {"revision": "b" * 40})
        loaded = deployments.get_run(run["run_id"])
        self.assertEqual(len(loaded["events"]), 2)
        self.assertEqual(len(deployments.list_runs()), 1)
        self.assertNotIn("events", deployments.list_runs()[0])

    def test_queue_does_not_overwrite_worker_completion(self):
        run = deployments.create_run("example/model", mode="pull")
        def launch(*args):
            latest = deployments.get_run(run["run_id"])
            latest.pop("events")
            latest.update(state="downloaded", stage="download")
            deployments._save(latest)
            return {"job_id": "worker-1"}
        with patch.object(jobs, "enqueue_process", side_effect=launch):
            queued = deployments.enqueue(run["run_id"])
        self.assertEqual(queued["state"], "downloaded")
        self.assertEqual(queued["job_id"], "worker-1")

    def test_queue_reuses_active_job(self):
        run = deployments.create_run("example/model")
        run.update(state="running", job_id="worker-1")
        deployments._save(run)
        with patch.object(jobs, "get", return_value={"state": "processing"}), patch.object(jobs, "enqueue_process") as launch:
            self.assertEqual(deployments.enqueue(run["run_id"])["job_id"], "worker-1")
            launch.assert_not_called()

    def test_exclusive_lock_preserves_other_operation(self):
        with deployments._exclusive():
            with self.assertRaisesRegex(RuntimeError, "busy"):
                with deployments._exclusive():
                    self.fail("second operation acquired lock")

    def test_service_setup_secret_never_enters_result(self):
        run = deployments.create_run("example/model")
        run["model"] = {"model_id": "example/model", "revision": "a" * 40}
        setup = deployments._setup(run, "do-not-disclose")
        self.assertNotIn("do-not-disclose", json.dumps(setup))
        env = Path(setup["environment_file"])
        self.assertEqual(env.stat().st_mode & 0o777, 0o600)
        self.assertIn("NUCLEUS_MODEL_REVISION=" + "a" * 40, env.read_text())
        env.write_text("hand edited\n")
        with self.assertRaisesRegex(RuntimeError, "differs"):
            deployments._setup(run, "do-not-disclose")
        self.assertEqual(env.read_text(), "hand edited\n")

    def test_key_is_private_and_reusable(self):
        with patch.dict(os.environ, {"MXSTUDIO_NUCLEUS_API_KEY": ""}):
            first = deployments._key()
            self.assertEqual(first, deployments._key())
            path = self.home / "endpoint.key"
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            path.chmod(0o644)
            with self.assertRaisesRegex(RuntimeError, "private"):
                deployments._key()

    def test_stop_rejects_superseded_run(self):
        run = deployments.create_run("example/model")
        run["endpoint"] = {"base_url": deployments.LOCAL_URL}
        deployments._save(run)
        deployments._runtime("active_run", {"run_id": "another-run"})
        with self.assertRaisesRegex(RuntimeError, "no longer owns"):
            deployments.stop_endpoint(run["run_id"])

    def test_deployment_not_dispatchable_through_legacy_jobs_route(self):
        with self.assertRaises(ValueError):
            jobs.start_kind("deployment", run_id="anything")

    def _transport(self, *, content="ready", done=True, reject=True, private=404):
        def handle(request):
            if request.url.path in {"/nucleus/status", "/api/settings", "/"}:
                return httpx.Response(private)
            if request.headers.get("authorization") != "Bearer test-only-key" and reject:
                return httpx.Response(401)
            if request.url.path == "/v1/models":
                return httpx.Response(200, json={"data": [{"id": "example/model"}]})
            if request.url.path == "/v1/chat/completions":
                event = {"choices": [{"delta": {"content": content}}]}
                body = "data: " + json.dumps(event) + "\n\n"
                if done:
                    body += "data: [DONE]\n\n"
                return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=body)
            return httpx.Response(404)
        return httpx.MockTransport(handle)

    def _probe(self, **kwargs):
        transport = self._transport(**kwargs)
        client_class = httpx.Client
        with patch.object(deployments.httpx, "Client", side_effect=lambda **options: client_class(transport=transport, **options)):
            return deployments.probe("https://api.example.test/v1", "example/model", "test-only-key", require_https=True)

    def test_https_probe_requires_content_auth_and_private_routes(self):
        evidence = self._probe()
        self.assertTrue(evidence["stream_complete"])
        self.assertTrue(evidence["tls_verified"])
        self.assertEqual(evidence["vantage"], "server")
        self.assertNotIn("test-only-key", json.dumps(evidence))
        for options in ({"content": ""}, {"done": False}, {"reject": False}, {"private": 200}):
            with self.subTest(options=options), self.assertRaises(RuntimeError):
                self._probe(**options)

    def test_https_verification_never_claims_second_client_trust(self):
        run = deployments.create_run("example/model")
        run.update(model={"model_id": "example/model", "revision": "a" * 40}, endpoint={"base_url": "https://api.example.test/v1"})
        deployments._save(run)
        with patch.object(deployments, "probe", return_value={"vantage": "server", "revision": "a" * 40}):
            result = deployments.verify_endpoint(run["run_id"])
        self.assertEqual(result["state"], "awaiting_client_verification")

    def _execute(self, mode="local", *, supported=True, controller=None, ensure=None):
        model = {"model_id": "example/model", "revision": "a" * 40, "supported": supported,
                 "snapshot_path": str(self.home / "snapshot")}
        inventory = MagicMock()
        inventory.resolve_model.return_value = model
        inventory.pull_model.return_value = model
        self.stack.enter_context(patch("notebooks.mxstudio.inventory", inventory, create=True))
        self.stack.enter_context(patch.object(deployments, "_controller_status", return_value=controller))
        prepare = self.stack.enter_context(patch.object(deployments, "_prepare"))
        self.stack.enter_context(patch.object(deployments, "_ensure_controller", side_effect=ensure))
        self.stack.enter_context(patch.object(deployments, "probe", return_value={"revision": "a" * 40}))
        run = deployments.create_run("example/model", mode=mode)
        result = deployments.execute(run["run_id"], job_id="test-worker", progress=MagicMock())
        return result, inventory, prepare

    def test_supported_local_run_is_durable(self):
        result, inventory, prepare = self._execute()
        self.assertEqual(result["state"], "local_ready")
        self.assertEqual(deployments.get_run(result["run_id"])["endpoint"]["revision"], "a" * 40)
        inventory.pull_model.assert_called_once()
        prepare.assert_called_once()

    def test_pull_never_prepares_or_publishes(self):
        result, inventory, prepare = self._execute("pull", supported=False)
        self.assertEqual(result["state"], "downloaded")
        inventory.pull_model.assert_called_once()
        prepare.assert_not_called()

    def test_unsupported_pull_uses_real_resolution_and_download_contract(self):
        for support in ("unsupported", "unknown", "unsupported_encoding"):
            with self.subTest(support=support):
                report = {
                    "ok": True, "model_id": "example/" + support, "revision": "a" * 40,
                    "architecture": None if support == "unknown" else "NovelForCausalLM",
                    "modular_compatible": support == "unsupported_encoding",
                    "has_safetensors": True, "format_status": "supported",
                    "files": [{"name": "config.json"}, {"name": "model.safetensors"}, {"name": "tokenizer.json"}],
                    "encoding_compatibility": {"status": "unsupported" if support == "unsupported_encoding" else "supported"},
                }
                with patch.object(inventory.compat, "check_hf_model", return_value=report), \
                     patch.object(inventory.compat, "max_architectures", return_value={} if support == "unknown" else {"LlamaForCausalLM": {}}), \
                     patch.object(inventory.downloads, "snapshot_for_revision", return_value=None), \
                     patch.object(inventory, "get_hf_token", return_value=""), \
                     patch.object(inventory.downloads, "pull_revision", return_value={"snapshot_path": str(self.home / support), "manifest": [{"name": "model.safetensors", "size": 10}]}) as pull, \
                     patch.object(deployments, "_prepare") as prepare:
                    run = deployments.create_run(report["model_id"], mode="pull")
                    result = deployments.execute(run["run_id"], job_id="job", progress=MagicMock())
                self.assertEqual(result["state"], "downloaded", result)
                self.assertFalse(result["model"]["ok"])
                self.assertFalse(result["model"]["supported"])
                self.assertEqual(result["model"]["support_status"], support)
                self.assertTrue(result["model"]["downloaded"])
                pull.assert_called_once()
                prepare.assert_not_called()

    def test_pull_does_not_bypass_resolution_or_storage_failures(self):
        valid = {"ok": False, "model_id": "example/model", "revision": "a" * 40,
                 "format_status": "supported", "has_safetensors": True, "support_status": "unsupported"}
        for changes in ({"revision": None}, {"model_id": None}, {"revision": "main"},
                        {"format_status": "unsupported"}, {"has_safetensors": False},
                        {"support_status": "lookup_error"}):
            with self.subTest(changes=changes), \
                 patch.object(inventory, "resolve_model", return_value={**valid, **changes}), \
                 patch.object(inventory, "pull_model") as pull:
                run = deployments.create_run("example/model", mode="pull")
                result = deployments.execute(run["run_id"], job_id="job", progress=MagicMock())
                self.assertEqual(result["state"], "blocked")
                pull.assert_not_called()

    def test_resume_retains_revision_across_download_and_resolution_failures(self):
        model = {"ok": True, "model_id": "example/model", "revision": "a" * 40}
        lookup_error = {"ok": False, "model_id": "example/model", "revision": None, "error": "HF unavailable"}
        for failure in (OSError("download failed"), {"ok": False, "error": "download blocked"}):
            with self.subTest(failure=failure), \
                 patch.object(inventory, "resolve_model", side_effect=[model, lookup_error, model]) as resolve, \
                 patch.object(inventory, "pull_model", side_effect=[failure, {**model, "downloaded": True}]) as pull:
                run = deployments.create_run("example/model", revision="main", mode="pull")
                first = deployments.execute(run["run_id"], job_id="job-1", progress=MagicMock())
                self.assertIn(first["state"], {"failed", "blocked"})
                second = deployments.execute(run["run_id"], job_id="job-2", progress=MagicMock())
                self.assertEqual(second["state"], "blocked")
                self.assertEqual(second["error"], "HF unavailable")
                self.assertEqual(second["model"]["revision"], "a" * 40)
                third = deployments.execute(run["run_id"], job_id="job-3", progress=MagicMock())
                self.assertEqual(third["state"], "downloaded")
                self.assertEqual([c.kwargs["revision"] for c in resolve.call_args_list], ["main", "a" * 40, "a" * 40])
                self.assertEqual(pull.call_count, 2)

    def test_resume_rejects_a_changed_resolved_identity(self):
        run = deployments.create_run("example/model", mode="pull")
        run["model"] = {"model_id": "example/model", "revision": "a" * 40}
        deployments._save(run)
        with patch.object(inventory, "resolve_model", return_value={"ok": True, "model_id": "example/model", "revision": "b" * 40}), \
             patch.object(inventory, "pull_model") as pull:
            result = deployments.execute(run["run_id"], job_id="job", progress=MagicMock())
        self.assertEqual(result["state"], "blocked")
        self.assertEqual(result["model"]["revision"], "a" * 40)
        pull.assert_not_called()

    def test_unsupported_up_blocks_before_download(self):
        result, inventory, prepare = self._execute(supported=False)
        self.assertEqual(result["state"], "blocked")
        inventory.pull_model.assert_not_called()
        prepare.assert_not_called()

    def test_existing_controller_configuration_checked_before_max_switch(self):
        result, _, prepare = self._execute(controller={"desired_model": "other/model", "desired_revision": "b" * 40})
        self.assertEqual(result["state"], "needs_setup")
        prepare.assert_not_called()

    def test_lan_run_releases_transient_controller_for_explicit_setup(self):
        def ensure(run, key):
            deployments._runtime("controller", {"run_id": run["run_id"], "pid": 999999})
        with patch.object(deployments, "_stop_controller") as stop:
            result, _, _ = self._execute("lan", ensure=ensure)
        self.assertEqual(result["state"], "needs_setup")
        stop.assert_called_once()
        self.assertIn("--apply", result["setup"]["install_command"])
        self.assertIn("--service-user-current", result["setup"]["install_command"])
        self.assertIn("--dry-run", result["setup"]["dry_run_command"])
        self.assertIn(result["run_id"], result["setup"]["installed_environment_file"])

    def test_lan_with_matching_service_waits_for_independent_client(self):
        result, _, _ = self._execute("lan", controller={"desired_model": "example/model", "desired_revision": "a" * 40})
        self.assertEqual(result["state"], "awaiting_client_verification")
        self.assertNotIn("client_evidence", result["endpoint"])

    def test_ambiguous_model_has_actionable_candidates_without_download(self):
        inventory = MagicMock()
        inventory.resolve_model.return_value = {"ok": False, "state": "ambiguous", "candidates": [{"model_id": "example/a"}, {"model_id": "example/b"}]}
        run = deployments.create_run("example", mode="pull")
        with patch("notebooks.mxstudio.inventory", inventory, create=True):
            result = deployments.execute(run["run_id"], job_id="job", progress=MagicMock())
        self.assertEqual(result["state"], "needs_input")
        self.assertEqual(len(result["model"]["candidates"]), 2)
        inventory.pull_model.assert_not_called()

    def test_structured_pull_failure_is_not_downloaded(self):
        inventory = MagicMock()
        inventory.resolve_model.return_value = {"model_id": "example/a", "revision": "a" * 40}
        inventory.pull_model.return_value = {"ok": False, "error": "Unsupported format"}
        run = deployments.create_run("example/a", mode="pull")
        with patch("notebooks.mxstudio.inventory", inventory, create=True):
            result = deployments.execute(run["run_id"], job_id="job", progress=MagicMock())
        self.assertEqual(result["state"], "blocked")
        self.assertEqual(result["error"], "Unsupported format")

    def test_competing_worker_does_not_overwrite_live_run(self):
        run = deployments.create_run("example/a", mode="pull")
        run.update(state="running", job_id="live-job")
        deployments._save(run)
        with deployments._exclusive(), self.assertRaisesRegex(RuntimeError, "busy"):
            deployments.execute(run["run_id"], job_id="other-job", progress=MagicMock())
        current = deployments.get_run(run["run_id"])
        self.assertEqual(current["state"], "running")
        self.assertEqual(current["job_id"], "live-job")

    def test_real_server_lifecycle_records_and_stops_owned_process(self):
        for preparation_fails in (False, True):
            with self.subTest(preparation_fails=preparation_fails):
                manager = servers.ServerManager()
                run = deployments.create_run("example/a", mode="local")
                run["model"] = {"model_id": "example/a", "revision": "a" * 40}
                entry = registry.ModelEntry(id="example/a", name="A", params="1B", approx_gb=1,
                                            context=1024, license="test", description="test", innovation="",
                                            revision="a" * 40)
                live = {"value": True}
                def kill_group(*args):
                    live["value"] = False
                with patch.object(deployments, "SERVERS", manager), \
                     patch.object(inventory, "entry_for", return_value=entry), \
                     patch.object(servers.subprocess, "Popen", return_value=MagicMock(pid=999999)), \
                     patch.object(servers, "_process_identity", return_value="test-only"), \
                     patch.object(servers, "_alive", side_effect=lambda pid: bool(pid) and live["value"]), \
                     patch.object(servers.os, "getpgid", return_value=999999), \
                     patch.object(servers.os, "killpg", side_effect=kill_group) as kill, \
                     patch.object(manager, "_healthy", side_effect=[False, RuntimeError("probe failed")] if preparation_fails else [False, True, True]), \
                     patch.object(manager, "model_matches", return_value=True), \
                     patch.object(deployments, "_controller_status", return_value=None):
                    if preparation_fails:
                        with self.assertRaisesRegex(RuntimeError, "probe failed"):
                            deployments._prepare(run)
                    else:
                        self.assertEqual(deployments._prepare(run)["state"], "running")
                    persisted = deployments.get_run(run["run_id"])
                    self.assertEqual(persisted["max_owner"], {"pid": 999999, "process_identity": "test-only",
                                                           "model_id": "example/a", "revision": "a" * 40})
                    persisted.update(state="failed" if preparation_fails else "local_ready")
                    deployments._save(persisted)
                    with patch.object(manager, "_healthy", return_value=False):
                        self.assertEqual(deployments.stop_endpoint(run["run_id"])["state"], "stopped")
                    kill.assert_called_once()
                    self.assertEqual(manager.status()["state"], "stopped")

    def test_stop_owned_process_that_has_already_exited(self):
        run = deployments.create_run("example/a", mode="local")
        run.update(state="failed", max_owner={"pid": 999999, "process_identity": "test-only",
                                             "model_id": "example/a", "revision": "a" * 40})
        deployments._save(run)
        deployments._runtime("active_run", {"run_id": run["run_id"]})
        state.update(server=run["max_owner"])
        with patch.object(deployments, "SERVERS", servers.ServerManager()), \
             patch.object(servers, "_alive", return_value=False), \
             patch.object(servers.os, "killpg") as kill, \
             patch.object(deployments, "_controller_status", return_value=None):
            self.assertEqual(deployments.stop_endpoint(run["run_id"])["state"], "stopped")
        kill.assert_not_called()
        self.assertNotIn("server", state.load())

    def test_unmanaged_status_does_not_expose_owned_identity(self):
        state.update(server={"pid": 999999, "process_identity": "stale", "model_id": "example/a"})
        with patch.object(servers, "_alive", return_value=True), \
             patch.object(servers, "_process_identity", return_value="replacement"):
            status = servers.ServerManager().status()
        self.assertEqual(status["state"], "unmanaged")
        self.assertNotIn("pid", status)
        self.assertNotIn("process_identity", status)

    def test_preparation_records_new_process_even_if_max_crashes(self):
        run = deployments.create_run("example/a", mode="local")
        run["model"] = {"model_id": "example/a", "revision": "a" * 40}
        inventory = MagicMock()
        inventory.entry_for.return_value = MagicMock(id="example/a")
        owned = {"pid": 999999, "process_identity": "test-only", "model_id": "example/a", "revision": "a" * 40}
        with patch("notebooks.mxstudio.inventory", inventory, create=True), \
             patch.object(deployments.SERVERS, "status", side_effect=[{"state": "stopped"}, dict(owned, state="crashed")]), \
             patch.object(deployments.SERVERS, "start", return_value=dict(owned, state="starting")):
            with self.assertRaisesRegex(RuntimeError, "MAX exited"):
                deployments._prepare(run)
        self.assertEqual(deployments.get_run(run["run_id"])["max_owner"], owned)
        self.assertEqual(deployments._runtime("active_run")["run_id"], run["run_id"])

    def test_failed_run_owned_resources_can_be_stopped_without_verified_endpoint(self):
        run = deployments.create_run("example/a", mode="local")
        owned = {"pid": 999999, "process_identity": "test-only", "model_id": "example/a", "revision": "a" * 40}
        run.update(state="failed", max_owner=owned)
        deployments._save(run)
        deployments._runtime("active_run", {"run_id": run["run_id"]})
        with patch.object(deployments, "_controller_status", return_value=None), \
             patch.object(deployments.SERVERS, "status", return_value=owned), \
             patch.object(deployments.SERVERS, "stop") as stop:
            result = deployments.stop_endpoint(run["run_id"])
        self.assertEqual(result["state"], "stopped")
        stop.assert_called_once()

    def test_interrupted_worker_can_be_inspected_and_resumed(self):
        run = deployments.create_run("example/model")
        job = jobs_db.create(kind="deployment", label="test")
        run.update(state="running", job_id=job["job_id"])
        deployments._save(run)
        jobs_db.update(job["job_id"], state="failed")
        self.assertEqual(deployments.get_run(run["run_id"])["state"], "interrupted")
        with patch.object(jobs, "enqueue_process", return_value={"job_id": "next-worker"}):
            self.assertEqual(deployments.resume(run["run_id"])["job_id"], "next-worker")

    def test_cli_wait_exits_when_verification_worker_dies(self):
        from notebooks.mxstudio import cli
        from typer.testing import CliRunner

        run = deployments.create_run("example/model")
        job = jobs_db.create(kind="deployment", label="test")
        run.update(state="verifying", job_id=job["job_id"])
        deployments._save(run)
        jobs_db.update(job["job_id"], state="failed")
        with patch.object(deployments, "resume", return_value=run), \
             patch.object(cli.time, "sleep") as sleep:
            result = CliRunner().invoke(cli.app, ["runs", "resume", run["run_id"]])
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("interrupted", result.output)
        sleep.assert_called_once()
        self.assertEqual(deployments.get_run(run["run_id"])["state"], "interrupted")

    def test_failed_heartbeat_does_not_interrupt_locked_worker(self):
        for active_state in ("running", "verifying"):
            with self.subTest(state=active_state):
                run = deployments.create_run("example/model")
                job = jobs_db.create(kind="deployment", label="test")
                run.update(state=active_state, job_id=job["job_id"])
                deployments._save(run)
                jobs_db.update(job["job_id"], state="failed")
                with deployments._exclusive():
                    self.assertEqual(deployments.get_run(run["run_id"])["state"], active_state)

    def _client_run(self):
        import time
        now = time.time()
        endpoint = {"base_url": "https://api.example.test/v1", "model_id": "example/model", "revision": "a" * 40,
                    "https_evidence": {"verified_at": now - 5, "machine_fingerprint": "1" * 64}}
        run = deployments.create_run("example/model", hostname="api.example.test")
        run.update(state="awaiting_client_verification", endpoint=endpoint)
        deployments._save(run)
        proof = {"schema_version": 1, "base_url": endpoint["base_url"], "model_id": endpoint["model_id"],
                 "revision": endpoint["revision"], "vantage": "client", "machine_fingerprint": "2" * 64,
                 "verified_at": now, "elapsed_s": 2, "content_bytes": 5, "stream_complete": True,
                 "authentication_verified": True, "tls_verified": True, "private_routes_verified": True}
        return run, proof

    def test_client_evidence_marks_ready_and_discards_unknown_fields(self):
        run, proof = self._client_run()
        proof["api_key"] = "must-not-persist"
        result = deployments.record_client_evidence(run["run_id"], proof)
        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["endpoint"]["client_evidence"]["provenance"], "trusted_operator_report")
        self.assertNotIn("must-not-persist", json.dumps(deployments.get_run(run["run_id"])))

    def test_invalid_client_evidence_never_marks_ready(self):
        run, proof = self._client_run()
        for change in ({"revision": "b" * 40}, {"vantage": "server"}, {"machine_fingerprint": "1" * 64},
                       {"tls_verified": 1}, {"content_bytes": True}, {"verified_at": 0},
                       {"elapsed_s": float("nan")}, {"schema_version": True}, {"extra": "x" * 65536}):
            with self.subTest(change=list(change)), self.assertRaises(ValueError):
                deployments.record_client_evidence(run["run_id"], dict(proof, **change))
        self.assertEqual(deployments.get_run(run["run_id"])["state"], "awaiting_client_verification")

    def test_failed_reverification_invalidates_ready_evidence(self):
        run, proof = self._client_run()
        deployments.record_client_evidence(run["run_id"], proof)
        with patch.object(deployments, "probe", side_effect=RuntimeError("secret-value")):
            result = deployments.verify_endpoint(run["run_id"])
        self.assertEqual(result["state"], "failed")
        self.assertNotIn("client_evidence", result["endpoint"])
        self.assertNotIn("https_evidence", result["endpoint"])
        self.assertNotIn("secret-value", json.dumps(result))

    def test_endpoint_jobs_delegate_and_are_not_legacy_dispatchable(self):
        for kind, target in (("endpoint_verify", "verify_endpoint"), ("endpoint_stop", "stop_endpoint")):
            with self.subTest(kind=kind), patch.object(deployments, target, return_value={"state": "done"}) as operation:
                self.assertEqual(jobs.HANDLERS[kind]("run-id", job_id="job-id", progress=MagicMock()), {"state": "done"})
                operation.assert_called_once()
                with self.assertRaises(ValueError):
                    jobs.start_kind(kind, run_id="run-id")

    def test_stream_limits_and_fragmented_events(self):
        import time
        stream = MagicMock()
        stream.iter_bytes.return_value = [b"data: {", b"}\r\n\n"]
        self.assertEqual(list(deployments._stream_lines(stream, time.monotonic())), ["data: {}", ""])
        stream.iter_bytes.return_value = [b"x" * 65537]
        with self.assertRaisesRegex(RuntimeError, "size limit"):
            list(deployments._stream_lines(stream, time.monotonic()))

    def test_http_cannot_count_as_lan_verification(self):
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            deployments.probe(deployments.LOCAL_URL, "example/model", "test-only-key", require_https=True)


if __name__ == "__main__":
    unittest.main()
