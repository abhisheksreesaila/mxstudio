from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()