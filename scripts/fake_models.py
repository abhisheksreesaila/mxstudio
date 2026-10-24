"""Fill a scratch store with fake models and labels, to see the library at scale (F-055).

    MXC_HOME=/tmp/mxc-lib pixi run python scripts/fake_models.py [count]
    MXC_HOME=/tmp/mxc-lib MXC_PORT=11499 pixi run mxc daemon

Refuses to touch the real ~/.mxc: MXC_HOME must be set and must not be that folder. The models have no weights,
so they sit idle (Start would fail); that is enough to see search, groups, labels and the tick bar with 100 rows.
"""
import os
import sys
from pathlib import Path

from mxconsole.store import Store, mxc_home

FAMILIES = [("qwen2.5", "Qwen2ForCausalLM", ["1.5b", "3b", "7b", "14b"]), ("qwen2.5-coder", "Qwen2ForCausalLM", ["0.5b", "3b", "7b", "14b"]),
            ("llama-3.2", "LlamaForCausalLM", ["1b", "3b"]), ("gemma-3", "Gemma3ForCausalLM", ["1b", "4b", "12b"]), ("phi-4-mini", "Phi3ForCausalLM", ["3.8b"]),
            ("smollm2", "LlamaForCausalLM", ["135m", "360m", "1.7b"]), ("mistral", "MistralForCausalLM", ["7b"]), ("deepseek-coder", "LlamaForCausalLM", ["1.3b", "6.7b"]),
            ("starcoder2", "Starcoder2ForCausalLM", ["3b", "7b"]), ("qwen2.5-vl", "Qwen2VLForConditionalGeneration", ["3b", "7b"]), ("smolvlm", "Idefics3ForConditionalGeneration", ["256m", "2.2b"]),
            ("bge", "BertModel", ["small", "base", "large"]), ("nomic-embed-text", "NomicBertModel", ["v1.5"]), ("flux.1", "FluxPipeline", ["schnell", "dev"]), ("sdxl", "StableDiffusionXLPipeline", ["turbo", "base"])]
SUFFIXES = ["", "-instruct", "-q4"]
LABELS = ["work", "favorites", "experiments", "client-acme"]


def main(count: int) -> None:
    home = mxc_home()
    if "MXC_HOME" not in os.environ or home == Path.home() / ".mxc":
        sys.exit("Set MXC_HOME to a scratch folder first: this never touches your real models.")
    store = Store(home / "mst.db")
    names = []
    for family, arch, sizes in FAMILIES:
        for size in sizes:
            for suffix in SUFFIXES:
                if len(names) < count:
                    names.append((f"{family}-{size}{suffix}", family, arch))
    for name, family, arch in names:
        store.add_model(name=name, repo_id=f"fake/{name}", revision="main", source="huggingface", architecture=arch)
    for label in LABELS:
        store.create_label(label)
    for i, (name, _, _) in enumerate(names, 1):
        for label, step in zip(LABELS, (5, 7, 11, 13)):
            if i % step == 0:
                store.add_labels([name], [label])
    print(f"{len(names)} fake models and {len(LABELS)} labels in {home}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 100)
