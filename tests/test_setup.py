from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from notebooks.mxstudio import setup


class SetupPreferencesTests(unittest.TestCase):
    def test_acknowledgement_is_persisted_for_the_matching_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            environment = home / "project" / ".pixi" / "envs" / "default"
            with (
                patch("notebooks.mxstudio.setup.state.HOME", home),
                patch("notebooks.mxstudio.setup.SETUP_FILE", home / "setup.json"),
            ):
                saved = setup.acknowledge("pixi", str(environment))

                self.assertTrue((home / "setup.json").is_file())
                self.assertTrue(setup.is_confirmed(str(environment)))
                self.assertFalse(setup.is_confirmed(str(home / "other-environment")))
                self.assertEqual(saved["setup_version"], setup.SETUP_VERSION)
                self.assertEqual(saved["environment_kind"], "pixi")


if __name__ == "__main__":
    unittest.main()