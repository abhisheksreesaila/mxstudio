from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi.testclient import TestClient

from notebooks.mxstudio import api, nucleus, servers, state
from notebooks.mxstudio.registry import ModelEntry


def entry(model):
    return ModelEntry(id=model, name=model, params="?", approx_gb=0, context=0,
                      license="?", description="test", innovation="")


class IsolatedState:
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        home = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.home = home
        self.stack.enter_context(patch.object(state, "HOME", home))
        self.stack.enter_context(patch.object(state, "LOGS", home / "logs"))
        self.stack.enter_context(patch.object(state, "STATE_FILE", home / "state.json"))
        self.stack.enter_context(patch.dict(os.environ, {
            "MXSTUDIO_NUCLEUS_API_KEY": "test-only-key", "NUCLEUS_MODEL_ID": "example/a",
            "MXSTUDIO_HOME": str(home), "NUCLEUS_DISCOVERY_HOST": "", "NUCLEUS_PREWARM": "0",
        }))
        self.manager = nucleus.NucleusManager()
        self.stack.enter_context(patch.object(nucleus, "MANAGER", self.manager))
        self.observed = {"state": "running", "model_id": "example/a", "host": servers.MODEL_HOST}
        self.stack.enter_context(patch.object(servers.MANAGER, "status", side_effect=lambda: dict(self.observed)))
        self.matches = self.stack.enter_context(patch.object(servers.MANAGER, "model_matches", return_value=True))
        self.resolve_entry = self.stack.enter_context(patch.object(self.manager, "_entry", side_effect=entry))


class NucleusConcurrencyTests(IsolatedState, unittest.IsolatedAsyncioTestCase):
    async def test_leases_prevent_prepare_and_legacy_mutations(self):
        with patch.object(servers.MANAGER, "_start_unlocked") as start, patch.object(servers.MANAGER, "_stop_unlocked") as stop:
            async with self.manager.lease("example/a"):
                self.assertEqual(self.manager.status()["active_leases"], 1)
                async with self.manager.lease("example/a"):
                    self.assertEqual(self.manager.status()["active_leases"], 2)
                with self.assertRaisesRegex(RuntimeError, "busy"):
                    await self.manager.prepare("example/b")
                with self.assertRaisesRegex(RuntimeError, "busy"):
                    servers.MANAGER.start(entry("example/b"))
                with self.assertRaisesRegex(RuntimeError, "busy"):
                    servers.MANAGER.stop()
                start.assert_not_called()
                stop.assert_not_called()
            servers.MANAGER.start(entry("example/b"))
            start.assert_called_once()
            self.assertEqual(self.manager.status()["active_leases"], 0)

    async def test_single_flight_same_model(self):
        self.observed["state"] = "stopped"
        entered, release = threading.Event(), threading.Event()

        def start(model):
            entered.set()
            if not release.wait(3):
                raise AssertionError("test release timed out")
            self.observed.update(state="running", model_id=model.id)
            return dict(self.observed)

        with patch.object(servers.MANAGER, "_start_unlocked", side_effect=start) as launch:
            first = asyncio.create_task(self.manager.prepare("example/a", 5))
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            second = asyncio.create_task(self.manager.prepare("example/a", 5))
            await asyncio.sleep(0.05)
            release.set()
            results = await asyncio.gather(first, second)
            self.assertEqual(launch.call_count, 1)
            self.assertTrue(all(result["model_id"] == "example/a" for result in results))

    async def test_cancelled_lease_releases_cross_process_reservation(self):
        entered = asyncio.Event()

        async def request():
            async with self.manager.lease("example/a"):
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(request())
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.manager.status()["active_leases"], 0)
        with servers.MANAGER.reservation():
            pass

    async def test_cli_process_cannot_stop_an_active_lease(self):
        code = """
from notebooks.mxstudio.servers import MANAGER
try:
    MANAGER.stop()
except RuntimeError:
    raise SystemExit(7)
raise SystemExit(0)
"""
        async with self.manager.lease("example/a"):
            result = await asyncio.to_thread(subprocess.run, [sys.executable, "-c", code],
                                             capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 7, result.stderr.decode())

    async def test_admission_rechecks_model_after_prepare(self):
        async def prepared(*args):
            self.observed["model_id"] = "example/b"
            return {}
        with patch.object(self.manager, "prepare", side_effect=prepared):
            with self.assertRaisesRegex(RuntimeError, "changed"):
                async with self.manager.lease("example/a"):
                    self.fail("wrong model admitted")
        with servers.MANAGER.reservation():
            pass

    async def test_wrong_upstream_identity_times_out(self):
        self.matches.return_value = False
        with patch.object(servers.MANAGER, "_start_unlocked"):
            with self.assertRaises(TimeoutError):
                await self.manager.prepare("example/a", 0.1)
        self.assertFalse(self.manager.status()["transition"])
        with servers.MANAGER.reservation():
            pass

    async def test_unsafe_host_is_not_reused(self):
        self.observed["host"] = "0.0.0.0"
        def start(model):
            self.observed["host"] = servers.MODEL_HOST
        with patch.object(servers.MANAGER, "_start_unlocked", side_effect=start) as launch:
            await self.manager.prepare("example/a")
            launch.assert_called_once()

    async def test_cancelled_preparation_retains_lock_until_worker_finishes(self):
        self.observed["state"] = "stopped"
        entered, release = threading.Event(), threading.Event()
        def start(model):
            entered.set()
            release.wait(3)
            self.observed["state"] = "running"
        with patch.object(servers.MANAGER, "_start_unlocked", side_effect=start):
            task = asyncio.create_task(self.manager.prepare("example/a", 5))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertEqual(self.manager.status()["pending_preparations"], 1)
                with self.assertRaisesRegex(RuntimeError, "busy"):
                    with servers.ServerManager().reservation():
                        pass
            finally:
                release.set()
            for _ in range(100):
                if self.manager.status()["pending_preparations"] == 0:
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(self.manager.status()["pending_preparations"], 0)
        with servers.MANAGER.reservation():
            pass

    async def test_pending_preparation_bound(self):
        self.manager._pending = 32
        with patch.object(self.manager, "_prepare_sync") as prepare:
            with self.assertRaisesRegex(RuntimeError, "queue is full"):
                await self.manager.prepare("example/a")
            prepare.assert_not_called()

    async def test_response_cancelled_before_first_chunk_releases_resources(self):
        request = MagicMock()
        request.headers = {"authorization": "Bearer test-only-key"}
        request.json = AsyncMock(return_value={"model": "example/a", "messages": [{"role": "user", "content": "hi"}], "stream": True})
        upstream = MagicMock(status_code=200)
        upstream.aclose = AsyncMock()
        client = MagicMock(aclose=AsyncMock())
        with patch.object(nucleus, "proxy_openai", new=AsyncMock(return_value=(upstream, client))):
            response = await api.nucleus_chat(request)
            self.assertEqual(self.manager.status()["active_leases"], 1)
            with self.assertRaises(asyncio.CancelledError):
                await response({"type": "http", "asgi": {"spec_version": "2.4"}}, AsyncMock(),
                               AsyncMock(side_effect=asyncio.CancelledError()))
        upstream.aclose.assert_awaited_once()
        client.aclose.assert_awaited_once()
        self.assertEqual(self.manager.status()["active_leases"], 0)
        with servers.MANAGER.reservation():
            pass

    async def test_legacy_browser_stream_blocks_swaps_and_closes_client(self):
        client = MagicMock(close=AsyncMock())
        async def events(client):
            with self.assertRaisesRegex(RuntimeError, "busy"):
                servers.MANAGER.stop()
            yield "data: test\\n\\n"
        with patch.object(api, "AsyncOpenAI", return_value=client):
            response = await api._studio_stream("example/a", events)
            await response({"type": "http", "asgi": {"spec_version": "2.4"}}, AsyncMock(), AsyncMock())
        client.close.assert_awaited_once()
        with servers.MANAGER.reservation():
            pass

    async def test_transition_is_exclusive_across_manager_instances(self):
        with servers.ServerManager().transition():
            with self.assertRaisesRegex(RuntimeError, "busy"):
                await self.manager.prepare("example/a", 0.1)


class NucleusHTTPTests(IsolatedState, unittest.TestCase):
    headers = {"Authorization": "Bearer test-only-key"}
    body = {"model": "nucleus-local", "messages": [{"role": "user", "content": "test"}]}

    def test_auth_and_model_listing_have_no_start_side_effect(self):
        with TestClient(api.app) as client, patch.object(self.manager, "prepare") as prepare:
            for headers in ({}, {"Authorization": "Bearer invalid"}):
                response = client.get("/v1/models", headers=headers)
                self.assertEqual(response.status_code, 401)
                self.assertIn("error", response.json())
            response = client.get("/v1/models", headers=self.headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["data"][0]["id"], "example/a")
            prepare.assert_not_called()

    def test_bad_json_and_fields(self):
        invalid = ["{", "[]", json.dumps({**self.body, "model": []}),
                   json.dumps({**self.body, "messages": []}),
                   json.dumps({**self.body, "stream": "false"})]
        with TestClient(api.app) as client, patch.object(self.manager, "prepare") as prepare:
            for content in invalid:
                response = client.post("/v1/chat/completions", content=content, headers=self.headers)
                self.assertEqual(response.status_code, 400, response.text)
                self.assertIn("error", response.json())
            prepare.assert_not_called()

    def proxy_client(self, handler):
        constructor = httpx.AsyncClient
        return patch.object(nucleus.httpx, "AsyncClient", side_effect=lambda **kwargs:
                            constructor(**kwargs, transport=httpx.MockTransport(handler)))

    def test_nonstream_and_alias_forwarding(self):
        captured = []
        def handler(request):
            captured.append(json.loads(request.content))
            return httpx.Response(200, json={"object": "chat.completion", "choices": []})
        with self.proxy_client(handler), TestClient(api.app) as client:
            response = client.post("/v1/chat/completions", json=self.body, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["object"], "chat.completion")
        self.assertEqual(captured[0]["model"], "example/a")
        self.assertEqual(self.manager.status()["active_leases"], 0)

    def test_stream_is_passed_unchanged_and_error_preserves_http_status(self):
        chunks = b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\ndata: [DONE]\n\n'
        for status, content, content_type in [(200, chunks, "text/event-stream"),
                (400, b'{"error":{"message":"invalid model"}}', "application/json")]:
            with self.proxy_client(lambda request: httpx.Response(status, content=content,
                    headers={"content-type": content_type, "retry-after": "7"})), TestClient(api.app) as client:
                response = client.post("/v1/chat/completions", json={**self.body, "stream": True}, headers=self.headers)
            self.assertEqual(response.status_code, status)
            self.assertEqual(response.content, content)
            self.assertIn(content_type, response.headers["content-type"])
            if status == 400:
                self.assertEqual(response.headers["retry-after"], "7")
            self.assertEqual(self.manager.status()["active_leases"], 0)

    def test_legacy_stop_returns_busy_while_inference_reserved(self):
        with servers.MANAGER.reservation(shared=True), TestClient(api.app) as client:
            response = client.post("/api/server/stop")
        self.assertEqual(response.status_code, 409)

    def test_prepare_uses_durable_jobs_and_private_job_status(self):
        job = {"job_id": "job_test", "kind": "nucleus_prepare", "state": "processing"}
        with patch.object(api.jobs, "enqueue_process", return_value=job) as enqueue, TestClient(api.app) as client:
            response = client.post("/nucleus/prepare", json={"model": "example/a"}, headers=self.headers)
            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.json()["job_id"], "job_test")
            self.assertEqual(enqueue.call_args.args[0], "nucleus_prepare")
            with patch.object(api.jobs, "get", return_value=job):
                self.assertEqual(client.get("/nucleus/jobs/job_test", headers=self.headers).status_code, 200)
                self.assertEqual(client.get("/nucleus/jobs/job_test").status_code, 401)
            with patch.object(api.jobs, "get", return_value={"kind": "download"}):
                self.assertEqual(client.get("/nucleus/jobs/other", headers=self.headers).status_code, 404)


class NucleusCLITests(IsolatedState, unittest.TestCase):
    def test_render_config_reports_exact_ingress_and_preserves_existing_files(self):
        from typer.testing import CliRunner
        from notebooks.mxstudio.cli import app
        runner = CliRunner()
        result = runner.invoke(app, ["nucleus", "render-config"])
        self.assertEqual(result.exit_code, 0, result.output)
        payload = json.loads(result.output)
        self.assertEqual(payload["public_ingress"], "Caddy only; GET /v1/models and POST /v1/chat/completions")
        output = self.home / "config.json"
        result = runner.invoke(app, ["nucleus", "render-config", "--output", str(output)])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(json.loads(output.read_text()), payload)
        output.write_text("hand edited")
        result = runner.invoke(app, ["nucleus", "render-config", "--output", str(output)])
        self.assertNotEqual(result.exit_code, 0)
        self.assertEqual(output.read_text(), "hand edited")

    def test_run_is_loopback_single_worker_and_requires_key(self):
        from typer.testing import CliRunner
        from notebooks.mxstudio.cli import app
        runner = CliRunner()
        with patch("uvicorn.run") as run:
            result = runner.invoke(app, ["nucleus", "run"])
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertEqual(run.call_args.kwargs["host"], "127.0.0.1")
            self.assertEqual(run.call_args.kwargs["workers"], 1)
            self.assertEqual(os.environ["NUCLEUS_PREWARM"], "1")
            run.reset_mock()
            for options in (["--host", "0.0.0.0"], ["--workers", "2"]):
                self.assertNotEqual(runner.invoke(app, ["nucleus", "run", *options]).exit_code, 0)
            with patch.dict(os.environ, {"MXSTUDIO_NUCLEUS_API_KEY": ""}):
                self.assertNotEqual(runner.invoke(app, ["nucleus", "run"]).exit_code, 0)
            run.assert_not_called()

    def test_prepare_alias_and_lesson(self):
        from typer.testing import CliRunner
        from notebooks.mxstudio.cli import app
        runner = CliRunner()
        with patch.object(self.manager, "prepare", new=AsyncMock(return_value={"state": "ready"})) as prepare:
            result = runner.invoke(app, ["nucleus", "prepare", "--model", "example/b"])
            self.assertEqual(result.exit_code, 0, result.output)
            prepare.assert_awaited_once_with("example/b", timeout=600.0)
        output = self.home / "timing.ipynb"
        result = runner.invoke(app, ["nucleus", "lesson", "--output", str(output)])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(json.loads(output.read_text())["nbformat"], 4)

    def test_lifespan_queues_prewarm_without_blocking_startup(self):
        with patch.dict(os.environ, {"NUCLEUS_PREWARM": "1"}), \
             patch.object(api.jobs, "enqueue_process", autospec=True, return_value={"job_id": "prewarm"}) as enqueue:
            with TestClient(api.app):
                self.assertEqual(api.app.state.nucleus_prewarm_job_id, "prewarm")
            self.assertEqual(enqueue.call_args.args[0], "nucleus_prepare")


class ProxyCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_failures_and_cancellation_close_client(self):
        for failure in (httpx.ConnectError("test"), asyncio.CancelledError()):
            client = MagicMock()
            client.send = AsyncMock(side_effect=failure)
            client.aclose = AsyncMock()
            with patch.object(nucleus.httpx, "AsyncClient", return_value=client):
                with self.assertRaises(type(failure)):
                    await nucleus.proxy_openai("/v1/chat/completions", {"stream": True})
            client.aclose.assert_awaited_once()


class BenchmarkReservationTests(IsolatedState, unittest.TestCase):
    def test_benchmark_holds_model_reservation_through_execution(self):
        from notebooks.mxstudio import bench
        def fallback(*args):
            with self.assertRaisesRegex(RuntimeError, "busy"):
                servers.MANAGER.stop()
            return {"runs": [], "summary": {}}
        def thread(*, target, **kwargs):
            return MagicMock(start=target)
        with patch.object(bench.threading, "Thread", side_effect=thread), \
             patch.object(bench, "_fallback_stream_runs", side_effect=fallback), \
             patch.object(bench.hwinfo, "gpus", return_value=[]):
            result = bench.BenchManager().start("example/a", use_max_benchmark=False)
        self.assertEqual(result["state"], "done", result)
        with servers.MANAGER.reservation():
            pass

    def test_benchmark_rejects_changed_model_without_executing(self):
        from notebooks.mxstudio import bench
        def thread(*, target, **kwargs):
            return MagicMock(start=target)
        with patch.object(bench.threading, "Thread", side_effect=thread), \
             patch.object(bench, "_fallback_stream_runs") as fallback:
            result = bench.BenchManager().start("example/b", use_max_benchmark=False)
        self.assertEqual(result["state"], "error")
        fallback.assert_not_called()


class ServerLaunchTests(IsolatedState, unittest.TestCase):
    def test_binding_uses_supported_environment_not_invalid_host_flag(self):
        manager = servers.ServerManager()
        process = MagicMock(pid=999999)
        with patch.object(state, "load", return_value={}), patch.object(state, "update") as update, \
             patch.object(manager, "status", return_value={"state": "starting"}), \
             patch.object(servers, "_process_identity", return_value="test-birth"), \
             patch.object(servers.subprocess, "Popen", return_value=process) as launch:
            manager.start(entry("example/a"))
        self.assertEqual(launch.call_args.kwargs["env"]["MAX_SERVE_HOST"], "127.0.0.1")
        self.assertNotIn("--host", launch.call_args.args[0])
        self.assertEqual(update.call_args.kwargs["server"]["process_identity"], "test-birth")
        self.assertTrue(launch.call_args.kwargs["stdout"].closed)

    def test_stale_process_identity_never_signalled(self):
        manager = servers.ServerManager()
        with patch.object(state, "load", return_value={"server": {"pid": 123}}), \
             patch.object(servers, "_alive", return_value=True), \
             patch.object(servers.os, "killpg") as signal:
            with self.assertRaisesRegex(RuntimeError, "ownership"):
                manager.stop()
            signal.assert_not_called()


class ModelResolutionTests(unittest.TestCase):
    def test_custom_model_keeps_architecture(self):
        with patch.object(nucleus.downloads, "is_installed", return_value=True), \
             patch.object(nucleus.registry, "get", return_value=None), \
             patch.object(nucleus.architectures, "custom_arch_relative", return_value="ports/custom"), \
             patch.dict(os.environ, {"NUCLEUS_MODEL_ID": "example/custom"}):
            result = nucleus.NucleusManager()._entry("nucleus-local")
        self.assertEqual(result.id, "example/custom")
        self.assertEqual(result.custom_arch, "ports/custom")

    def test_unknown_model_never_downloads(self):
        with patch.object(nucleus.registry, "get", return_value=None), \
             patch.object(nucleus.downloads, "is_installed", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "not downloaded"):
                nucleus.NucleusManager()._entry("example/missing")


if __name__ == "__main__":
    unittest.main()
