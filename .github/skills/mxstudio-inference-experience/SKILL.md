---
name: mxstudio-inference-experience
description: "Build an MX Studio inference-engineering feature or tutorial. Use when adding MAX serving, GPU or memory inspection, compiler experiments, profiling, benchmarking, notebooks, visualizers, CLI commands, API routes, or Learn experiences."
argument-hint: "Concept or workflow to teach and implement"
---

# MX Studio Inference Experience

Build a runnable vertical experience, not an isolated explanation. A user should be able to start simply, inspect evidence, change something, and observe the result.

## Experience Shape

1. Name the learning question and the observable result. Examples: what controls time to first token, where model memory goes, or how a MAX graph change affects compilation.
2. Identify the existing owner module before editing. Prefer `servers.py` for serving, `bench.py` for measurement, `hwinfo.py` for hardware facts, `notebooks.py` for editable labs, and `architectures.py` or `ports/` for MAX graph work.
3. Design the terminal path first. Keep the common command short and reuse one domain operation from the CLI and API rather than duplicating behavior.
4. Add the optional GUI surface only after the operation and result schema are clear. Long work must use the existing job system and report progress, errors, command context, and artifacts.
5. Add or extend a deterministic notebook lesson. It should cite primary sources, call the local MAX endpoint when inference is needed, and invite safe edits that demonstrate the concept.
6. For Mojo or compiler execution, create an explicit bounded subprocess boundary with executable discovery, device checks, timeouts, captured output, and distinct compile/runtime failure states. Never label a static visualizer as execution.
7. Include a focused benchmark or comparison when performance is part of the claim. Label fallback measurements accurately and preserve hardware, MAX version, model, parameters, and raw evidence.
8. Add focused reliability tests. Hardware-dependent proof remains a separate live validation step and must be reported as such.

## Completion Standard

- The simplest path is available without requiring the GUI.
- Advanced details are inspectable rather than hidden.
- Claims distinguish estimates, hermetic tests, and target-hardware evidence.
- The workflow teaches one inference-engineering idea through a real operation.
- Current limitations and recovery actions are visible.

## Validation

Run the touched module's tests, then `pixi run python -m unittest discover -s tests`. For frontend changes, start `pixi run studio` and verify the complete workflow in the browser at desktop and mobile widths.