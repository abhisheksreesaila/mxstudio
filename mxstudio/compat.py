"""Modular MAX compatibility checks for Hugging Face model IDs."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from huggingface_hub import HfApi, hf_hub_download
from huggingface_hub.constants import HF_HUB_CACHE

# Architecture names seen in config.json that map to MAX pipelines.
# Sourced from `max list` / docs.modular.com/max/models — keep soft, not exhaustive.
_KNOWN_ARCH_PREFIXES = (
    "LlamaForCausalLM",
    "Llama4ForCausalLM",
    "MistralForCausalLM",
    "Mistral3ForConditionalGeneration",
    "Qwen2ForCausalLM",
    "Qwen2MoeForCausalLM",
    "Qwen3ForCausalLM",
    "Qwen3MoeForCausalLM",
    "Qwen2_5_VLForConditionalGeneration",
    "Qwen3VLForConditionalGeneration",
    "Gemma2ForCausalLM",
    "Gemma3ForCausalLM",
    "Gemma3ForConditionalGeneration",
    "Phi3ForCausalLM",
    "OlmoForCausalLM",
    "Olmo2ForCausalLM",
    "Olmo3ForCausalLM",
    "GraniteForCausalLM",
    "GptOssForCausalLM",
    "DeepseekV2ForCausalLM",
    "DeepseekV3ForCausalLM",
    "BertModel",
)


def env_status() -> dict:
    """What the user needs before a HF download can succeed."""
    token = (
        os.environ.get("HF_TOKEN")
        or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        or ""
    )
    cache = os.environ.get("HF_HUB_CACHE") or str(HF_HUB_CACHE)
    pixi = shutil.which("pixi") or shutil.which("max")
    free_gb = None
    try:
        usage = shutil.disk_usage(Path(cache).expanduser())
        free_gb = round(usage.free / 1e9, 1)
    except OSError:
        pass
    return {
        "hf_token_set": bool(token.strip()),
        "hf_token_hint": (
            "set" if token.strip() else "missing — gated models will fail; export HF_TOKEN"
        ),
        "hf_hub_cache": cache,
        "disk_free_gb": free_gb,
        "max_cli": bool(pixi),
        "ready": True,  # token optional for public models; UI warns when gated
        "notes": [
            "Public models download without a token.",
            "Gated/private models need HF_TOKEN or HUGGING_FACE_HUB_TOKEN.",
            "Models land under HF_HUB_CACHE (huggingface hub default if unset).",
        ],
    }


@lru_cache(maxsize=1)
def max_architectures() -> dict:
    """Parse `max list --json` when available; empty dict on failure."""
    candidates = []
    pixi = shutil.which("pixi")
    if pixi:
        candidates.append([pixi, "run", "max", "list", "--json"])
    max_bin = shutil.which("max")
    if max_bin:
        candidates.append([max_bin, "list", "--json"])

    for cmd in candidates:
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
            if proc.returncode != 0 or not proc.stdout.strip():
                continue
            data = json.loads(proc.stdout)
            arches = data.get("architectures") or data
            if isinstance(arches, dict):
                return arches
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            continue
    return {}


def _arch_supported(arch: str, max_arches: dict) -> bool:
    if not arch:
        return False
    if any(arch == p or arch.startswith(p) for p in _KNOWN_ARCH_PREFIXES):
        return True
    # max list keys are often short names like "Llama3", "Qwen2"
    lower = arch.lower()
    for key in max_arches:
        k = key.lower()
        if k in lower or lower.startswith(k) or arch.endswith(key):
            return True
    return False


def check_hf_model(model_id: str) -> dict:
    """Ask HF for metadata + config, then score Modular MAX compatibility."""
    model_id = model_id.strip().strip("/")
    if not model_id or "/" not in model_id:
        return {
            "ok": False,
            "model_id": model_id,
            "error": "expected org/name Hugging Face repo id (e.g. Qwen/Qwen2.5-0.5B-Instruct)",
        }

    env = env_status()
    api = HfApi(token=os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN"))
    try:
        info = api.model_info(model_id, files_metadata=True)
    except Exception as exc:
        return {
            "ok": False,
            "model_id": model_id,
            "error": f"Hugging Face lookup failed: {exc}",
            "env": env,
            "hint": (
                "If this model is gated, set HF_TOKEN and accept the license on huggingface.co"
            ),
        }

    gated = bool(getattr(info, "gated", False))
    siblings = info.siblings or []
    has_st = any(
        (s.rfilename or "").endswith(".safetensors") for s in siblings
    )
    size_bytes = sum(s.size or 0 for s in siblings if s.size)

    architecture = None
    config_ok = False
    try:
        cfg_path = hf_hub_download(model_id, "config.json")
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)
        archs = cfg.get("architectures") or []
        architecture = archs[0] if archs else cfg.get("model_type")
        config_ok = True
    except Exception as exc:
        cfg_error = str(exc)[:240]
    else:
        cfg_error = None

    max_arches = max_architectures()
    modular_compatible = bool(
        architecture and _arch_supported(str(architecture), max_arches)
    )

    warnings: list[str] = []
    if gated and not env["hf_token_set"]:
        warnings.append("Model is gated and HF_TOKEN is not set.")
    if not has_st:
        warnings.append("No .safetensors weights found — MAX prefers safetensors.")
    if not modular_compatible:
        warnings.append(
            f"Architecture '{architecture}' is not in the known MAX list — "
            "serve may fail. See https://docs.modular.com/max/models/"
        )
    if env["disk_free_gb"] is not None and size_bytes and env["disk_free_gb"] < size_bytes / 1e9 * 1.2:
        warnings.append(
            f"Disk may be tight: model ~{size_bytes / 1e9:.1f} GB, free {env['disk_free_gb']} GB."
        )

    return {
        "ok": True,
        "model_id": model_id,
        "name": model_id.split("/")[-1],
        "architecture": architecture,
        "config_ok": config_ok,
        "config_error": cfg_error,
        "gated": gated,
        "has_safetensors": has_st,
        "approx_gb": round(size_bytes / 1e9, 2) if size_bytes else None,
        "modular_compatible": modular_compatible,
        "max_list_available": bool(max_arches),
        "env": env,
        "warnings": warnings,
        "can_download": has_st and (not gated or env["hf_token_set"]),
        "handbook": "https://handbook.modular.com/",
        "models_docs": "https://docs.modular.com/max/models/",
    }
