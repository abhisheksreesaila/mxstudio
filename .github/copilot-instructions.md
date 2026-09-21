# MX Studio Project Instructions

## North Star

Build the default local studio for discovering, downloading, understanding, adapting, and running models with Modular MAX. Match Ollama and LM Studio for first-run simplicity while preserving direct access to Hugging Face metadata, editable notebooks, MAX code, GPU behavior, compilers, serving, profiling, and inference engineering.

Keep the CLI and API first-class. The browser is an optional interface, not the product boundary. Distinguish shipped behavior from roadmap goals, especially for arbitrary model conversion, Apple GPU support, and regulated professional use.

## Architecture

Read [the architecture guide](../docs/MXSTUDIO_ARCHITECTURE.md) before changing a workflow.

- Put model metadata in `notebooks/mxstudio/registry.py`.
- Put Hugging Face and hardware compatibility logic in `notebooks/mxstudio/compat.py`.
- Put serving lifecycle behavior in `notebooks/mxstudio/servers.py`.
- Put architecture bring-up and Modular skill orchestration in `notebooks/mxstudio/architectures.py`.
- Put deterministic learning artifacts in `notebooks/mxstudio/notebooks.py` and `notebooks/mxstudio/slides.py`.
- Keep `notebooks/mxstudio/api.py` as a thin HTTP composition layer. Reuse domain operations from both FastAPI and `notebooks/mxstudio/cli.py`.

Long-running downloads, compilation, benchmarks, and architecture work must not block normal API request threads. Preserve the single managed MAX server invariant and never overwrite hand-edited files under `ports/`.

## Product Rules

- Prefer one simple command for the common path and expose evidence for advanced users.
- For every user-facing workflow, consider CLI, API, GUI, and notebook surfaces. Implement only the surfaces that add value, but do not make the GUI mandatory.
- Treat compatibility checks and scaffolds as guidance, not proof. Claims require recorded load, compilation, generation, benchmark, or parity evidence.
- Generated lessons must be deterministic, local, source-aware, and editable. Do not imply that static visualizers execute Mojo.
- Keep secrets out of logs and responses. Preserve allowed-root checks for file operations.
- Professional legal or health workflows require explicit privacy, security, licensing, evaluation, oversight, and regulatory design; never infer readiness from local serving alone.

## Verification

Install with `pixi install`. Run commands through Pixi so tests use the repository environment.

- Focused test: `pixi run python -m unittest tests.test_<area>`
- Full hermetic suite: `pixi run python -m unittest discover -s tests`
- Live first-run acceptance: `pixi run python scripts/first_run_live.py` only when network access, model download, and MAX execution are intended.

Do not claim NVIDIA, Apple silicon, custom-port, or numerical-parity behavior without testing on the relevant hardware and preserving the evidence.