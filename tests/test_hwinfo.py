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


if __name__ == "__main__":
    unittest.main()
