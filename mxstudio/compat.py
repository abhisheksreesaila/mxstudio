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

from mxstudio import hwinfo, settings as studio_settings

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
    token = studio_settings.get_hf_token()
    cache = os.environ.get("HF_HUB_CACHE") or str(HF_HUB_CACHE)
    pixi = shutil.which("pixi") or shutil.which("max")
    free_gb = None
    try:
        usage = shutil.disk_usage(Path(cache).expanduser())
        free_gb = round(usage.free / 1e9, 1)
    except OSError:
        pass
    pub = studio_settings.public_settings()
    return {
        "hf_token_set": bool(token.strip()),
        "hf_token_hint": (
            "set" if token.strip() else "missing — gated models will fail; set token in Settings"
        ),
        "hf_token_masked": pub.get("hf_token_masked", ""),
        "hf_token_source": pub.get("hf_token_source", "none"),
        "hf_hub_cache": cache,
        "disk_free_gb": free_gb,
        "max_cli": bool(pixi),
        "ready": True,  # token optional for public models; UI warns when gated
        "notes": [
            "Public models download without a token.",
            "Gated/private models need an HF token (Settings tab or HF_TOKEN env).",
            "Models land under HF_HUB_CACHE (huggingface hub default if unset).",
            "Use Settings to save a token without touching the shell.",
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


def hardware_fit(approx_gb: float | None) -> dict:
    """Compare weight size to local GPU VRAM / host RAM."""
    gpus = hwinfo.gpus()
    mem = hwinfo.memory()
    total_vram_gb = (
        round(sum(g["vram_total_mb"] for g in gpus) / 1024, 2) if gpus else 0.0
    )
    free_vram_gb = (
        round(sum(g["vram_total_mb"] - g["vram_used_mb"] for g in gpus) / 1024, 2)
        if gpus
        else 0.0
    )
    # Weights + KV/activations headroom — rough rule of thumb for serve.
    need_gb = round(approx_gb * 1.25, 2) if approx_gb else None

    if not gpus:
        level = "unknown"
        message = "No NVIDIA GPU detected via nvidia-smi — cannot estimate VRAM fit."
    elif need_gb is None:
        level = "unknown"
        message = "Model size unknown — VRAM fit not estimated."
    elif need_gb <= free_vram_gb:
        level = "ok"
        message = (
            f"Fits comfortably: need ~{need_gb} GB (weights+headroom), "
            f"{free_vram_gb} GB VRAM free of {total_vram_gb} GB total."
        )
    elif need_gb <= total_vram_gb:
        level = "tight"
        message = (
            f"Tight fit: need ~{need_gb} GB but only {free_vram_gb} GB free "
            f"({total_vram_gb} GB total). Stop other GPU jobs first."
        )
    elif need_gb <= (total_vram_gb + mem.get("available_gb", 0) * 0.5):
        level = "slow"
        message = (
            f"Likely needs host offload / will be extremely slow: need ~{need_gb} GB, "
            f"only {total_vram_gb} GB VRAM. Use at your own risk."
        )
    else:
        level = "insufficient"
        message = (
            f"Not recommended: need ~{need_gb} GB but system has "
            f"{total_vram_gb} GB VRAM + {mem.get('available_gb')} GB RAM available. "
            "Download only if you accept very slow or failing serve."
        )

    return {
        "approx_gb": approx_gb,
        "estimated_need_gb": need_gb,
        "total_vram_gb": total_vram_gb,
        "free_vram_gb": free_vram_gb,
        "ram_available_gb": mem.get("available_gb"),
        "ram_total_gb": mem.get("total_gb"),
        "gpu_count": len(gpus),
        "gpus": [{"name": g["name"], "vram_total_gb": round(g["vram_total_mb"] / 1024, 1)} for g in gpus],
        "level": level,
        "message": message,
        "recommend_download": level in ("ok", "tight", "unknown"),
    }


def bench_readiness(approx_gb: float | None = None) -> dict:
    """Whether this machine looks conducive for a meaningful max benchmark."""
    fit = hardware_fit(approx_gb)
    gpus = fit["gpu_count"]
    warnings: list[str] = []
    ok = True
    if gpus == 0:
        ok = False
        warnings.append("No NVIDIA GPU detected — max benchmark needs a serving GPU.")
    if fit["free_vram_gb"] is not None and fit["free_vram_gb"] < 2:
        warnings.append("Less than 2 GB free VRAM — serve/benchmark may OOM.")
    if fit["ram_available_gb"] is not None and fit["ram_available_gb"] < 4:
        ok = False
        warnings.append("Less than 4 GB host RAM available — benchmarking is unsafe.")
    if fit["level"] in ("slow", "insufficient"):
        warnings.append(fit["message"])
    return {
        "ok": ok and fit["level"] not in ("insufficient",),
        "warnings": warnings,
        "hardware": fit,
        "prerequisites": [
            "Serve a model from the Models tab (server pill = running).",
            "Prefer Modular `max benchmark` (default in this tab).",
            "Leave enough free VRAM for KV cache + activations beyond weights.",
            "Set HF token in Settings if the served model is gated.",
        ],
    }


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
    token = studio_settings.get_hf_token() or None
    api = HfApi(token=token)
    try:
        info = api.model_info(model_id, files_metadata=True)
    except Exception as exc:
        return {
            "ok": False,
            "model_id": model_id,
            "error": f"Hugging Face lookup failed: {exc}",
            "env": env,
            "hint": (
                "If this model is gated, set an HF token in Settings and accept the "
                "license on huggingface.co"
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
        cfg_path = hf_hub_download(model_id, "config.json", token=token)
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

    approx_gb = round(size_bytes / 1e9, 2) if size_bytes else None
    hardware = hardware_fit(approx_gb)

    warnings: list[str] = []
    steps: list[str] = [
        "1. Paste an org/name Hugging Face id (e.g. Qwen/Qwen2.5-0.5B-Instruct).",
        "2. Click Check — we verify MAX architecture support, HF access, disk, and VRAM.",
        "3. Fix any blockers (Settings → HF token, free VRAM, accept gated license).",
        "4. Download only when Modular-compatible and hardware looks OK — or force at your own risk.",
    ]
    if gated and not env["hf_token_set"]:
        warnings.append("Model is gated and HF token is not set — open Settings to save one.")
    if not has_st:
        warnings.append("No .safetensors weights found — MAX prefers safetensors.")
    if not modular_compatible:
        warnings.append(
            f"Architecture '{architecture}' is not in the known MAX list — "
            "serve may fail. Use Learn → Custom architectures to scaffold a port, "
            "or see https://docs.modular.com/max/models/"
        )
    if env["disk_free_gb"] is not None and size_bytes and env["disk_free_gb"] < size_bytes / 1e9 * 1.2:
        warnings.append(
            f"Disk may be tight: model ~{size_bytes / 1e9:.1f} GB, free {env['disk_free_gb']} GB."
        )
    if hardware["level"] in ("tight", "slow", "insufficient"):
        warnings.append(hardware["message"])

    can_download = bool(has_st and (not gated or env["hf_token_set"]))
    recommend_download = bool(
        can_download
        and modular_compatible
        and hardware.get("recommend_download", True)
    )
    risk = "ok"
    if not modular_compatible:
        risk = "unsupported_arch"
    elif hardware["level"] in ("slow", "insufficient"):
        risk = "hardware_risk"
    elif hardware["level"] == "tight":
        risk = "tight_vram"

    return {
        "ok": True,
        "model_id": model_id,
        "name": model_id.split("/")[-1],
        "architecture": architecture,
        "config_ok": config_ok,
        "config_error": cfg_error,
        "gated": gated,
        "has_safetensors": has_st,
        "approx_gb": approx_gb,
        "modular_compatible": modular_compatible,
        "max_list_available": bool(max_arches),
        "env": env,
        "hardware": hardware,
        "warnings": warnings,
        "steps": steps,
        "can_download": can_download,
        "recommend_download": recommend_download,
        "risk": risk,
        "risk_message": (
            "Compatible and hardware looks OK."
            if risk == "ok"
            else (
                "Architecture may not run on MAX — use at your own risk, or scaffold a custom arch."
                if risk == "unsupported_arch"
                else hardware["message"]
            )
        ),
        "handbook": "https://handbook.modular.com/",
        "models_docs": "https://docs.modular.com/max/models/",
        "hf_url": f"https://huggingface.co/{model_id}",
    }
