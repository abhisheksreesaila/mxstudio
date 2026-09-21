"""Generate an editable, local Nucleus TTFT lesson.

Generation is deterministic and never contacts a model.  The generated timing
cells make requests only when a learner explicitly executes them.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


SOURCES = (
    ("Nucleus contract", "docs/MXSTUDIO_NUCLEUS.md"),
    ("OpenAI chat completions", "https://platform.openai.com/docs/api-reference/chat"),
    ("MAX serving", "https://docs.modular.com/max/serve"),
)


def _markdown(text: str) -> dict[str, Any]:
    return {"cell_type": "markdown", "metadata": {}, "source": text}


def _code(text: str) -> dict[str, Any]:
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": text,
    }


def build_lesson(endpoint: str = "http://127.0.0.1:8080") -> dict[str, Any]:
    """Return a stable notebook teaching cold versus warm TTFT."""
    source_lines = "\n".join(f"- [{name}]({url})" for name, url in SOURCES)
    cells = [
        _markdown(
            "# Nucleus: first-request and warm time to first token\n\n"
            "Learning question: how does prewarming change the first-token "
            "latency of the same local endpoint? This lesson records measurements "
            "only when you execute the request cells; it contains no precomputed "
            "or claimed measurements.\n\n## Sources\n" + source_lines
        ),
        _code(
            "import json, os, time\n"
            "from urllib import request\n\n"
            f"ENDPOINT = os.environ.get('NUCLEUS_ENDPOINT', {endpoint!r})\n"
            "API_KEY = os.environ['MXSTUDIO_NUCLEUS_API_KEY']\n"
            "MODEL = os.environ.get('NUCLEUS_MODEL_ID', 'nucleus-local')\n"
            "PROMPT = 'Reply with one short sentence about local inference.'\n"
            "MAX_TOKENS = 64\n"
            "TEMPERATURE = 0.0\n"
            "import platform\n"
            "from importlib import metadata\n"
            "try:\n"
            "    max_version = metadata.version('modular')\n"
            "except metadata.PackageNotFoundError:\n"
            "    max_version = 'unknown: record the serving environment version manually'\n"
            "CONTEXT = {'platform': platform.platform(), 'max_version': max_version,\n"
            "           'hardware_notes': 'Record CPU/GPU and available memory here',\n"
            "           'server_state': 'Record whether MAX was already ready'}\n"
        ),
        _code(
            "def measure_ttft():\n"
            "    body = json.dumps({'model': MODEL, 'messages': "
            "[{'role': 'user', 'content': PROMPT}], 'stream': True,\n"
            "        'max_tokens': MAX_TOKENS, 'temperature': TEMPERATURE}).encode()\n"
            "    req = request.Request(ENDPOINT + '/v1/chat/completions', data=body,\n"
            "        headers={'Authorization': 'Bearer ' + API_KEY, "
            "'Content-Type': 'application/json'})\n"
            "    started = time.perf_counter()\n"
            "    first_token_at = None\n"
            "    raw_events = []\n"
            "    with request.urlopen(req, timeout=120) as response:\n"
            "        for raw in response:\n"
            "            line = raw.decode('utf-8', 'replace').strip()\n"
            "            if not line.startswith('data:'):\n"
            "                continue\n"
            "            payload = line[5:].strip()\n"
            "            if payload == '[DONE]':\n"
            "                continue\n"
            "            try:\n"
            "                event = json.loads(payload)\n"
            "            except json.JSONDecodeError:\n"
            "                continue\n"
            "            raw_events.append(event)\n"
            "            for choice in event.get('choices', []):\n"
            "                text = choice.get('delta', {}).get('content', '')\n"
            "                if text and first_token_at is None:\n"
            "                    first_token_at = time.perf_counter()\n"
            "    if first_token_at is None:\n"
            "        raise RuntimeError('stream ended without a non-empty token')\n"
            "    finished = time.perf_counter()\n"
            "    return {'ttft_seconds': first_token_at - started,\n"
            "            'total_seconds': finished - started, 'raw_events': raw_events,\n"
            "            'request': json.loads(body), 'context': dict(CONTEXT)}\n"
        ),
        _markdown(
            "## First-request measurement\n\n"
            "The service lifespan prewarms the model, so this is not a cold "
            "start measurement. To study an unprepared model, launch Nucleus "
            "with `--no-prewarm` and deliberately stop MAX using `mxs stop` "
            "only when no clients are active. The first request waits at most "
            "30 seconds for readiness; a `503` is not a TTFT sample. Use "
            "`mxs nucleus prepare` for longer preparations. Cached weights and "
            "compiled graphs mean even this is not proof of a fully cold compile. "
            "Record the actual starting state in CONTEXT."
        ),
        _code("first_request = measure_ttft()\nfirst_request"),
        _markdown(
            "## Warm measurement\n\n"
            "Run the same request again. Compare the two returned dictionaries "
            "and record hardware, model, prompt, and service state alongside any "
            "conclusion."
        ),
        _code("warm = measure_ttft()\nwarm"),
        _markdown(
            "## Edit and compare\n\nChange MAX_TOKENS from 64 to 128 and rerun "
            "both measurements with the same model and prompt. Compare TTFT and "
            "total duration separately; these two samples are not a statistical "
            "benchmark. Save the notebook outputs as local evidence. Raw events "
            "contain generated text; review privacy before sharing. The API key "
            "is never included in evidence."
        ),
    ]
    for index, cell in enumerate(cells):
        cell["id"] = f"nucleus-{index}"
    return {
        "cells": cells,
        "metadata": {"kernelspec": {"display_name": "Python 3", "name": "python3"}},
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def write_lesson(path: str | os.PathLike[str]) -> Path:
    """Write the deterministic lesson without executing any cell."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(build_lesson(), indent=2) + "\n")
    return destination
