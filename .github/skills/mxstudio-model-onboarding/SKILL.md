---
name: mxstudio-model-onboarding
description: "Onboard, inspect, download, or port a Hugging Face model into MX Studio. Use when adding model metadata, checking MAX compatibility, creating a custom architecture, adapting weights, or validating model bring-up."
argument-hint: "Hugging Face org/model id and desired outcome"
---

# MX Studio Model Onboarding

Use this workflow to move a model from a Hugging Face ID to an evidence-backed MAX experience. Do not equate download success or generated scaffolding with a working port.

## Procedure

1. Read `docs/MXSTUDIO_ARCHITECTURE.md`, then inspect the closest entry in `notebooks/mxstudio/registry.py` and the compatibility path in `notebooks/mxstudio/compat.py`.
2. Run `pixi run mxs info <model>` for curated metadata. For arbitrary IDs, use the browser compatibility check or call the existing `compat.check_hf_model()` path through a focused test.
3. Record model-card source, license, architecture name, checkpoint format, approximate size, context length, hardware fit, and whether `max list --json` reports support.
4. If MAX supports the architecture, add only truthful curated metadata and prove download, serve, one streamed response, and notebook generation on suitable hardware.
5. If MAX does not support it, use the Architectures workflow. Inspect, select a donor, approve semantic deltas, scaffold under `ports/`, and preserve all hand-written implementation work.
6. Use Modular's official `import-model` workflow for inspection and implementation preflight. MX Studio installs it on first managed architecture stage into `.mxstudio/skills/modular`, or discovers it from `.agents/skills`, `~/.agents/skills`, or `~/.copilot/skills`.
7. Close gates in order: implementation targets, strict weight load, cold compilation and generation, then reference token/logit parity. Persist commands, logs, and artifacts under the existing bring-up result paths.
8. Add focused tests in `tests/test_compat.py`, `tests/test_architectures.py`, `tests/test_downloads.py`, or `tests/test_olmoe_port.py` as appropriate.

## Required Evidence

- Hugging Face source and license are visible.
- Hardware guidance is labeled approximate.
- Unsupported architectures are not presented as runnable.
- No scaffold overwrites an existing implementation.
- A successful port has strict-load, generation, and reference-comparison evidence.

## Validation

Run the narrowest relevant test first, then `pixi run python -m unittest discover -s tests`. Live serving and parity checks require the intended MAX version, weights, and target hardware.