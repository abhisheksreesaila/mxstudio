"""Drop request fields `max serve` would refuse (F-063; LiteLLM's `drop_params` did this before).

MAX's OpenAI request models use pydantic `extra='forbid'`, so one field MAX doesn't know (a vendor
extra from a client written for another server) fails the whole request with a 4xx. The daemon
removes those top-level fields first and names them in the `X-Mxc-Dropped-Params` reply header.
What MAX accepts is read from MAX's own schema, so it follows the installed MAX version. Without
an importable MAX the body passes through untouched (and the daemon logs one warning).

Only top-level fields are dropped: an unknown field inside a message or tool still gets MAX's own
error. Fields that change what the answer means (NEVER_DROP) are never dropped, even where MAX
doesn't accept them: the client hears MAX's 4xx instead of silently getting a different answer.
"""
from __future__ import annotations

import functools
import logging
import re

log = logging.getLogger("mxconsole.params")
HEADER = "x-mxc-dropped-params"
NEVER_DROP = frozenset({"tools", "tool_choice", "functions", "function_call", "response_format", "logprobs",
                        "top_logprobs", "n", "seed", "stop"})
HEADER_NAME = re.compile(r"[A-Za-z0-9_.\-]{1,64}")  # what may be echoed back: client keys are untrusted
HEADER_MAX_NAMES = 20
_SCHEMAS = {"/v1/chat/completions": "CreateChatCompletionRequest", "/v1/completions": "CreateCompletionRequest",
            "/v1/embeddings": "CreateEmbeddingRequest"}
_warned = False


@functools.cache
def accepted(path: str) -> frozenset[str] | None:
    """The top-level fields MAX accepts on `path`, or None when MAX's schema can't be read."""
    global _warned
    try:
        from max.serve.schemas import openai as schemas
        model = getattr(schemas, _SCHEMAS[path])
        if model.model_config.get("extra") != "forbid":
            return None  # this MAX ignores unknown fields itself
        return frozenset(model.model_fields)
    except Exception:  # an older or moved MAX: pass the body through as before
        if not _warned:
            _warned = True
            log.warning("can't read MAX's request schema (max.serve.schemas.openai); unknown request fields "
                        "now pass through to max serve, which may refuse them", exc_info=True)
        return None


def drop_unknown(body: dict, path: str) -> tuple[dict, list[str]]:
    """`body` without the top-level fields MAX doesn't accept on `path` (never a NEVER_DROP one), and their names."""
    known = accepted(path)
    if known is None:
        return body, []
    dropped = sorted(k for k in body if k not in known and k not in NEVER_DROP)
    gone = set(dropped)
    return ({k: v for k, v in body.items() if k not in gone}, dropped) if dropped else (body, [])


def header_value(dropped: list[str]) -> str | None:
    """The reply header naming what was dropped: only safe names, at most HEADER_MAX_NAMES, then "+N more"."""
    if not dropped:
        return None
    shown = [k for k in dropped if HEADER_NAME.fullmatch(k)][:HEADER_MAX_NAMES]
    rest = len(dropped) - len(shown)
    return ",".join(shown + ([f"+{rest} more"] if rest else []))
