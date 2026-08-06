from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from notebooks.mxstudio import pathops


class RevealPathTests(unittest.TestCase):
    def test_wsl_path_opens_in_windows_explorer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            launcher = Mock()
            launcher.poll.return_value = None
            converted = subprocess.CompletedProcess(
                ["wslpath"],
                0,
                stdout=r"\\wsl.localhost\Ubuntu-24.04\tmp\package" + "\n",
                stderr="",
            )
            with (
                patch("notebooks.mxstudio.pathops.hwinfo.is_wsl", return_value=True),
                patch("notebooks.mxstudio.pathops.subprocess.run", return_value=converted),
                patch(
                    "notebooks.mxstudio.pathops.shutil.which",
                    side_effect=lambda name: "/usr/bin/explorer.exe"
                    if name == "explorer.exe"
                    else None,
                ),
                patch("notebooks.mxstudio.pathops.subprocess.Popen", return_value=launcher) as popen,
                patch("notebooks.mxstudio.pathops.time.sleep"),
            ):
                result = pathops.reveal_path(target, allowed_roots=(target,))

            self.assertTrue(result["opened"])
            self.assertEqual(result["opened_with"], "explorer.exe")
            self.assertTrue(result["target_windows"].startswith("\\\\wsl.localhost"))
            popen.assert_called_once()

    def test_wslpath_failure_is_not_reported_as_opened(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            converted = subprocess.CompletedProcess(
                ["wslpath"],
                1,
                stdout="",
                stderr="conversion failed",
            )
            with (
                patch("notebooks.mxstudio.pathops.hwinfo.is_wsl", return_value=True),
                patch("notebooks.mxstudio.pathops.subprocess.run", return_value=converted),
                patch("notebooks.mxstudio.pathops.subprocess.Popen") as popen,
            ):
                result = pathops.reveal_path(target, allowed_roots=(target,))

            self.assertFalse(result["opened"])
            self.assertIn("conversion failed", result["error"])
            popen.assert_not_called()

    def test_allowed_root_blocks_unrelated_paths(self) -> None:
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as other:
            result = pathops.reveal_path(
                Path(other),
                allowed_roots=(Path(root),),
            )

        self.assertFalse(result["opened"])
        self.assertIn("outside", result["error"])


if __name__ == "__main__":
    unittest.main()
