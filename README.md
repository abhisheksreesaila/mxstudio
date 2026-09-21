# MX Studio

**The goal: one model, one command, a secure endpoint.**

The shortest path from a supported Hugging Face checkpoint or MAX model listing
to a working, authenticated HTTPS endpoint on your network—powered by Modular
MAX, published through Caddy, with every step logged for debugging.

**Status:** experimental. The building blocks exist; the single-command,
model-to-HTTPS workflow and autonomous unsupported-model adaptation loop are
**not yet implemented**. This is the product direction, not a claim that any
model can already be converted or served.

## Three ways in, one endpoint out

| Starting point | Intended workflow | Available today |
| --- | --- | --- |
| MAX-supported model | Resolve its checkpoint → download → validate → serve → HTTPS URL | Support checks and serving exist; no unified catalog-to-HTTPS command. |
| Hugging Face model supported by MAX | Inspect compatibility → use the supported MAX architecture → serve → HTTPS URL | Metadata checks and downloads exist; the download/serve CLI currently resolves curated entries. |
| Hugging Face model not supported by MAX | Skill-driven inspect → implement/adapt → load → compile → generate → compare → fix/retry → serve only after validation | Guided scaffolds and skill preflight exist; autonomous implementation, repair, and evidence-enforced promotion do not. |

Already-supported checkpoints do not need an invented conversion step. Unsupported
architectures require a real MAX implementation, not just renamed weights or a scaffold.

## Try the current supported-model path

Install [Pixi](https://pixi.sh/), clone this repository, and run from its root:

```bash
pixi install
pixi run mxs download Qwen/Qwen2.5-0.5B-Instruct
pixi run mxs serve Qwen/Qwen2.5-0.5B-Instruct
pixi run mxs chat "Say hello in one sentence."
pixi run mxs logs --lines 80
pixi run mxs stop
```

This downloads weights and launches real MAX on suitable hardware. It is a
**localhost smoke test**, not the finished secure-endpoint experience.
For authenticated serving and Caddy, follow the [endpoint guide](docs/MXSTUDIO_NUCLEUS.md).
LAN hostname resolution and client certificate trust still require setup.

## Keep the core small

- **Core:** terminal-first, one managed MAX model, FastAPI, SQLite jobs, Caddy for HTTPS.
- **Optional:** browser Studio, notebooks, benchmarks, and advanced LiteLLM routing.
- LiteLLM is not needed to serve one model. It does not make this controller run multiple models concurrently.
- Current Linux deployment installer: Debian/Ubuntu x86_64. Apple-Silicon Linux/GPU support is not established.

## Documentation

- [Mission, current gaps, and completion criteria](docs/ENDPOINT_FIRST.md)
- [Installation, CLI, apps, and optional Studio experiences](docs/USER_GUIDE.md)
- [Authenticated endpoint, Caddy, operations, and troubleshooting](docs/MXSTUDIO_NUCLEUS.md)
- [Architecture and module ownership](docs/MXSTUDIO_ARCHITECTURE.md)

Project licensing has not yet been declared. Model weights retain their publishers' licenses.
