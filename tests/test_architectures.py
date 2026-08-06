from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from notebooks.mxstudio import registry
from notebooks.mxstudio.servers import custom_arch_path
from notebooks.mxstudio import architectures, downloads, jobs


def analysis_for(root: Path, package: str = "demo_for_causal_lm") -> dict:
    return {
        "ok": True,
        "model_id": "acme/demo",
        "architecture": "DemoForCausalLM",
        "modular_compatible": False,
        "package_name": package,
        "package_path": f"ports/{package}",
        "serve_cmd": (
            "max serve --model acme/demo "
            f"--custom-architectures ports:{package}"
        ),
        "gaps": [],
        "similar_hint": "Dense decoder reference",
        "scaffold_exists": False,
        "docs": architectures.DOCS,
    }


class ArchitecturePathTests(unittest.TestCase):
    def test_installed_models_are_discovered_from_hf_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            complete = cache / "models--acme--local-demo" / "snapshots" / "abc"
            complete.mkdir(parents=True)
            (complete / "config.json").write_text("{}", encoding="utf-8")
            (complete / "model.safetensors").touch()
            incomplete = cache / "models--acme--incomplete" / "snapshots" / "abc"
            incomplete.mkdir(parents=True)
            (incomplete / "config.json").write_text("{}", encoding="utf-8")

            with patch.object(downloads, "HF_HUB_CACHE", str(cache)):
                model_ids = downloads.installed_model_ids()

        self.assertEqual(model_ids, ["acme/local-demo"])

    def test_registry_custom_architecture_controls_slug(self) -> None:
        self.assertEqual(
            architectures._slug(
                "allenai/OLMoE-1B-7B-0125-Instruct",
                "OlmoeForCausalLM",
            ),
            "olmoe_1b_7b_0125_instruct",
        )
        self.assertEqual(
            architectures._slug("acme/demo", "DemoForCausalLM"),
            "demo_for_causal_lm",
        )

    def test_server_and_wizard_use_same_project_root(self) -> None:
        entry = registry.ModelEntry(
            id="acme/demo",
            name="demo",
            params="1B",
            approx_gb=2,
            context=4096,
            license="test",
            description="",
            innovation="",
            custom_arch="ports/demo_for_causal_lm",
        )

        self.assertEqual(
            custom_arch_path(entry),
            (architectures.REPO_ROOT / "ports/demo_for_causal_lm").resolve(),
        )

    def test_analyze_builds_documented_custom_serve_commands(self) -> None:
        report = {
            "ok": True,
            "model_id": "acme/demo",
            "architecture": "DemoForCausalLM",
            "modular_compatible": False,
            "approx_gb": 2,
            "hardware": {"level": "ok"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch.object(architectures, "REPO_ROOT", root),
                patch.object(architectures, "PORTS_DIR", root / "ports"),
                patch.object(architectures, "PLANS_DIR", root / "results" / "bringup"),
                patch("notebooks.mxstudio.architectures.compat.check_hf_model", return_value=report),
                patch("notebooks.mxstudio.architectures.registry.get", return_value=None),
            ):
                result = architectures.analyze("acme/demo")

        self.assertEqual(
            result["serve_cmd"],
            "max serve --model acme/demo "
            "--custom-architectures ports:demo_for_causal_lm",
        )
        self.assertIn('"model": "acme/demo"', result["request_cmd"])


class ArchitectureScaffoldTests(unittest.TestCase):
    @patch.object(jobs, "enqueue_process")
    def test_architecture_jobs_use_detached_worker(self, enqueue_process) -> None:
        enqueue_process.return_value = {
            "job_id": "arch-scaffold-test",
            "kind": "arch_scaffold",
            "state": "processing",
        }

        result = jobs.start_kind("arch_scaffold", model_id="acme/demo")

        self.assertEqual(result["job_id"], "arch-scaffold-test")
        enqueue_process.assert_called_once()

    @patch.object(jobs, "enqueue_process")
    def test_skill_stage_job_routes_stage_to_detached_worker(self, enqueue_process) -> None:
        enqueue_process.return_value = {
            "job_id": "arch-stage-test",
            "kind": "arch_stage",
            "state": "processing",
        }

        result = jobs.start_kind("arch_stage", model_id="acme/demo", stage="inspect")

        self.assertEqual(result["job_id"], "arch-stage-test")
        enqueue_process.assert_called_once_with(
            "arch_stage",
            "Architecture stage acme/demo",
            {"model_id": "acme/demo", "stage": "inspect"},
        )

    def test_scaffold_never_overwrites_human_edits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            analysis = analysis_for(root)
            analysis["plan"] = {"approved": True}
            with (
                patch.object(architectures, "REPO_ROOT", root),
                patch.object(architectures, "PORTS_DIR", root / "ports"),
                patch.object(architectures, "analyze", return_value=analysis),
            ):
                first = architectures.scaffold("acme/demo")
                model_path = root / "ports/demo_for_causal_lm/model.py"
                model_path.write_text("# human implementation\n", encoding="utf-8")
                second = architectures.scaffold("acme/demo")

            self.assertTrue(first["scaffolded"])
            self.assertFalse(second["scaffolded"])
            arch_text = (root / "ports/demo_for_causal_lm/arch.py").read_text(
                encoding="utf-8"
            )
            self.assertIn("default_encoding=CustomModelConfig.DEFAULT_ENCODING", arch_text)
            self.assertIn("context_type=TextContext", arch_text)
            self.assertEqual(
                model_path.read_text(encoding="utf-8"),
                "# human implementation\n",
            )
            self.assertIn("model.py", second["preserved_files"])

    def test_plan_persists_and_blocks_scaffold_until_approved(self) -> None:
        report = {
            "ok": True,
            "model_id": "acme/demo",
            "architecture": "DemoForCausalLM",
            "modular_compatible": False,
            "approx_gb": 2,
            "hardware": {"level": "ok"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch.object(architectures, "REPO_ROOT", root),
                patch.object(architectures, "PORTS_DIR", root / "ports"),
                patch.object(architectures, "PLANS_DIR", root / "results" / "bringup"),
                patch("notebooks.mxstudio.architectures.compat.check_hf_model", return_value=report),
                patch("notebooks.mxstudio.architectures.registry.get", return_value=None),
            ):
                inspected = architectures.analyze("acme/demo")
                blocked = architectures.scaffold("acme/demo")
                approved = architectures.approve_plan("acme/demo")
                scaffolded = architectures.scaffold("acme/demo")

            plan_path = root / "results" / "bringup" / "demo_for_causal_lm.json"
            stored = json.loads(plan_path.read_text(encoding="utf-8"))

        self.assertFalse(inspected["plan"]["approved"])
        self.assertFalse(blocked["ok"])
        self.assertIn("approve", blocked["error"])
        self.assertTrue(approved["plan"]["approved"])
        self.assertTrue(stored["approved"])
        self.assertTrue(scaffolded["scaffolded"])

    def test_existing_metadata_resolves_package_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ports = Path(tmp) / "ports"
            package = ports / "demo_for_causal_lm"
            package.mkdir(parents=True)
            (package / "mxstudio_meta.json").write_text(
                json.dumps({"model_id": "acme/demo"}),
                encoding="utf-8",
            )
            with (
                patch.object(architectures, "PORTS_DIR", ports),
                patch("notebooks.mxstudio.architectures.registry.get", return_value=None),
            ):
                resolved = architectures.package_dir_for_model("acme/demo")

            self.assertEqual(resolved, package)

    def test_validation_gate_evidence_persists_across_inspection(self) -> None:
        report = {
            "ok": True,
            "model_id": "acme/demo",
            "architecture": "DemoForCausalLM",
            "modular_compatible": False,
            "approx_gb": 2,
            "hardware": {"level": "ok"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch.object(architectures, "REPO_ROOT", root),
                patch.object(architectures, "PORTS_DIR", root / "ports"),
                patch.object(architectures, "PLANS_DIR", root / "results" / "bringup"),
                patch("notebooks.mxstudio.architectures.compat.check_hf_model", return_value=report),
                patch("notebooks.mxstudio.architectures.registry.get", return_value=None),
            ):
                inspected = architectures.analyze("acme/demo")
                blocked = architectures.update_validation_gate(
                    "acme/demo", "DemoForCausalLM", "strict_load", "passed"
                )
                architectures.approve_plan("acme/demo")
                updated = architectures.update_validation_gate(
                    "acme/demo",
                    "DemoForCausalLM",
                    "strict_load",
                    "passed",
                    ["missing=0", "unexpected=0", "mismatched=0", "orphan=0"],
                    "Adapter and graph key sets match.",
                )
                refreshed = architectures.analyze("acme/demo")

        self.assertEqual(inspected["plan"]["schema_version"], 2)
        self.assertEqual(len(inspected["plan"]["validation_gates"]), 4)
        self.assertFalse(blocked["ok"])
        self.assertEqual(updated["gate"]["status"], "passed")
        strict_load = next(
            gate
            for gate in refreshed["plan"]["validation_gates"]
            if gate["id"] == "strict_load"
        )
        self.assertEqual(strict_load["status"], "blocked")
        self.assertEqual(strict_load["artifacts"], updated["gate"]["artifacts"])
        self.assertIn("Legacy manual evidence", strict_load["notes"])

    def test_manifest_audit_names_concrete_source_targets(self) -> None:
        package = architectures.REPO_ROOT / "ports/olmoe_1b_7b_0125_instruct"
        manifest = architectures._bringup_manifest(package)

        checks = architectures._audit_manifest(package, manifest)

        self.assertGreaterEqual(len(checks), 6)
        self.assertTrue(all(check["ok"] for check in checks), checks)
        self.assertIn("layers/attention.py", {check["file"] for check in checks})
        self.assertIn("strict=True", {check["symbol"] for check in checks})


if __name__ == "__main__":
    unittest.main()
