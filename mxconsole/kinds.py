"""What a model is for (its kind) and what you may call a label. Pure functions; no network, no store."""
from __future__ import annotations

import re

KINDS = ("chat", "coder", "vision", "embeddings", "image")
MAX_LABEL = 40
LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?")

_TASKS = {"image-text-to-text": "vision", "image-to-text": "vision", "visual-question-answering": "vision",
          "feature-extraction": "embeddings", "sentence-similarity": "embeddings", "text-to-image": "image"}
_NAME_HINTS = (  # for a model with no card task: the name and architecture say it
    ("coder", re.compile(r"(?<![a-z])(code|coder|codestral|starcoder|codellama|codegemma)(?![a-z])")),
    ("vision", re.compile(r"(?<![a-z])(vl|vision|llava|paligemma|pixtral)(?![a-z])|vl[a-z]*forconditionalgeneration")),
    ("embeddings", re.compile(r"(?<![a-z])(embed|embedding|embeddings|bge|e5|minilm|gte)(?![a-z])|robertamodel$|bertmodel$")),
    ("image", re.compile(r"diffusion|(?<![a-z])flux(?![a-z])")),
)


class LabelError(ValueError):
    """A refused label or kind change. `.code` is the admin API's error code."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def check_label(name: str) -> str:
    if not isinstance(name, str) or len(name) > MAX_LABEL or not LABEL.fullmatch(name):
        raise LabelError(f"label names are lower-case letters, digits and hyphens, up to {MAX_LABEL} characters "
                         f"(like `work` or `my-label`); got {name!r}", "invalid_label")
    return name


def check_kind(kind: str) -> str:
    if kind not in KINDS:
        raise LabelError(f"kind must be one of: {', '.join(KINDS)}; got {kind!r}", "invalid_kind")
    return kind


def derive_kind(*, name: str, architecture: str = "", pipeline_tag: str | None = None, tags=()) -> str:
    """The card's task wins; text-generation is chat unless the name or tags say code. With no task, guess
    from the name and architecture, and fall back to chat."""
    text = f"{name} {architecture}".lower()
    if pipeline_tag in _TASKS:
        return _TASKS[pipeline_tag]
    coder = _NAME_HINTS[0][1].search(text) or any(t in ("code", "coding") for t in tags)
    if pipeline_tag == "text-generation":
        return "coder" if coder else "chat"
    if pipeline_tag is None:
        return next((kind for kind, rx in _NAME_HINTS if rx.search(text)), "chat")
    return "chat"
