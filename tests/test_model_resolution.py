from __future__ import annotations

import shutil
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

from notebooks.mxstudio import inventory, state
from notebooks.mxstudio.registry import ModelEntry
from notebooks.mxstudio.servers import ServerManager


class ModelResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path.cwd() / f".test-model-inventory-{uuid4().hex}"
        self.home.mkdir()
        self._home_patch = patch.object(state, "HOME", self.home)
        self._home_patch.start()
        self._db_path_patch = patch(
            "notebooks.mxstudio.inventory.jobs_db.DB_PATH", self.home / "jobs.db"
        )
        self._db_path_patch.start()

    def tearDown(self) -> None:
        self._db_path_patch.stop()
        self._home_patch.stop()
        shutil.rmtree(self.home, ignore_errors=True)

    @staticmethod
    def report(
        model_id: str = "Qwen/Qwen2.5-0.5B-Instruct",
        *,
        architecture: str = "Qwen2ForCausalLM",
        compatible: bool = True,
    ) -> dict:
        return {
            "ok": True,
            "model_id": model_id,
            "revision": "a" * 40,
            "architecture": architecture,
            "max_list_available": True,
            "modular_compatible": compatible,
            "name": model_id.rsplit("/", 1)[-1],
            "license": "Apache-2.0",
            "gated": False,
            "has_safetensors": True,
            "files": [
                {"name": "config.json", "size": 10},
                {"name": "model.safetensors.index.json", "size": 100},
                {"name": "model-00001.safetensors", "size": 1000},
                {"name": "tokenizer.json", "size": 100},
            ],
            "approx_gb": 1.0,
            "hardware": {"level": "unknown"},
            "warnings": [],
        }

    def test_resolution_pins_revision_and_persists_curated_shortcut(self) -> None:
        with (
            patch(
                "notebooks.mxstudio.inventory.compat.check_hf_model",
                return_value=self.report(),
            ),
            patch(
                "notebooks.mxstudio.inventory.compat.max_architectures",
                return_value={"Qwen2": {}},
            ),
            patch(
                "notebooks.mxstudio.inventory.downloads.snapshot_for_revision",
                return_value=None,
            ),
        ):
            record = inventory.resolve_model("qwen2.5-0.5b-instruct")

        self.assertTrue(record["ok"])
        self.assertEqual(record["model_id"], "Qwen/Qwen2.5-0.5B-Instruct")
        self.assertEqual(record["revision"], "a" * 40)
        self.assertEqual(record["support_status"], "supported")
        self.assertEqual(inventory.get_model(record["model_id"]), record)
        self.assertEqual(inventory.list_models(), [record])

    def test_ambiguous_shortcut_is_explicit(self) -> None:
        record = inventory.resolve_model("qwen")

        self.assertEqual(record["state"], "ambiguous")
        self.assertGreater(len(record["candidates"]), 1)

    def test_catalog_reference_resolves_unique_actual_repo_with_provenance(self) -> None:
        normalized = {
            "available": True,
            "source": "max list --json",
            "entries": [
                {
                    "architecture": "Qwen2",
                    "examples": ["example/model"],
                    "provenance": "max list --json",
                }
            ],
        }
        with (
            patch("notebooks.mxstudio.inventory.catalog", return_value=normalized),
            patch(
                "notebooks.mxstudio.inventory.compat.check_hf_model",
                return_value=self.report("example/model"),
            ),
            patch(
                "notebooks.mxstudio.inventory.compat.max_architectures",
                return_value={"Qwen2": {"encodings": ["bf16"]}},
            ),
            patch(
                "notebooks.mxstudio.inventory.downloads.snapshot_for_revision",
                return_value=None,
            ),
        ):
            record = inventory.resolve_model("catalog:Qwen2")

        self.assertTrue(record["ok"])
        self.assertEqual(record["model_id"], "example/model")
        self.assertEqual(record["source"], "catalog")
        self.assertEqual(record["catalog_architecture"], "Qwen2")
        self.assertEqual(record["provenance"]["source"], "max list --json")

    def test_catalog_reference_reports_ambiguity_and_unavailable_catalog(self) -> None:
        available = {
            "available": True,
            "entries": [
                {"architecture": "Qwen2", "examples": ["example/a", "example/b"]}
            ],
        }
        with patch("notebooks.mxstudio.inventory.catalog", return_value=available):
            ambiguous = inventory.resolve_model("catalog:Qwen2")
        self.assertEqual(ambiguous["state"], "ambiguous")
        self.assertEqual(
            {item["model_id"] for item in ambiguous["candidates"]},
            {"example/a", "example/b"},
        )

        with patch(
            "notebooks.mxstudio.inventory.catalog",
            return_value={"available": False, "entries": []},
        ):
            unavailable = inventory.resolve_model("catalog:Qwen2")
        self.assertFalse(unavailable["ok"])
        self.assertEqual(unavailable["state"], "blocked")
        self.assertIn("catalog is unavailable", unavailable["error"])

    def test_catalog_attaches_only_real_hf_repo_examples(self) -> None:
        with patch(
            "notebooks.mxstudio.inventory.compat.max_catalog",
            return_value={
                "available": True,
                "source": "max list --json",
                "entries": [{"architecture": "Qwen2", "examples": ["actual/org-model"]}],
            },
        ):
            result = inventory.catalog()

        entry = result["entries"][0]
        self.assertIn("actual/org-model", entry["examples"])
        self.assertTrue(
            all(example.count("/") == 1 for example in entry["examples"])
        )
        self.assertGreaterEqual(len(entry["examples"]), 2)

    def test_default_lookup_prefers_last_pulled_revision(self) -> None:
        pulled = {
            "model_id": "example/model",
            "revision": "a" * 40,
            "state": "downloaded",
            "downloaded": True,
            "snapshot_path": "/cache/a",
            "manifest": [{"name": "config.json", "size": 1}],
        }
        newly_resolved = {
            "model_id": "example/model",
            "revision": "b" * 40,
            "state": "resolved",
        }
        inventory._save(pulled)
        inventory._save(newly_resolved)

        self.assertEqual(inventory.get_model("example/model")["revision"], "a" * 40)

        inventory._save({**pulled, "state": "resolved", "manifest": None})
        retained = inventory.get_model("example/model", "a" * 40)
        self.assertEqual(retained["state"], "downloaded")
        self.assertEqual(retained["manifest"], pulled["manifest"])

    def test_exact_cached_sha_resolves_and_pulls_offline(self) -> None:
        repo = self.home / "hf-repo"
        snapshot = repo / "snapshots" / ("a" * 40)
        snapshot.mkdir(parents=True)
        contents = {
            "config.json": "{}",
            "tokenizer.json": "{}",
            "model.safetensors": "weights",
        }
        for name, content in contents.items():
            (snapshot / name).write_text(content)
        manifest = [
            {"name": name, "size": len(content.encode())}
            for name, content in contents.items()
        ]
        cached = {
            "ok": True,
            "state": "downloaded",
            "downloaded": True,
            "model_id": "example/offline",
            "revision": "a" * 40,
            "snapshot_path": str(snapshot),
            "manifest": manifest,
            "files": [{"name": name, "size": len(content.encode())} for name, content in contents.items()],
            "architecture": "LlamaForCausalLM",
            "support_status": "supported",
            "supported": True,
            "blocked": False,
            "advisory": [],
        }
        inventory._save(cached)
        progress = []
        with (
            patch("notebooks.mxstudio.inventory.downloads.repo_dir", return_value=repo),
            patch(
                "notebooks.mxstudio.inventory.compat.check_hf_model",
                side_effect=AssertionError("offline resolve must not call HF"),
            ),
            patch(
                "notebooks.mxstudio.inventory.downloads.pull_revision",
                side_effect=AssertionError("verified cached pull must not fetch"),
            ),
        ):
            resolved = inventory.resolve_model("example/offline", revision="a" * 40)
            pulled = inventory.pull_model(resolved, progress=progress.append)

        self.assertEqual(resolved["revision"], "a" * 40)
        self.assertEqual(resolved["snapshot_path"], str(snapshot.resolve()))
        self.assertEqual(pulled["state"], "downloaded")
        self.assertTrue(progress[-1]["cached"])

        (snapshot / "model.safetensors").unlink()
        refreshed = {
            "snapshot_path": str(snapshot),
            "manifest": manifest,
        }
        with (
            patch("notebooks.mxstudio.inventory.downloads.repo_dir", return_value=repo),
            patch("notebooks.mxstudio.inventory.get_hf_token", return_value=""),
            patch(
                "notebooks.mxstudio.inventory.downloads.pull_revision",
                return_value=refreshed,
            ) as fetch,
        ):
            inventory.pull_model(pulled)
        fetch.assert_called_once()
        self.assertEqual(fetch.call_args.args[1], "a" * 40)

    def test_unsupported_architecture_is_blocked_with_guidance(self) -> None:
        with (
            patch(
                "notebooks.mxstudio.inventory.compat.check_hf_model",
                return_value=self.report(
                    "example/unsupported", architecture="UnknownForCausalLM",
                    compatible=False,
                ),
            ),
            patch(
                "notebooks.mxstudio.inventory.compat.max_architectures",
                return_value={"Llama3": {}},
            ),
            patch(
                "notebooks.mxstudio.inventory.downloads.snapshot_for_revision",
                return_value=None,
            ),
        ):
            record = inventory.resolve_model("example/unsupported")

        self.assertTrue(record["blocked"])
        self.assertEqual(record["support_status"], "unsupported")
        self.assertIn("Custom architectures", record["advisory"][0])
        snapshot = "/models/example-unsupported/" + "a" * 40
        manifest = [
            {"name": "config.json", "size": 10},
            {"name": "model-00001.safetensors", "size": 1000},
            {"name": "model.safetensors.index.json", "size": 100},
            {"name": "tokenizer.json", "size": 100},
        ]
        with (
            patch("notebooks.mxstudio.inventory.get_hf_token", return_value=""),
            patch(
                "notebooks.mxstudio.inventory.downloads.pull_revision",
                return_value={"snapshot_path": snapshot, "manifest": manifest},
            ) as pull_revision,
        ):
            pull = inventory.pull_model(record)
        pull_revision.assert_called_once()
        self.assertEqual(pull["state"], "downloaded")
        self.assertFalse(pull["supported"])
        self.assertTrue(pull["blocked"])
        self.assertEqual(pull["support_status"], "unsupported")

    def test_missing_safetensors_blocks_resolution_before_pull(self) -> None:
        report = self.report(
            "example/gguf-only", architecture="Qwen2ForCausalLM", compatible=True
        )
        report.update(
            has_safetensors=False,
            format_status="unsupported",
            files=[{"name": "model.gguf", "size": 8_000_000_000}],
        )
        with (
            patch(
                "notebooks.mxstudio.inventory.compat.check_hf_model",
                return_value=report,
            ),
            patch(
                "notebooks.mxstudio.inventory.compat.max_architectures",
                return_value={"Qwen2": {"encodings": ["bf16"]}},
            ),
            patch(
                "notebooks.mxstudio.inventory.downloads.snapshot_for_revision",
                return_value=None,
            ),
        ):
            record = inventory.resolve_model("example/gguf-only")

        self.assertFalse(record["ok"])
        self.assertTrue(record["blocked"])
        self.assertEqual(record["format_status"], "unsupported")
        with patch(
            "notebooks.mxstudio.inventory.downloads.pull_revision",
            side_effect=AssertionError("unsupported format must not download weights"),
        ):
            result = inventory.pull_model(record)
        self.assertFalse(result["ok"])
        self.assertIn("safetensors", result["error"])

    def test_unsupported_quantized_encoding_can_pull_without_promotion(self) -> None:
        report = self.report(
            "example/int4", architecture="Qwen2ForCausalLM", compatible=True
        )
        report.update(
            checkpoint_encoding={"encoding": "int4", "source": "quantization_config"},
            encoding_compatibility={
                "status": "unsupported",
                "checkpoint_encoding": "int4",
                "max_encodings": ["bf16"],
                "advisory": "Checkpoint encoding int4 is not listed for Qwen2.",
            },
        )
        with (
            patch(
                "notebooks.mxstudio.inventory.compat.check_hf_model",
                return_value=report,
            ),
            patch(
                "notebooks.mxstudio.inventory.compat.max_architectures",
                return_value={"Qwen2": {"encodings": ["bf16"]}},
            ),
            patch(
                "notebooks.mxstudio.inventory.downloads.snapshot_for_revision",
                return_value=None,
            ),
        ):
            record = inventory.resolve_model("example/int4")

        self.assertFalse(record["ok"])
        self.assertFalse(record["supported"])
        self.assertEqual(record["support_status"], "unsupported_encoding")
        with (
            patch("notebooks.mxstudio.inventory.get_hf_token", return_value=""),
            patch(
                "notebooks.mxstudio.inventory.downloads.pull_revision",
                return_value={
                    "snapshot_path": "/models/example-int4/" + "a" * 40,
                    "manifest": [{"name": "model.safetensors", "size": 1000}],
                },
            ) as pull_revision,
        ):
            result = inventory.pull_model(record)
        pull_revision.assert_called_once()
        self.assertEqual(result["state"], "downloaded")
        self.assertFalse(result["supported"])
        self.assertTrue(result["blocked"])
        self.assertEqual(result["support_status"], "unsupported_encoding")

    def test_pull_persists_verified_pinned_snapshot_without_credentials(self) -> None:
        record = self.report()
        record.update(
            state="resolved",
            source="huggingface",
            support_status="supported",
            blocked=False,
            advisory=[],
        )
        verified = {
            "ok": True,
            "snapshot_path": "/models/snapshots/" + "a" * 40,
            "manifest": [{"name": "config.json", "size": 10}],
        }
        with (
            patch("notebooks.mxstudio.inventory.get_hf_token", return_value="secret-token"),
            patch(
                "notebooks.mxstudio.inventory.downloads.pull_revision",
                return_value=verified,
            ) as pull,
        ):
            result = inventory.pull_model(record)

        self.assertEqual(result["state"], "downloaded")
        self.assertEqual(result["snapshot_path"], verified["snapshot_path"])
        self.assertEqual(pull.call_args.kwargs["token"], "secret-token")
        self.assertNotIn("secret-token", str(inventory.get_model(record["model_id"])))

    def test_entry_for_preserves_canonical_identity_and_load_path(self) -> None:
        repo = self.home / "repo"
        snapshot = repo / "snapshots" / ("a" * 40)
        snapshot.mkdir(parents=True)
        for name in ("config.json", "tokenizer.json", "model.safetensors"):
            (snapshot / name).write_text("{}")
        record = {
            "model_id": "example/model",
            "revision": "a" * 40,
            "snapshot_path": str(snapshot),
            "manifest": [
                {"name": "config.json"},
                {"name": "tokenizer.json"},
                {"name": "model.safetensors"},
            ],
            "support_status": "supported",
            "blocked": False,
            "architecture": "LlamaForCausalLM",
        }

        with patch("notebooks.mxstudio.inventory.downloads.repo_dir", return_value=repo):
            entry = inventory.entry_for(record)

        self.assertEqual(entry.id, "example/model")
        self.assertEqual(entry.revision, "a" * 40)
        self.assertEqual(entry.load_path, str(snapshot.resolve()))
        self.assertIsNone(entry.advertised_model_id)


class PinnedServerIdentityTests(unittest.TestCase):
    def test_model_match_maps_canonical_id_to_current_advertised_id(self) -> None:
        response = Mock()
        response.json.return_value = {"data": [{"id": "model"}]}
        persisted = {}
        with (
            patch(
                "notebooks.mxstudio.servers.state.load",
                return_value={
                    "server": {
                        "model_id": "org/model",
                        "advertised_model_id": "/cache/models--org--model/snapshots/sha",
                    }
                },
            ),
            patch("notebooks.mxstudio.servers.httpx.get", return_value=response),
            patch(
                "notebooks.mxstudio.servers.state.update",
                side_effect=lambda **kwargs: persisted.update(kwargs),
            ),
        ):
            self.assertTrue(ServerManager().model_matches("org/model"))
        self.assertEqual(persisted["server"]["advertised_model_id"], "model")

    def test_status_discovers_and_reports_snapshot_advertised_id(self) -> None:
        response = Mock()
        response.json.return_value = {"data": [{"id": "snapshot-alias"}]}
        server = {
            "pid": 123,
            "process_identity": "proc-id",
            "model_id": "org/model",
            "revision": "a" * 40,
            "load_path": "/cache/snapshots/" + "a" * 40,
            "advertised_model_id": "org/model",
            "advertised_model_id_verified": False,
            "port": 8100,
            "host": "127.0.0.1",
            "started_at": 1,
        }
        with (
            patch(
                "notebooks.mxstudio.servers.state.load",
                side_effect=lambda: {"server": server},
            ),
            patch(
                "notebooks.mxstudio.servers.state.update",
                side_effect=lambda **kwargs: server.update(kwargs["server"]),
            ),
            patch("notebooks.mxstudio.servers._alive", return_value=True),
            patch("notebooks.mxstudio.servers._owned", return_value=True),
            patch.object(ServerManager, "_healthy", return_value=True),
            patch("notebooks.mxstudio.servers.httpx.get", return_value=response),
        ):
            status = ServerManager().status()

        self.assertEqual(status["advertised_model_id"], "snapshot-alias")
        self.assertTrue(server["advertised_model_id_verified"])

    def test_server_loads_snapshot_and_persists_canonical_and_advertised_ids(self) -> None:
        root = Path.cwd() / f".test-pinned-server-{uuid4().hex}"
        snapshot = root / "snapshot"
        logs = root / "logs"
        snapshot.mkdir(parents=True)
        logs.mkdir()
        self.addCleanup(shutil.rmtree, root, True)
        entry = ModelEntry(
            id="org/model",
            name="Model",
            params="1B",
            approx_gb=1,
            context=1024,
            license="Apache-2.0",
            description="test",
            innovation="test",
            load_path=str(snapshot),
            revision="a" * 40,
            advertised_model_id="model-alias",
        )
        state_data = {}
        with (
            patch("notebooks.mxstudio.servers.state.LOGS", logs),
            patch("notebooks.mxstudio.servers.state.load", return_value={}),
            patch(
                "notebooks.mxstudio.servers.state.update",
                side_effect=lambda **kwargs: state_data.update(kwargs),
            ),
            patch(
                "notebooks.mxstudio.servers.subprocess.Popen",
                return_value=type("Process", (), {"pid": 123})(),
            ) as popen,
            patch("notebooks.mxstudio.servers._process_identity", return_value="proc-id"),
            patch("notebooks.mxstudio.servers.platform.system", return_value="Linux"),
        ):
            manager = ServerManager()
            with patch.object(manager, "status", return_value={"state": "starting"}):
                manager._start_unlocked(entry)

        command = popen.call_args.args[0]
        self.assertEqual(command[command.index("--model") + 1], str(snapshot))
        server = state_data["server"]
        self.assertEqual(server["model_id"], "org/model")
        self.assertEqual(server["revision"], "a" * 40)
        self.assertEqual(server["load_path"], str(snapshot))
        self.assertEqual(server["advertised_model_id"], "model-alias")


if __name__ == "__main__":
    unittest.main()
