"""Test doubles for the MAX catalog and the Hugging Face client."""
from hotfire import resolve as r

CATALOG = {
    "Qwen2ForCausalLM": {"example_repo_ids": ["Qwen/Qwen2.5-0.5B-Instruct"],
                         "supported_encodings": ["bfloat16", "float32"]},
}

FILES = ["config.json", "model.safetensors", "tokenizer.json", "pytorch_model.bin", "README.md"]


class FakeHF:
    def __init__(self, repos=None, gated=()):
        self.repos = repos or {}
        self.gated = set(gated)
        self.downloads = []

    def inspect(self, repo_id):
        if repo_id in self.gated:
            raise r.RepoGated(repo_id)
        if repo_id not in self.repos:
            raise r.RepoNotFound(repo_id)
        arch, files = self.repos[repo_id]
        return r.RepoInfo(sha="abc123", architecture=arch, files=files)

    def read_json(self, repo_id, revision, filename):
        return {"weight_map": {"a": "model-00001.safetensors", "b": "model-00002.safetensors"}}

    def download(self, repo_id, revision, patterns):
        self.downloads.append((repo_id, revision, patterns))
        return "/cache/snap"


def make_hf(**kw):
    return FakeHF({"Qwen/Qwen2.5-0.5B-Instruct": ("Qwen2ForCausalLM", FILES),
                   "someone/qwen-finetune": ("Qwen2ForCausalLM", FILES),
                   "acme/pickle": ("Qwen2ForCausalLM", ["config.json", "pytorch_model.bin", "tokenizer.json"]),
                   "acme/sharded": ("Qwen2ForCausalLM", [
                       "config.json", "tokenizer.json", "model.safetensors.index.json",
                       "model-00001.safetensors", "model-00002.safetensors", "consolidated.safetensors"]),
                   "bob/foo": ("Qwen2ForCausalLM", FILES),
                   "alice/foo": ("Qwen2ForCausalLM", FILES),
                   "acme/weird": ("WeirdForCausalLM", FILES)}, **kw)
