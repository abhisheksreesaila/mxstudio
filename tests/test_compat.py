from __future__ import annotations

import unittest
from unittest.mock import patch

from notebooks.mxstudio import compat


class EnvironmentStatusTests(unittest.TestCase):
    @patch("notebooks.mxstudio.compat.subprocess.run")
    @patch("notebooks.mxstudio.compat.shutil.which")
    def test_direct_max_cli_reports_version(self, which, run) -> None:
        which.side_effect = lambda command: "/usr/bin/max" if command == "max" else None
        run.return_value.returncode = 0
        run.return_value.stdout = "MAX 26.5.0\n"
        run.return_value.stderr = ""

        status = compat._max_cli_status()

        self.assertTrue(status["available"])
        self.assertEqual(status["source"], "path")
        self.assertEqual(status["version"], "MAX 26.5.0")

    @patch("notebooks.mxstudio.compat.subprocess.run")
    @patch("notebooks.mxstudio.compat.shutil.which")
    def test_project_pixi_max_cli_is_discovered(self, which, run) -> None:
        which.side_effect = lambda command: "/usr/bin/pixi" if command == "pixi" else None
        run.return_value.returncode = 0
        run.return_value.stdout = "MAX 26.5.0\n"
        run.return_value.stderr = ""

        status = compat._max_cli_status()

        self.assertTrue(status["available"])
        self.assertEqual(status["source"], "pixi")
        self.assertEqual(status["command"], "/usr/bin/pixi run max")

    @patch("notebooks.mxstudio.compat.studio_settings.public_settings", return_value={})
    @patch("notebooks.mxstudio.compat.studio_settings.get_hf_token", return_value="")
    @patch("notebooks.mxstudio.compat.shutil.disk_usage")
    @patch("notebooks.mxstudio.compat.shutil.which")
    def test_pixi_without_max_is_not_reported_as_max_ready(
        self,
        which,
        disk_usage,
        _get_hf_token,
        _public_settings,
    ) -> None:
        which.side_effect = lambda command: "/usr/bin/pixi" if command == "pixi" else None
        disk_usage.return_value.free = 100_000_000_000

        status = compat.env_status()

        self.assertFalse(status["max_cli"])
        self.assertFalse(status["ready"])
        self.assertIn("pixi install", status["max_install_command"])

    @patch("notebooks.mxstudio.compat.setup.is_confirmed", return_value=False)
    @patch("notebooks.mxstudio.compat._max_cli_status")
    def test_env_status_recognizes_project_pixi_environment(
        self, max_status, _is_confirmed
    ) -> None:
        max_status.return_value = {
            "available": True,
            "source": "path",
            "version": "MAX test",
            "command": "max",
        }
        executable = compat.PROJECT_ENVIRONMENT / "bin" / "python"

        with patch("notebooks.mxstudio.compat.sys.executable", str(executable)):
            status = compat.env_status()

        self.assertTrue(status["project_environment"])
        self.assertTrue(status["isolated_environment"])
        self.assertEqual(status["environment_kind"], "pixi")
        self.assertEqual(status["environment_path"], str(compat.PROJECT_ENVIRONMENT.resolve()))

    @patch("notebooks.mxstudio.compat.setup.is_confirmed", return_value=False)
    @patch("notebooks.mxstudio.compat._max_cli_status")
    def test_env_status_warns_for_system_python(self, max_status, _is_confirmed) -> None:
        max_status.return_value = {
            "available": False,
            "source": "missing",
            "version": None,
            "command": None,
            "install_command": "pixi install",
        }

        with (
            patch("notebooks.mxstudio.compat.sys.executable", "/usr/bin/python3"),
            patch("notebooks.mxstudio.compat.sys.prefix", "/usr"),
            patch("notebooks.mxstudio.compat.sys.base_prefix", "/usr"),
        ):
            status = compat.env_status()

        self.assertFalse(status["project_environment"])
        self.assertFalse(status["isolated_environment"])
        self.assertEqual(status["environment_kind"], "system")


class HardwareFitTests(unittest.TestCase):
    @patch("notebooks.mxstudio.compat.hwinfo.memory", return_value={"available_gb": 32, "total_gb": 64})
    @patch("notebooks.mxstudio.compat.hwinfo.gpus")
    def test_fit_counts_only_selected_cuda_devices(self, gpus, _memory) -> None:
        gpus.return_value = [
            {"name": "GPU 0", "vram_total_mb": 8192, "vram_used_mb": 0},
            {"name": "GPU 1", "vram_total_mb": 8192, "vram_used_mb": 0},
        ]

        fit = compat.hardware_fit(10, cuda_devices="0")

        self.assertEqual(fit["gpu_count"], 1)
        self.assertEqual(fit["total_vram_gb"], 8.0)
        self.assertNotEqual(fit["level"], "ok")


if __name__ == "__main__":
    unittest.main()