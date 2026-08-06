"""Build a Reveal.js-friendly slide deck that mirrors the model details drawer.

Decks are generated during the notebook background job (and rebuilt when the
schema version advances). They favor story, fit guidance, animated architecture
steps, and a small code walkthrough — not a dump of raw specs.
"""

from __future__ import annotations

import json
import textwrap
import uuid
from pathlib import Path

from notebooks.mxstudio import state
from notebooks.mxstudio import compat, registry

# Bump when the deck narrative changes so stale sidecars rebuild on open.
SCHEMA_VERSION = 4


def slides_path(notebook_path: Path) -> Path:
    """Return the sidecar path used for a notebook's cached deck."""
    return notebook_path.with_suffix(".slides.json")


def _is_moe(entry: registry.ModelEntry) -> bool:
    text = f"{entry.params} {entry.architecture} {entry.family} {entry.name}".lower()
    return "moe" in text or "mixture" in text


def model_persona(entry: registry.ModelEntry) -> dict:
    """Plain-language persona matching the model details Overview tab."""
    if _is_moe(entry):
        caution = (
            entry.serve_disabled_reason
            if not entry.serve_enabled and entry.serve_disabled_reason
            else "Simple demos where a smaller dense model starts faster."
        )
        return {
            "icon": "experts",
            "title": "An expert relay",
            "summary": (
                "A router activates only the specialists needed for each token—"
                "more knowledge without using every parameter at once."
            ),
            "best": entry.when_to_use
            or "Learning expert routing, sparse inference, and systems ideas such as expert caching.",
            "caution": caution,
        }
    if entry.approx_gb and entry.approx_gb <= 2:
        return {
            "icon": "pocket",
            "title": "A pocket laboratory",
            "summary": (
                "Small enough to experiment freely, fast enough to keep the "
                "feedback loop playful."
            ),
            "best": entry.when_to_use
            or "Local API experiments, notebooks, UI demos, and learning how inference behaves.",
            "caution": (
                "Difficult multi-step reasoning, demanding coding, or tasks "
                "where factual depth matters most."
            ),
        }
    if entry.approx_gb and entry.approx_gb <= 8:
        return {
            "icon": "workbench",
            "title": "A capable local workbench",
            "summary": (
                "A practical balance between responsiveness and useful "
                "instruction-following on local hardware."
            ),
            "best": entry.when_to_use
            or "Everyday local prompting, prototypes, and richer notebook experiments.",
            "caution": "Very large-context workloads or frontier-level reasoning expectations.",
        }
    return {
        "icon": "heavyweight",
        "title": "A local heavyweight",
        "summary": (
            "Built for deeper capability, with a larger memory and startup "
            "footprint to match."
        ),
        "best": entry.when_to_use
        or "Tasks that benefit from more capacity and can justify a longer startup.",
        "caution": "Quick smoke tests or hardware without comfortable VRAM headroom.",
    }


def fit_guidance(entry: registry.ModelEntry, persona: dict) -> tuple[str, str]:
    """Split publisher guidance into Great for / Think twice when possible."""
    text = (entry.when_to_use or "").strip()
    for marker in ("Do not ", "Don't ", "Skip if ", "Avoid "):
        idx = text.find(marker)
        if idx > 24:
            return text[:idx].strip(), text[idx:].strip()
    return str(persona["best"]).strip(), str(persona["caution"]).strip()


def architecture_flow(entry: registry.ModelEntry) -> dict:
    """Text flow diagram used by the How it works slide."""
    if _is_moe(entry):
        return {
            "nodes": ["Prompt", "Router", "Experts", "Next token"],
            "accent": "Router",
            "caption": (
                "The router chooses a small team of experts for each token, "
                "then merges their work."
            ),
            "steps": [
                {"label": "Prompt", "text": "Tokens enter the model one sequence at a time."},
                {"label": "Router", "text": "A gate scores experts and picks the top few."},
                {"label": "Experts", "text": "Only the chosen MLPs run — the rest stay cold."},
                {"label": "Next token", "text": "Outputs combine; decoding continues."},
            ],
        }
    return {
        "nodes": ["Prompt", "Attention", "MLP", "Next token"],
        "accent": "Attention",
        "caption": (
            "Attention gathers context; the feed-forward layers transform it; "
            "the cycle repeats one token at a time."
        ),
        "steps": [
            {"label": "Prompt", "text": "Your chat messages become token ids."},
            {"label": "Attention", "text": "Each token looks back at useful context."},
            {"label": "MLP", "text": "A dense feed-forward block transforms the state."},
            {"label": "Next token", "text": "Sample / argmax → stream the next piece of text."},
        ],
    }


def _chat_walkthrough(entry: registry.ModelEntry) -> dict:
    code = textwrap.dedent(
        f"""\
        from openai import OpenAI

        client = OpenAI(
            base_url="http://127.0.0.1:8100/v1",
            api_key="local",  # ignored by max serve
        )
        stream = client.chat.completions.create(
            model="{entry.id}",
            messages=[{{"role": "user", "content": "Explain this model in one sentence."}}],
            stream=True,
            max_tokens=64,
        )
        for event in stream:
            delta = event.choices[0].delta.content or ""
            print(delta, end="", flush=True)
        """
    )
    return {
        "kind": "walkthrough",
        "kicker": "Code walkthrough",
        "title": "Talk to the local server",
        "body": (
            "After Serve shows running, the notebook uses the same OpenAI-compatible "
            "API that Chat uses — no cloud round-trip."
        ),
        "code": code,
        "code_lang": "python",
        "steps": [
            {
                "label": "Point the client",
                "text": "base_url is mxstudio's max serve port (8100).",
            },
            {
                "label": "Send one chat turn",
                "text": "Use the Hugging Face id as the model name.",
            },
            {
                "label": "Stream tokens",
                "text": "Each event carries a tiny delta — that is generation you can feel.",
            },
        ],
        "fragments": True,
        "animate": True,
    }


def _token_story(entry: registry.ModelEntry) -> dict:
    if _is_moe(entry):
        bullets = [
            "Most parameters stay idle; only routed experts burn FLOPs",
            "That is why MoE can feel 'bigger than it runs'",
            "Systems work (batching, expert placement) matters as much as the paper math",
        ]
        title = "One token through the experts"
        body = (
            "Follow a single generated token: the router picks specialists, "
            "those experts run, then logits become the next piece of text."
        )
    else:
        bullets = [
            "Prefill reads the prompt once; decode emits one token at a time",
            "KV cache remembers attention state so decode stays cheap",
            "Temperature / top_p only change the final sampling step",
        ]
        title = "One token through the stack"
        body = (
            "Generation is a loop: build context, run the transformer block stack, "
            "sample the next token, append, repeat."
        )
    return {
        "kind": "story",
        "kicker": "Mental model",
        "title": title,
        "body": body,
        "bullets": bullets,
        "fragments": True,
        "animate": True,
    }


def _parked_slide(entry: registry.ModelEntry) -> dict | None:
    if entry.serve_enabled:
        return None
    return {
        "kind": "parked",
        "kicker": "Serve status",
        "title": "Parked — learn, don't serve",
        "body": entry.serve_disabled_reason
        or "Serving is disabled until a complete MAX custom architecture exists.",
        "bullets": [
            "Use this lab to study the architecture and paper ideas",
            "For a runnable MoE on MAX, switch to the Qwen3-30B-A3B FP8 target",
            "The Architectures wizard can scaffold ports/ — it cannot finish the graph",
        ],
        "fragments": True,
        "animate": True,
    }


def _local_evidence_slide(entry: registry.ModelEntry) -> dict:
    """Summarize measured local fit without pretending estimates are guarantees."""
    try:
        fit = compat.hardware_fit(entry.approx_gb, cuda_devices=entry.cuda_devices)
    except Exception as exc:
        fit = {"level": "unknown", "message": f"Hardware fit unavailable: {exc}"}

    history = state.load().get("bench_history", [])
    latest = next(
        (item for item in reversed(history) if item.get("model_id") == entry.id),
        None,
    )
    fit_tone = {
        "ok": "good",
        "tight": "caution",
        "insufficient": "caution",
    }.get(fit.get("level"), "")
    cards = [
        {
            "tone": fit_tone,
            "label": "Estimated VRAM fit",
            "text": fit.get("message") or "No hardware estimate is available yet.",
        }
    ]
    summary = (latest or {}).get("summary") or {}
    tok_s = summary.get("mean_tok_per_s")
    if tok_s is not None:
        ttft = summary.get("mean_ttft_s")
        backend = (latest or {}).get("backend") or summary.get("backend") or "local benchmark"
        detail = f"{tok_s} tok/s"
        if ttft is not None:
            detail += f" · {ttft}s TTFT"
        cards.append(
            {
                "tone": "good",
                "label": "Measured here",
                "text": f"{detail} via {backend.replace('_', ' ')}.",
            }
        )
    else:
        cards.append(
            {
                "tone": "",
                "label": "Next measurement",
                "text": "Serve the model, then run Benchmark to replace estimates with local evidence.",
            }
        )
    return {
        "kind": "evidence",
        "kicker": "Local evidence",
        "title": "This model on this machine",
        "body": "An estimate helps you choose; a local benchmark tells you what users will feel.",
        "cards": cards,
        "animate": True,
    }


def _custom_deck(model_id: str) -> dict:
    name = model_id.split("/")[-1]
    code = textwrap.dedent(
        f"""\
        from openai import OpenAI
        client = OpenAI(base_url="http://127.0.0.1:8100/v1", api_key="local")
        print(client.models.list())
        # Serve only after MAX compatibility looks good.
        """
    )
    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "model_id": model_id,
        "title": name,
        "hf_url": f"https://huggingface.co/{model_id}",
        "slides": [
            {
                "kind": "title",
                "kicker": "Custom model lab",
                "title": name,
                "body": (
                    f"Custom Hugging Face model `{model_id}`. "
                    "Start from the publisher card, then smoke-test locally."
                ),
                "bullets": [
                    "Open the HF card for architecture details",
                    "Check MAX compatibility before serving",
                    "Create a lab only after a quick card skim",
                ],
                "fragments": True,
            },
            {
                "kind": "persona",
                "kicker": "The short version",
                "title": "An uncurated guest",
                "body": (
                    "This model is not in the curated registry yet, so the deck stays "
                    "honest: publisher notes first, then a careful local trial."
                ),
                "persona": "workbench",
                "bullets": [
                    "Great for: exploring a new HF id in a safe sandbox",
                    "Think twice for: assuming Modular MAX support without a check",
                ],
                "fragments": True,
            },
            {
                "kind": "walkthrough",
                "kicker": "Code walkthrough",
                "title": "Probe the local endpoint",
                "body": "Once a supported model is serving, list models and try a tiny completion.",
                "code": code,
                "code_lang": "python",
                "steps": [
                    {"label": "List models", "text": "Confirms max serve is up on :8100."},
                    {"label": "Chat small", "text": "Keep max_tokens low while you learn the loop."},
                ],
                "fragments": True,
                "animate": True,
            },
            {
                "kind": "sources",
                "kicker": "Sources",
                "title": "Hugging Face first",
                "body": "Prefer the model card and config.json as primary sources.",
                "links": {"Hugging Face": f"https://huggingface.co/{model_id}"},
            },
        ],
    }


def build_slides(model_id: str) -> dict:
    entry = registry.get(model_id)
    if entry is None:
        return _custom_deck(model_id)

    persona = model_persona(entry)
    great_for, think_twice = fit_guidance(entry, persona)
    flow = architecture_flow(entry)
    bullets_ideas = list(entry.key_ideas)[:5] or [entry.innovation[:160]]
    bullets_goals = list(entry.learning_goals)[:4]
    parked = _parked_slide(entry)

    slides: list[dict] = [
        {
            "kind": "title",
            "kicker": entry.family or "Model lab",
            "title": entry.name,
            "body": entry.description,
            "bullets": [
                f"{entry.params} · ~{entry.approx_gb} GB · ctx {entry.context:,}",
                f"License: {entry.license}",
            ],
            "fragments": True,
            "animate": True,
        },
        {
            "kind": "persona",
            "kicker": "The short version",
            "title": persona["title"],
            "body": persona["summary"],
            "persona": persona["icon"],
            "bullets": [
                f"{entry.params} on ~{entry.approx_gb} GB",
                f"{entry.context:,}-token context window",
            ],
            "fragments": True,
            "animate": True,
        },
        {
            "kind": "fit",
            "kicker": "Fit",
            "title": "When to pick it",
            "body": "Same guidance as the model details drawer — pick it for the right job.",
            "cards": [
                {"tone": "good", "label": "Great for", "text": great_for},
                {"tone": "caution", "label": "Think twice for", "text": think_twice},
            ],
            "animate": True,
        },
        _local_evidence_slide(entry),
    ]

    if parked:
        slides.append(parked)

    slides.extend(
        [
            {
                "kind": "innovation",
                "kicker": "Why it matters",
                "title": "What's special?",
                "body": entry.innovation,
                "bullets": bullets_ideas,
                "fragments": True,
                "animate": True,
            },
            {
                "kind": "architecture",
                "kicker": "How it works",
                "title": "Inside the model",
                "body": entry.architecture or "See the Hugging Face model card.",
                "flow": flow,
                "fragments": True,
                "animate": True,
            },
            _token_story(entry),
            _chat_walkthrough(entry),
            {
                "kind": "goals",
                "kicker": "Learn",
                "title": "Lab goals",
                "body": "Finish the notebook when you can explain these in your own words.",
                "bullets": bullets_goals,
                "fragments": True,
                "animate": True,
            },
            {
                "kind": "sources",
                "kicker": "Sources",
                "title": "Cite & go deeper",
                "body": "Hugging Face is the primary source; papers are secondary.",
                "links": dict(entry.links),
                "animate": True,
            },
        ]
    )

    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "model_id": entry.id,
        "title": entry.name,
        "hf_url": f"https://huggingface.co/{entry.id}",
        "persona": {
            "icon": persona["icon"],
            "title": persona["title"],
            "summary": persona["summary"],
        },
        "slides": slides,
    }


def write_slides(model_id: str, notebook_path: Path) -> Path:
    """Build and atomically cache a Reveal-compatible JSON sidecar."""
    path = slides_path(notebook_path)
    payload = build_slides(model_id)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    temporary.replace(path)
    return path


def _cache_is_current(payload: dict, model_id: str) -> bool:
    if payload.get("model_id") != model_id:
        return False
    if not isinstance(payload.get("slides"), list) or not payload["slides"]:
        return False
    version = int(payload.get("schema_version") or 0)
    return version >= SCHEMA_VERSION


def load_slides(notebook_path: Path, model_id: str, *, rebuild: bool = True) -> dict:
    """Load a valid sidecar, rebuilding corrupt/missing/stale caches when requested."""
    path = slides_path(notebook_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not _cache_is_current(payload, model_id):
            raise ValueError("stale or invalid slide cache")
        return {**payload, "cached": True, "artifact_path": str(path)}
    except (FileNotFoundError, json.JSONDecodeError, ValueError, OSError, TypeError):
        if not rebuild:
            raise FileNotFoundError(path)
        write_slides(model_id, notebook_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        return {**payload, "cached": False, "artifact_path": str(path)}
