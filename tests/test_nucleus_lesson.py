import json
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.request import Request

from notebooks.mxstudio.nucleus_lesson import (
    SOURCES,
    build_lesson,
    write_lesson,
)


class NucleusLessonTests(unittest.TestCase):
    def test_generation_is_deterministic_and_editable(self):
        self.assertEqual(build_lesson(), build_lesson())
        notebook = build_lesson()
        source = "\n".join("".join(cell["source"]) for cell in notebook["cells"])
        self.assertIn("MXSTUDIO_NUCLEUS_API_KEY", source)
        self.assertIn("measure_ttft", source)
        self.assertIn("no precomputed", source)
        self.assertTrue(all(name and url for name, url in SOURCES))

    def measure_generated(self, lines, clock):
        response = MagicMock()
        response.__enter__.return_value = iter(lines)
        opener = MagicMock(return_value=response)
        namespace = {
            "json": json, "time": SimpleNamespace(perf_counter=clock),
            "request": SimpleNamespace(Request=Request, urlopen=opener),
            "ENDPOINT": "http://127.0.0.1:8080", "API_KEY": "test-only-key",
            "MODEL": "example/a", "PROMPT": "test", "MAX_TOKENS": 64,
            "TEMPERATURE": 0.0, "CONTEXT": {"hardware_notes": "mocked"},
        }
        exec(compile(build_lesson()["cells"][2]["source"], "lesson-cell", "exec"), namespace)
        result = namespace["measure_ttft"]()
        response.__exit__.assert_called_once()
        self.assertNotIn("test-only-key", json.dumps(result))
        return result

    def test_sse_ttft_ignores_role_heartbeat_and_drains_stream(self):
        now = iter([10.0, 10.4, 11.0])
        lines = [
            b"data: {\"choices\":[{\"delta\":{\"role\":\"assistant\"}}]}\n",
            b"data: {\"choices\":[{\"delta\":{}}]}\n",
            b"data: {\"choices\":[{\"delta\":{\"content\":\"Hi\"}}]}\n",
            b"data: {\"choices\":[{\"delta\":{\"content\":\" there\"}}]}\n",
            b"data: [DONE]\n",
        ]
        result = self.measure_generated(lines, clock=lambda: next(now))
        self.assertAlmostEqual(result["ttft_seconds"], 0.4)
        self.assertAlmostEqual(result["total_seconds"], 1.0)
        self.assertEqual(len(result["raw_events"]), 4)
        self.assertEqual(result["request"]["max_tokens"], 64)
        self.assertEqual(result["context"]["hardware_notes"], "mocked")

    def test_sse_without_token_is_error(self):
        with self.assertRaisesRegex(RuntimeError, "without a non-empty token"):
            self.measure_generated([b'data: {"choices":[{"delta":{"role":"assistant"}}]}'], clock=lambda: 1)

    def test_write_is_json_only_and_does_not_execute(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_lesson(Path(directory) / "lesson.ipynb")
            loaded = json.loads(path.read_text())
            self.assertEqual(loaded["nbformat"], 4)
            for cell in loaded["cells"]:
                if cell["cell_type"] == "code":
                    self.assertIsNone(cell["execution_count"])
                    compile(cell["source"], cell["id"], "exec")
            path.write_text("hand edit")
            with self.assertRaises(FileExistsError):
                write_lesson(path)
            self.assertEqual(path.read_text(), "hand edit")
