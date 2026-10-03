"""A fake Hugging Face that "downloads" real files slowly, so progress can be watched without a network.

Used two ways:
- tests import SlowHub directly;
- a scratch daemon loads this file when MXC_FAKE_HUB is its absolute path (see mxconsole/admin_pull.py).

Env knobs (daemon mode only): FAKE_HUB_MB (size of model.safetensors, default 24),
FAKE_HUB_CHUNK_DELAY (seconds per 256 KB chunk, default 0.05).
"""
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fakes import CATALOG, make_hf  # noqa: E402,F401

from mxconsole import resolve as r  # noqa: E402

CHUNK = 256 * 1024
SMALL = {"config.json": 700, "tokenizer.json": 40_000}


class SlowHub:
    """Wraps FakeHF's repos; download() writes files into a temp dir a chunk at a time."""

    def __init__(self, hf=None, *, weights_bytes=2 * CHUNK, chunk_delay=0.0, root=None, fail_after=None):
        self.hf = hf or make_hf()
        self.weights_bytes, self.chunk_delay, self.fail_after = weights_bytes, chunk_delay, fail_after
        self.root = Path(root or tempfile.mkdtemp(prefix="fake-hub-"))
        self.downloads = []
        self.gate = threading.Event()  # tests can clear it to hold the download mid-way
        self.gate.set()

    def inspect(self, repo_id):
        return self.hf.inspect(repo_id)

    def read_json(self, repo_id, revision, filename):
        return self.hf.read_json(repo_id, revision, filename)

    def size_of(self, filename):
        return self.weights_bytes if filename.endswith(".safetensors") else SMALL.get(filename, 1000)

    def download(self, repo_id, revision, patterns, progress=None, cancel=None):
        self.downloads.append((repo_id, revision, list(patterns)))
        snap = self.root / repo_id.replace("/", "--") / revision
        snap.mkdir(parents=True, exist_ok=True)
        for f in patterns:  # sizes first, like the real client
            if progress:
                progress(f, 0, self.size_of(f))
        sent = 0
        for f in patterns:
            size, done = self.size_of(f), 0
            with open(snap / f, "wb") as out:
                while done < size:
                    self.gate.wait()
                    if cancel is not None and cancel.is_set():
                        raise r.PullCancelled()
                    n = min(CHUNK, size - done)
                    out.write(b"\0" * n)
                    done += n
                    sent += n
                    if self.chunk_delay:
                        time.sleep(self.chunk_delay)
                    if self.fail_after is not None and sent >= self.fail_after:
                        raise r.ResolveError("download of the model failed (ConnectError); retry to resume")
                    if progress:
                        progress(f, done, size)
        return str(snap)


def make_hub():
    return SlowHub(weights_bytes=int(float(os.environ.get("FAKE_HUB_MB", "24")) * 1024 * 1024),
                   chunk_delay=float(os.environ.get("FAKE_HUB_CHUNK_DELAY", "0.05")))
