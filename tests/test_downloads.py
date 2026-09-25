from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

from notebooks.mxstudio import downloads


class LocalModelMetadataTests(unittest.TestCase):
    def test_reads_catalog_fields_from_cached_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            snapshot = repo / "snapshots" / "revision"
            snapshot.mkdir(parents=True)
            (snapshot / "config.json").write_text(
                json.dumps(
                    {
                        "architectures": ["Gemma3ForCausalLM"],
                        "model_type": "gemma3_text",
                        "max_position_embeddings": 32768,
                    }
                )
            )

            with (
                patch("notebooks.mxstudio.downloads.repo_dir", return_value=repo),
                patch("notebooks.mxstudio.downloads.local_size_bytes", return_value=4_250_000_000),
            ):
                metadata = downloads.local_model_metadata("example/model")

        self.assertEqual(metadata["approx_gb"], 4.2)
        self.assertEqual(metadata["context"], 32768)
        self.assertEqual(metadata["architecture"], "Gemma3ForCausalLM")
        self.assertEqual(metadata["family"], "Gemma3 Text")


class SnapshotValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / f".test-snapshot-{uuid4().hex}"
        self.snapshot = self.root / "snapshots" / ("a" * 40)
        self.snapshot.mkdir(parents=True)
        (self.snapshot / "config.json").write_text("{}")
        (self.snapshot / "tokenizer.json").write_text("{}")
        (self.snapshot / "model.safetensors.index.json").write_text(
            json.dumps({"weight_map": {"layer.weight": "model-00001.safetensors"}})
        )
        (self.snapshot / "model-00001.safetensors").write_bytes(b"weights")

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_indexed_snapshot_requires_every_shard_and_tokenizer(self) -> None:
        file_names = [
            "config.json",
            "tokenizer.json",
            "model.safetensors.index.json",
            "model-00001.safetensors",
        ]

        selected = downloads.required_snapshot_files(file_names)
        result = downloads.validate_snapshot(self.snapshot, selected)

        self.assertTrue(result["ok"])
        self.assertEqual(result["missing_files"], [])
        self.assertEqual(
            {item["name"] for item in result["manifest"]},
            set(file_names),
        )
        mismatch = downloads.validate_snapshot(
            self.snapshot, expected_sizes={"model-00001.safetensors": 8}
        )
        self.assertFalse(mismatch["ok"])
        self.assertEqual(mismatch["size_mismatches"][0]["expected"], 8)

        (self.snapshot / "model-00001.safetensors").unlink()
        result = downloads.validate_snapshot(self.snapshot, selected)
        self.assertFalse(result["ok"])
        self.assertIn("model-00001.safetensors", result["missing_files"])

    def test_pinned_pull_passes_exact_revision_and_checks_manifest(self) -> None:
        file_names = [
            "config.json",
            "tokenizer.json",
            "model.safetensors.index.json",
            "model-00001.safetensors",
        ]
        progress = []
        with patch(
            "notebooks.mxstudio.downloads.snapshot_download",
            return_value=str(self.snapshot),
        ) as snapshot_download:
            result = downloads.pull_revision(
                "example/model", "a" * 40, file_names,
                token="hf-test-token", progress=progress.append,
            )

        self.assertEqual(result["revision"], "a" * 40)
        self.assertTrue(result["ok"])
        snapshot_download.assert_called_once()
        self.assertEqual(snapshot_download.call_args.kwargs["revision"], "a" * 40)
        self.assertEqual(snapshot_download.call_args.kwargs["token"], "hf-test-token")
        self.assertEqual(progress[0]["stage"], "downloading")
        self.assertEqual(progress[-1]["stage"], "downloaded")


if __name__ == "__main__":
    unittest.main()