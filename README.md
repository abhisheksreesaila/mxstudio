# mxstudio

**Version 0.1.1** — see [CHANGELOG.md](CHANGELOG.md).


A thin, fast LM Studio-style layer on top of [Modular MAX](https://www.modular.com/max).
Download curated models from Hugging Face, serve them with an OpenAI-compatible
API, chat, generate documentation notebooks, and benchmark your hardware — from
a web UI or the CLI.

## Quickstart

```bash
cd ~/unifiedram/mxstudio

# Web UI → http://localhost:8080
./scripts/studio.sh          # or: pixi run studio

# CLI
pixi run mxs list
pixi run mxs download qwen2.5-0.5b
pixi run mxs serve qwen2.5-0.5b        # OpenAI API on http://localhost:8100/v1
pixi run mxs chat "Hello!"             # one-shot
pixi run mxs chat                      # interactive REPL
pixi run mxs bench --runs 3
pixi run mxs notebook qwen2.5-0.5b
pixi run mxs stop
```

Model names resolve by unique substring — `qwen2.5-0.5b`, `smollm`, `olmoe`
all work.

## The tabs

| Tab | What it does |
|-----|--------------|
| **Models** | Curated registry + HF import with MAX/token/VRAM gates. Download, serve, uninstall. |
| **Chat** | Floating button (anytime). Streaming chat + browser voice input. |
| **Notebooks** | Educational labs with **cited sources** (HF first) + runnable cells. |
| **Benchmark** | Hardware + readiness checks; Modular `max benchmark` with history. |
| **Learn** | Handbook links + **custom architecture wizard** (gap checklist → scaffold). |
| **Settings** | Save Hugging Face token securely to `~/.mxstudio/secrets.json`. |
| **Streaming** | Phase 5 preview: SSD expert streaming for MoE models (mxstream). |

## Architecture

```
mxstudio/                  Python package
├── registry.py            curated model list (id, links, innovation notes)
├── downloads.py           HF snapshot downloads, background threads + progress
├── servers.py             `max serve` subprocess manager (health, logs, pid)
├── bench.py               TTFT / tok/s benchmark jobs
├── hwinfo.py              GPU/CPU/RAM/disk detection
├── modelinfo.py           HF model card fetch
├── notebooks.py           per-model .ipynb generation
├── api.py                 FastAPI backend (REST + SSE chat proxy)
├── cli.py                 Typer CLI (`pixi run mxs …`)
└── web/                   static frontend (no build step)
```

Design choices that keep the system responsive:

- **`max serve` runs in its own process group.** The studio backend never
  blocks on it and a model crash can't take the UI down. Its pid is persisted
  in `~/.mxstudio/state.json`, so the studio can restart independently.
- **Everything slow is a background job** (downloads, benchmarks, model
  loading) that the UI polls — no request ever waits on a long operation.
- **One model server at a time** on port 8100 — a deliberate VRAM guard for
  single-GPU machines. Starting a new model cleanly stops the old one.
- **Chat streams end-to-end**: SSE from the backend, which proxies the
  OpenAI-compatible stream from MAX (also avoids CORS issues).
- **WSL quirk handled**: `--no-device-graph-capture` is always passed, since
  CUDA graph capture fails under WSL2.

## Ports

| Port | Service |
|------|---------|
| 8080 | mxstudio web UI + REST API |
| 8100 | managed `max serve` (OpenAI-compatible: `/v1/chat/completions`) |
| 8000 | legacy manual Qwen server (`scripts/serve.sh`) |
| 8002 | experimental OLMoE bring-up (`scripts/serve_olmoe.sh`) |

## Notebooks

Generated into `notebooks/`. Open them in Cursor/VS Code (built-in kernel
support), or install Jupyter into the pixi env:

```bash
pixi add jupyterlab
pixi run jupyter lab
```

## Roadmap

1. ✅ Models / Chat / Notebooks / Benchmark tabs
2. Model comparison benchmarks (same prompt, N models, side-by-side)
3. Electron packaging
4. **Streaming (phase 5):** mxstream SSD expert streaming for MoE models —
   io_uring + O_DIRECT + VRAM hot-expert tier, targeting OLMoE first.
