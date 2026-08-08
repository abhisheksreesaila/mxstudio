from __future__ import annotations

import unittest
from unittest.mock import patch

from notebooks.mxstudio import hwinfo


class HardwareInfoTests(unittest.TestCase):
    def test_gpu_optional_wsl_metrics_do_not_drop_device(self) -> None:
        rows = hwinfo._parse_gpus(
            "NVIDIA GeForce RTX 3090, 24576, 812, [N/A], [N/A]"
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "NVIDIA GeForce RTX 3090")
        self.assertEqual(rows[0]["vram_total_mb"], 24576)
        self.assertIsNone(rows[0]["util_pct"])
        self.assertIsNone(rows[0]["temp_c"])

    @patch("notebooks.mxstudio.hwinfo.shutil.disk_usage", side_effect=OSError("offline"))
    def test_disk_failure_returns_partial_snapshot(self, _disk_usage) -> None:
        self.assertEqual(
            hwinfo.disk(),
            {"total_gb": None, "free_gb": None},
        )

    @patch("notebooks.mxstudio.hwinfo.subprocess.run")
    def test_macos_memory_uses_unified_memory_statistics(self, run) -> None:
        run.side_effect = [
            unittest.mock.Mock(stdout=str(16 * 1024**3)),
            unittest.mock.Mock(
                stdout=(
                    "Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
                    "Pages free: 131072.\n"
                    "Pages inactive: 65536.\n"
                    "Pages speculative: 65536.\n"
                )
            ),
        ]

        self.assertEqual(
            hwinfo._macos_memory(),
            {"total_gb": 16.0, "available_gb": 4.0},
        )


if __name__ == "__main__":
    unittest.main()
