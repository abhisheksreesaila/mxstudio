"""Drop request fields `max serve` would refuse (F-063; LiteLLM's `drop_params` did this before).

MAX's OpenAI request models use pydantic `extra='forbid'`, so one field MAX doesn't know (a vendor
extra from a client written for another server) fails the whole request with a 4xx. The daemon
removes those top-level fields first and names them in the `X-Mxc-Dropped-Params` reply header.
What MAX accepts is read from MAX's own schema, so it follows the installed MAX version. Without
an importable MAX the body passes through untouched.
"""
from __future__ import annotations

import functools
import logging

log = logging.getLogger("mxconsole.params")
HEADER = "x-mxc-dropped-params"
_SCHEMAS = {"/v1/chat/completions": "CreateChatCompletionRequest", "/v1/completions": "CreateCompletionRequest",
            "/v1/embeddings": "CreateEmbeddingRequest"}


@functools.cache
def accepted(path: str) -> frozenset[str] | None:
    """The top-level fields MAX accepts on `path`, or None when MAX's schema can't be read."""
    try:
        from max.serve.schemas import openai as schemas
        model = getattr(schemas, _SCHEMAS[path])
        if model.model_config.get("extra") != "forbid":
            return None  # this MAX ignores unknown fields itself
        return frozenset(model.model_fields)
    except Exception:  # an older or missing MAX: pass the body through as before
        log.info("can't read MAX's request schema for %s; unknown fields pass through", path, exc_info=True)
        return None


def drop_unknown(body: dict, path: str) -> tuple[dict, list[str]]:
    """`body` without the top-level fields MAX doesn't accept on `path`, and their names (sorted)."""
    known = accepted(path)
    if known is None:
        return body, []
    dropped = sorted(k for k in body if k not in known)
    return ({k: v for k, v in body.items() if k in known}, dropped) if dropped else (body, [])
