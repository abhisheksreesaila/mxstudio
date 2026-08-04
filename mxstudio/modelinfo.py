"""Fetch and summarize Hugging Face model cards for educational notebooks."""

from __future__ import annotations

import re
from pathlib import Path

from huggingface_hub import hf_hub_download


def get_model_card(model_id: str) -> str | None:
    path: str | None = None
    try:
        path = hf_hub_download(model_id, "README.md", local_files_only=True)
    except Exception:
        try:
            path = hf_hub_download(model_id, "README.md")
        except Exception:
            return None
    text = Path(path).read_text(errors="replace")
    # Strip YAML frontmatter that HF model cards start with.
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4 :]
    return text.strip()


def _split_sections(md: str) -> list[tuple[str, str]]:
    """Return (heading, body) pairs. Leading text before first heading is ('', body)."""
    parts = re.split(r"(?m)^(#{1,3}\s+.+)$", md)
    sections: list[tuple[str, str]] = []
    if parts and parts[0].strip():
        sections.append(("", parts[0].strip()))
    i = 1
    while i + 1 < len(parts):
        heading = parts[i].strip()
        body = parts[i + 1].strip()
        sections.append((heading, body))
        i += 2
    return sections


def _trim(text: str, max_chars: int) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    cut = text[: max_chars - 1]
    # Prefer ending on a paragraph boundary.
    for sep in ("\n\n", "\n", ". "):
        pos = cut.rfind(sep)
        if pos > max_chars // 3:
            return cut[: pos + (1 if sep == ". " else 0)].rstrip() + "…"
    return cut.rstrip() + "…"


# Prefer these sections when present on HF model cards.
_PREFERRED = (
    "introduction",
    "model details",
    "model description",
    "about",
    "overview",
    "summary",
    "intended uses",
    "intended use",
    "uses",
    "limitations",
    "training details",
    "training data",
    "evaluation",
    "benchmarks",
    "how to use",
    "quickstart",
    "citation",
)


def summarize_model_card(model_id: str, max_chars: int = 3500) -> str | None:
    """Build a compact, notebook-friendly excerpt of the HF model card."""
    card = get_model_card(model_id)
    if not card:
        return None

    sections = _split_sections(card)
    if not sections:
        return _trim(card, max_chars)

    # Score sections: preferred headings first, then everything else (skip giant tables).
    scored: list[tuple[int, str, str]] = []
    for heading, body in sections:
        h = re.sub(r"^#+\s*", "", heading).strip().lower()
        # Skip noisy / redundant blocks.
        if any(x in h for x in ("chat template", "license file", "files info")):
            continue
        # Drop HTML-heavy blocks and huge tables.
        clean = re.sub(r"<[^>]+>", "", body)
        clean = re.sub(r"\|.+\|\n\|[-:| ]+\|\n(?:\|.+\|\n?)+", "[table omitted]\n", clean)
        clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
        if len(clean) < 40:
            continue
        rank = 50
        for i, key in enumerate(_PREFERRED):
            if key in h or (not h and i == 0):
                rank = i
                break
        if not h:
            rank = 0
        scored.append((rank, heading, clean))

    scored.sort(key=lambda t: t[0])
    chunks: list[str] = []
    total = 0
    for _, heading, body in scored:
        piece = _trim(body, 900)
        block = f"{heading}\n\n{piece}" if heading else piece
        if total + len(block) > max_chars:
            remain = max_chars - total
            if remain > 200:
                chunks.append(_trim(block, remain))
            break
        chunks.append(block)
        total += len(block) + 2

    if not chunks:
        return _trim(card, max_chars)
    return "\n\n".join(chunks)
