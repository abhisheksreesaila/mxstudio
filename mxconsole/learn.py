"""`mxc learn`: turn a model's Hugging Face metadata into a notebook you can read and run.

All data comes from the Hugging Face API (via an injectable `HFSource`), never from scraping.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

import nbformat
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook

from mxconsole.resolve import CURATED, model_name
from mxconsole.settings import daemon_url
from mxconsole.store import Store, mxc_home

CARD_LIMIT = 6000


@dataclass
class HFInfo:
    repo_id: str
    revision: str | None = None
    pipeline_tag: str | None = None
    license: str | None = None
    downloads: int | None = None
    likes: int | None = None
    safetensors_total: int | None = None  # total parameter count from safetensors metadata
    tags: list[str] = field(default_factory=list)


class HFSource(Protocol):
    def info(self, repo_id: str, revision: str | None) -> HFInfo: ...
    def card_text(self, repo_id: str, revision: str | None) -> str: ...
    def config(self, repo_id: str, revision: str | None) -> dict[str, Any]: ...


class HubSource:
    """The real thing: huggingface_hub API calls only, with HF_TOKEN handled by resolve.HubClient."""

    def __init__(self):
        from mxconsole.resolve import HubClient
        self.client = HubClient()

    def info(self, repo_id, revision):
        from huggingface_hub.errors import GatedRepoError, RepositoryNotFoundError
        try:
            mi = self.client.api.model_info(repo_id, revision=revision)
        except GatedRepoError as e:
            raise RuntimeError(f"{repo_id} is gated: accept its license on huggingface.co, "
                               "then set HF_TOKEN and retry.") from e
        except RepositoryNotFoundError as e:
            raise RuntimeError(f"no such model on Hugging Face: {repo_id} (use owner/repo)") from e
        card = mi.card_data.to_dict() if getattr(mi, "card_data", None) else {}
        st = getattr(mi, "safetensors", None)
        total = getattr(st, "total", None) if st else None
        lic = card.get("license")
        if isinstance(lic, list):
            lic = ", ".join(lic)
        return HFInfo(repo_id=repo_id, revision=getattr(mi, "sha", revision), pipeline_tag=mi.pipeline_tag,
                      license=lic, downloads=mi.downloads, likes=mi.likes, safetensors_total=total,
                      tags=list(mi.tags or []))

    def card_text(self, repo_id, revision):
        from huggingface_hub import ModelCard, hf_hub_download
        from huggingface_hub.errors import EntryNotFoundError
        try:
            path = hf_hub_download(repo_id, "README.md", revision=revision, token=self.client.token)
        except EntryNotFoundError:
            return ""
        return ModelCard(Path(path).read_text(encoding="utf-8")).text or ""

    def config(self, repo_id, revision):
        from huggingface_hub import hf_hub_download
        from huggingface_hub.errors import EntryNotFoundError
        try:
            path = hf_hub_download(repo_id, "config.json", revision=revision, token=self.client.token)
        except EntryNotFoundError:
            return {}
        return json.loads(Path(path).read_text())


def default_source() -> HFSource:
    return HubSource()


# ---------------------------------------------------------------- architecture

EXPLAIN = {
    "model_type": "The family name transformers uses to pick the model code.",
    "architectures": "The class name that loads these weights; MAX matches on this.",
    "hidden_size": "Width of the residual stream: the size of every token's vector as it moves through the layers.",
    "num_hidden_layers": "How many transformer blocks are stacked. Depth.",
    "num_attention_heads": "Query heads per layer; each looks at the context in its own learned way.",
    "num_key_value_heads": "Key/value heads. Fewer than query heads means grouped-query attention (GQA): "
                           "query heads share K/V, which shrinks the KV cache.",
    "gqa_ratio": "Query heads per KV head. Higher means a smaller KV cache for the same context.",
    "head_dim": "Size of each attention head.",
    "intermediate_size": "Hidden width of the MLP (feed-forward) block, usually 3-4x hidden_size.",
    "vocab_size": "Number of tokens the model knows; sets the embedding and output matrix size.",
    "max_position_embeddings": "Longest context the model was trained to handle.",
    "rope_theta": "RoPE base frequency; larger values stretch positional encoding for longer contexts.",
    "rope_scaling": "Optional RoPE scaling (YaRN, linear, ...) used to extend context past training length.",
    "tie_word_embeddings": "True means the output matrix reuses the input embeddings, saving vocab x hidden parameters.",
}


def _fmt(n: float | int | None) -> str:
    if n is None:
        return "n/a"
    n = float(n)
    for unit, size in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if n >= size:
            return f"{n / size:.2f}{unit}"
    return f"{int(n)}"


def architecture_rows(cfg: dict[str, Any]) -> list[tuple[str, Any]]:
    """The config.json fields worth explaining, with the derived GQA ratio. Missing fields are skipped."""
    c = cfg.get("text_config") or cfg
    heads, kv = c.get("num_attention_heads"), c.get("num_key_value_heads")
    hidden = c.get("hidden_size")
    rope = c.get("rope_theta")
    rope_scaling = c.get("rope_scaling") or c.get("rope_parameters")
    if rope is None and isinstance(rope_scaling, dict):
        rope = rope_scaling.get("rope_theta")
    rows: list[tuple[str, Any]] = [
        ("model_type", cfg.get("model_type")),
        ("architectures", ", ".join(cfg.get("architectures") or []) or None),
        ("hidden_size", hidden),
        ("num_hidden_layers", c.get("num_hidden_layers")),
        ("num_attention_heads", heads),
        ("num_key_value_heads", kv),
    ]
    if heads and kv:
        rows.append(("gqa_ratio", f"{heads / kv:g}:1" if heads != kv else "1:1 (plain multi-head)"))
    head_dim = c.get("head_dim") or (hidden // heads if hidden and heads else None)
    rows += [
        ("head_dim", head_dim),
        ("intermediate_size", c.get("intermediate_size")),
        ("vocab_size", c.get("vocab_size")),
        ("max_position_embeddings", c.get("max_position_embeddings")),
        ("rope_theta", rope),
        ("rope_scaling", json.dumps(rope_scaling) if rope_scaling else None),
        ("tie_word_embeddings", c.get("tie_word_embeddings", cfg.get("tie_word_embeddings"))),
    ]
    return [(k, v) for k, v in rows if v is not None]


def parameter_breakdown(cfg: dict[str, Any]) -> dict[str, int] | None:
    """Rough dense-transformer parameter count; None when the config lacks the basics."""
    c = cfg.get("text_config") or cfg
    h, layers, heads = c.get("hidden_size"), c.get("num_hidden_layers"), c.get("num_attention_heads")
    vocab, inter = c.get("vocab_size"), c.get("intermediate_size")
    if not all((h, layers, heads, vocab, inter)):
        return None
    kv = c.get("num_key_value_heads") or heads
    hd = c.get("head_dim") or h // heads
    experts = c.get("num_local_experts") or c.get("num_experts") or 1
    mlp_width = (c.get("moe_intermediate_size") or inter) if experts > 1 else inter
    embed = vocab * h
    attn = h * heads * hd + 2 * h * kv * hd + heads * hd * h
    mlp = 3 * h * mlp_width * experts
    norms = 2 * h
    tied = bool(c.get("tie_word_embeddings", cfg.get("tie_word_embeddings", False)))
    lm_head = 0 if tied else vocab * h
    total = embed + layers * (attn + mlp + norms) + h + lm_head
    return {"embeddings": embed, "attention_per_layer": attn, "mlp_per_layer": mlp, "norms_per_layer": norms,
            "layers": layers, "lm_head": lm_head, "total": total, "experts": experts}


def _breakdown_md(cfg: dict[str, Any], actual: int | None) -> str:
    b = parameter_breakdown(cfg)
    if not b:
        return ("A parameter breakdown needs hidden size, layers, heads, vocab and intermediate size "
                "in config.json; not all are present.")
    n = b["layers"]
    experts = f" x {b['experts']} experts" if b["experts"] > 1 else ""
    head_note = "separate from embeddings" if b["lm_head"] else "tied to embeddings, no extra weights"
    lines = ["| part | parameters | note |", "|---|---|---|",
             f"| token embeddings | {_fmt(b['embeddings'])} | vocab x hidden |",
             f"| attention, per layer | {_fmt(b['attention_per_layer'])} | Q, K, V and output projections |",
             f"| MLP, per layer | {_fmt(b['mlp_per_layer'])} | gate, up and down projections{experts} |",
             f"| attention, all {n} layers | {_fmt(b['attention_per_layer'] * n)} | |",
             f"| MLP, all {n} layers | {_fmt(b['mlp_per_layer'] * n)} | |",
             f"| output head | {_fmt(b['lm_head'])} | {head_note} |",
             f"| **estimated total** | **{_fmt(b['total'])}** | ignores biases; norms included |"]
    if actual:
        diff = (b["total"] - actual) / actual * 100
        verdict = ("close enough to trust the table" if abs(diff) < 5
                   else "the estimate is off; this model has parts the formula does not know about")
        lines.append(f"| safetensors total (Hugging Face) | {_fmt(actual)} | estimate is {diff:+.1f}% off: {verdict} |")
    else:
        lines.append("| safetensors total | n/a | Hugging Face did not report a parameter count |")
    return "\n".join(lines)


# ---------------------------------------------------------------- MAX lookup

def find_max_implementation(architecture: str | None, root: Path | None = None) -> list[Path]:
    """Directories under max/pipelines/architectures whose source names this architecture."""
    if not architecture:
        return []
    if root is None:
        try:
            import max.pipelines.architectures as arch_pkg
            root = Path(list(arch_pkg.__path__)[0])
        except Exception:
            return []
    pat = re.compile(rf"\b{re.escape(architecture)}\b")
    hits = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        for f in d.glob("*.py"):
            try:
                if pat.search(f.read_text(errors="ignore")):
                    hits.append(d)
                    break
            except OSError:
                continue
    return hits


def _max_cell(architecture: str | None, finder: Callable[[str | None], list[Path]]) -> str:
    hits = finder(architecture)
    head = "## See inside: MAX's implementation\n\n"
    if not architecture:
        return head + "config.json names no architecture, so there is nothing to look up in MAX."
    if not hits:
        return head + (f"`{architecture}` was not found in the installed `max` package's pipelines architectures "
                       "directory. MAX may not support it yet; `mxc port` can help add it.")
    listing = "\n".join(f"- `{p}`" for p in hits)
    return head + (f"MAX's code for `{architecture}` is plain Python you can read and change:\n\n{listing}\n\n"
                   "Start with `arch.py` (registration) and the `model.py` / graph files beside it.")


# ---------------------------------------------------------------- notebook

def _trim(text: str, limit: int = CARD_LIMIT) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit("\n", 1)[0]
    return cut + "\n\n*(model card trimmed; the full card is on Hugging Face)*"


def _setup_code(model_id: str) -> str:
    return (
        "import os\n"
        f'BASE_URL = (os.environ.get("MXC_BASE_URL") or os.environ.get("HOTFIRE_BASE_URL")\n'
        f'            or os.environ.get("MST_BASE_URL") or "{daemon_url()}/v1")\n'
        'API_KEY = (os.environ.get("MXC_API_KEY") or os.environ.get("HOTFIRE_API_KEY") or os.environ["MST_API_KEY"])  # create one with: mxc keys new <app>\n'
        f'MODEL = "{model_id}"\n'
        "print(BASE_URL, MODEL)"
    )


OPENAI_CODE = '''from openai import OpenAI
client = OpenAI(base_url=BASE_URL, api_key=API_KEY)
reply = client.chat.completions.create(
    model=MODEL,
    messages=[{"role": "user", "content": "In one sentence, what are you?"}],
)
print(reply.choices[0].message.content)'''

STREAM_CODE = '''stream = client.chat.completions.create(
    model=MODEL,
    messages=[{"role": "user", "content": "Count from 1 to 5."}],
    stream=True,
)
for chunk in stream:
    delta = chunk.choices[0].delta.content if chunk.choices else None
    if delta:
        print(delta, end="", flush=True)
print()'''

HTTPX_CODE = '''import httpx
r = httpx.post(
    f"{BASE_URL}/chat/completions",
    headers={"Authorization": f"Bearer {API_KEY}"},
    json={"model": MODEL, "messages": [{"role": "user", "content": "Say hi."}]},
    timeout=120,
)
r.raise_for_status()
print(r.json()["choices"][0]["message"]["content"])'''


def build_notebook(*, model_id: str, repo_id: str, source: HFSource, revision: str | None = None, registered: bool = True,
                   max_finder: Callable[[str | None], list[Path]] = find_max_implementation):
    info = source.info(repo_id, revision)
    card = source.card_text(repo_id, revision)
    cfg = source.config(repo_id, revision)
    archs = cfg.get("architectures") or []
    arch = archs[0] if archs else None

    facts = [
        ("Pipeline", info.pipeline_tag),
        ("License", info.license),
        ("Parameters", _fmt(info.safetensors_total) if info.safetensors_total else None),
        ("Downloads", f"{info.downloads:,}" if info.downloads is not None else None),
        ("Likes", f"{info.likes:,}" if info.likes is not None else None),
        ("Revision", info.revision),
    ]
    facts_md = "\n".join(f"- **{k}:** {v}" for k, v in facts if v)

    cells = [
        new_markdown_cell(f"# {model_id}\n\n[`{repo_id}`](https://huggingface.co/{repo_id}) on Hugging Face. "
                          f"Everything below came from the Hugging Face API and was generated by `mxc learn`.\n\n"
                          f"## What it is\n\n{facts_md}"),
        new_markdown_cell("## Model card\n\n" + (_trim(card) if card.strip() else "*No model card text available.*")),
    ]

    rows = architecture_rows(cfg)
    if rows:
        table = "| field | value | what it means |\n|---|---|---|\n" + "\n".join(
            f"| `{k}` | {v} | {EXPLAIN.get(k, '')} |" for k, v in rows)
        cells.append(new_markdown_cell("## Architecture\n\nFrom `config.json`:\n\n" + table))
        cells.append(new_markdown_cell("## Where the parameters live\n\nA rough count from the config, checked "
                                       "against the real total from the safetensors metadata.\n\n"
                                       + _breakdown_md(cfg, info.safetensors_total)))
    else:
        cells.append(new_markdown_cell("## Architecture\n\nNo `config.json` was available from the Hugging Face API."))

    cells.append(new_markdown_cell(_max_cell(arch, max_finder)))
    cells += [
        new_markdown_cell("## Try it\n\nThese cells talk to your local `mxc` endpoint. Set `MXC_API_KEY` "
                          "(and optionally `MXC_BASE_URL`) in the environment before starting Jupyter."
                          + ("" if registered else f"\n\n**This model is not pulled yet:** run `mxc pull {model_id}` "
                             "first, or the endpoint will not know the name below.")),
        new_code_cell(_setup_code(model_id)),
        new_markdown_cell("### OpenAI client"),
        new_code_cell(OPENAI_CODE),
        new_markdown_cell("### Streaming"),
        new_code_cell(STREAM_CODE),
        new_markdown_cell("### Raw HTTP with httpx"),
        new_code_cell(HTTPX_CODE),
    ]
    nb = new_notebook(cells=cells, metadata={
        "kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"},
        "mxconsole": {"model": model_id, "repo_id": repo_id}})
    nbformat.validate(nb)
    return nb


def resolve(ref: str) -> tuple[str, str, str | None, bool]:
    """(name the gateway serves, repo id, revision, registered) for a model name, repo id or curated alias."""
    store = Store.open()
    m = store.get_model(ref) or next((x for x in store.models() if x.repo_id == ref), None)
    if m:
        return m.name, m.repo_id, m.revision, True
    repo_id = CURATED.get(ref.lower(), ref)
    m = next((x for x in store.models() if x.repo_id == repo_id), None)
    if m:
        return m.name, m.repo_id, m.revision, True
    return model_name(ref, repo_id), repo_id, None, False


def write_notebook(ref: str, source: HFSource | None = None,
                   max_finder: Callable[[str | None], list[Path]] = find_max_implementation) -> Path:
    name, repo_id, revision, registered = resolve(ref)
    nb = build_notebook(model_id=name, repo_id=repo_id, source=source or default_source(),
                        revision=revision, registered=registered, max_finder=max_finder)
    out = mxc_home() / "notebooks" / f"{name}.ipynb"
    out.parent.mkdir(parents=True, exist_ok=True)
    nbformat.write(nb, out)
    return out
