from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from notebooks.mxstudio import slides
from notebooks.mxstudio.registry import REGISTRY
from notebooks.mxstudio import jobs, jobs_db, notebooks


class NotebookArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.notebooks_dir = Path(self.tempdir.name) / "notebooks"
        self.dir_patch = patch.object(notebooks, "NOTEBOOKS_DIR", self.notebooks_dir)
        self.dir_patch.start()
        self.card_patch = patch(
            "notebooks.mxstudio.modelinfo.summarize_model_card", return_value=""
        )
        self.card_patch.start()

    def tearDown(self) -> None:
        self.card_patch.stop()
        self.dir_patch.stop()
        self.tempdir.cleanup()

    def test_save_preserves_mxstudio_model_metadata(self) -> None:
        generated = notebooks.generate_notebook(REGISTRY[0].id)
        name = generated["name"]
        before = notebooks.load_notebook(name)

        notebooks.save_notebook(name, before["cells"][:2])

        saved = json.loads((self.notebooks_dir / name).read_text())
        self.assertEqual(
            saved["metadata"]["mxstudio"]["model_id"],
            REGISTRY[0].id,
        )
        self.assertEqual(saved["metadata"]["mxstudio"]["kind"], "educational_lab")

    def test_custom_org_model_generates_truthful_notebook_and_slide_cache(self) -> None:
        model_id = "example-org/Uncurated.Model"
        result = notebooks.generate_notebook(model_id)
        notebook_path = Path(result["path"])
        slide_path = Path(result["slides_path"])

        self.assertEqual(notebook_path.name, "example-org--uncurated.model.ipynb")
        self.assertTrue(slide_path.is_file())
        notebook = json.loads(notebook_path.read_text())
        joined = "\n".join(str(cell["source"]) for cell in notebook["cells"])
        self.assertEqual(
            notebook["metadata"]["mxstudio"]["model_id"],
            model_id,
        )
        self.assertIn("compatibility not yet verified", joined)
        self.assertIn("Unknown — check model card", joined)

        slide_path.unlink()
        rebuilt = slides.load_slides(notebook_path, model_id)
        self.assertFalse(rebuilt["cached"])
        self.assertEqual(rebuilt["model_id"], model_id)
        self.assertTrue(slide_path.is_file())

    def test_invalid_custom_model_id_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "org/name"):
            notebooks.resolve_model_entry("local-name-only")

    def test_curated_slides_mirror_details_drawer_narrative(self) -> None:
        model_id = REGISTRY[0].id
        deck = slides.build_slides(model_id)
        kinds = [slide.get("kind") for slide in deck["slides"]]

        self.assertEqual(deck["schema_version"], slides.SCHEMA_VERSION)
        self.assertEqual(kinds[0], "title")
        self.assertIn("persona", kinds)
        self.assertIn("fit", kinds)
        self.assertIn("innovation", kinds)
        self.assertIn("architecture", kinds)
        self.assertIn("walkthrough", kinds)
        self.assertIn("goals", kinds)
        self.assertIn("sources", kinds)
        walk = next(s for s in deck["slides"] if s["kind"] == "walkthrough")
        self.assertIn("OpenAI", walk["code"])
        self.assertTrue(walk.get("fragments"))
        persona = next(s for s in deck["slides"] if s["kind"] == "persona")
        fit = next(s for s in deck["slides"] if s["kind"] == "fit")
        architecture = next(s for s in deck["slides"] if s["kind"] == "architecture")

        self.assertEqual(persona["title"], "A pocket laboratory")
        self.assertEqual(persona["persona"], "pocket")
        self.assertEqual([c["label"] for c in fit["cards"]], ["Great for", "Think twice for"])
        self.assertTrue(all(c["text"] for c in fit["cards"]))
        self.assertEqual(
            architecture["flow"]["nodes"],
            ["Prompt", "Attention", "MLP", "Next token"],
        )

    @patch("notebooks.mxstudio.state.load")
    @patch("notebooks.mxstudio.compat.hardware_fit")
    def test_curated_slides_include_local_runtime_evidence(self, hardware_fit, load_state) -> None:
        model_id = REGISTRY[0].id
        hardware_fit.return_value = {
            "level": "ok",
            "message": "Fits with 18 GB VRAM free.",
        }
        load_state.return_value = {
            "bench_history": [
                {
                    "model_id": model_id,
                    "backend": "max_benchmark",
                    "summary": {"mean_tok_per_s": 42.5, "mean_ttft_s": 0.18},
                }
            ]
        }

        deck = slides.build_slides(model_id)
        evidence = next(s for s in deck["slides"] if s["kind"] == "evidence")

        self.assertIn("this machine", evidence["title"].lower())
        self.assertIn("18 GB VRAM", evidence["cards"][0]["text"])
        self.assertIn("42.5 tok/s", evidence["cards"][1]["text"])

    def test_old_slide_schema_is_rebuilt_on_load(self) -> None:
        model_id = REGISTRY[0].id
        notebook_path = self.notebooks_dir / "qwen-demo.ipynb"
        notebook_path.parent.mkdir(parents=True, exist_ok=True)
        notebook_path.write_text("{}", encoding="utf-8")
        stale = slides.slides_path(notebook_path)
        stale.write_text(
            json.dumps(
                {
                    "ok": True,
                    "schema_version": 1,
                    "model_id": model_id,
                    "title": "stale",
                    "slides": [{"kicker": "old", "title": "plain", "body": "boring"}],
                }
            ),
            encoding="utf-8",
        )

        rebuilt = slides.load_slides(notebook_path, model_id)

        self.assertFalse(rebuilt["cached"])
        self.assertEqual(rebuilt["schema_version"], slides.SCHEMA_VERSION)
        self.assertGreaterEqual(len(rebuilt["slides"]), 8)
        self.assertTrue(any(s.get("kind") == "fit" for s in rebuilt["slides"]))


    def test_olmoe_deck_discloses_pending_numerical_parity(self) -> None:
        olmoe = next(m for m in REGISTRY if m.id.startswith("allenai/OLMoE"))
        deck = slides.build_slides(olmoe.id)
        kinds = [s.get("kind") for s in deck["slides"]]
        title = next(s for s in deck["slides"] if s["kind"] == "title")

        self.assertTrue(olmoe.serve_enabled)
        self.assertNotIn("parked", kinds)
        self.assertIn("numerical parity", title["body"])


class NotebookJobTests(unittest.TestCase):
    def test_generation_runs_through_persistent_job_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "jobs.db"
            expected = {
                "model_id": "org/model",
                "name": "org--model.ipynb",
                "slides_name": "org--model.slides.json",
            }
            with (
                patch.object(jobs_db, "DB_PATH", db_path),
                patch.object(notebooks, "generate_notebook", return_value=expected),
            ):
                queued = jobs.start_kind(
                    "notebook_generate", model_id="org/model"
                )
                deadline = time.time() + 3
                current = queued
                while current["state"] == "processing" and time.time() < deadline:
                    time.sleep(0.02)
                    current = jobs.get(queued["job_id"])

                self.assertEqual(current["state"], "done")
                self.assertEqual(current["kind"], "notebook_generate")
                self.assertEqual(current["result"], expected)
                persisted = jobs_db.get(queued["job_id"])
                self.assertEqual(persisted["state"], "done")


if __name__ == "__main__":
    unittest.main()
