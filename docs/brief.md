# Product brief

Status: approved

## Why (the mission)
- Why this exists, in one sentence: a local AI runtime built only on Modular MAX/Mojo that is as easy as Ollama, nicer than LM Studio, and, unlike llama.cpp's compiled binary, lets you see and change how the model works.
- Why now, and why you: MAX runs the same tuned kernels on Linux, macOS and Windows hardware for free; nobody has built the convenient, transparent product around it.
- If it disappeared tomorrow: Abhishek's other projects would fall back to Ollama, which gives a black-box endpoint with nothing to learn from or tweak.

## Who it's for
- The one person first: Abhishek, on Omarchy Linux, whose other projects need one local endpoint serving several small models.
- The moment they reach for it: starting a project that needs a local LLM ("I need a 1.5B chat model and a small embedder on localhost").
- What they use today, and what's wrong with it: Ollama/LM Studio work, but are opaque (llama.cpp binaries) and not MAX.

## What it does
- The core job: serve small models behind one secure local endpoint, then teach you about them.
- v1 does:
  1. Serve: `mst serve <model>` resolves MAX catalog → Hugging Face (supported arch) → Modular's import-model skill (unsupported arch), and starts it. Several small models (≤3B) run at once.
  2. Secure endpoint: one OpenAI-compatible endpoint, with LiteLLM routing by model name and Caddy in front for HTTPS and keys, plus a page listing endpoints and models.
  3. Learn: `mst learn <model>` opens a Jupyter notebook generated from Hugging Face API metadata (model card, config, architecture) with sample code against the live endpoint.
- Out of scope for v1: hardware-fit advice, LoRA, the classifier conversion and other malleability, plugins, public installer, big models (>3B), non-Linux testing.

## How it should look and feel
- Three words: transparent, fast, exciting (assumed)
- Apps it should feel like: Ollama's CLI (borrow its verbs and speed); LM Studio comes later.
- House style (Terminal Ledger) or explore: explore a new direction with the designer skill; this is the flagship and gets its own identity.
- The one screen that matters most: the terminal output of `mst serve`, and the endpoints page.

## How we'll know it worked
- The behaviour that says it's working: one of Abhishek's real projects calls the mst endpoint daily for 2 weeks without him touching mst.
- What "done" means for v1: on a clean Omarchy box, install to first token in under 10 minutes; 3 small models served through one key-protected HTTPS endpoint, reachable from a second device; `mst learn <model>` opens a working notebook; then the 2-week daily-use run passes.

## Constraints and risks
- Small models only; single machine (Omarchy laptop).
- Riskiest assumption: MAX can run several small models side by side on this machine's memory/GPU. Cheapest test: start two `max serve` processes by hand today.
- Second risk: Modular's import-model skill does not reliably produce a working port; v1 treats it as a guided, best-effort path.

## Decisions and trade-offs
- 2026-10-03: the person asked to wire up the designed signature pages too, so `mxc bench` and the Benchmarks page move into v1 (tested against the stand-in model; real numbers come with the live run).
- First user is Abhishek on Omarchy: daily real use; we accept that the public installer waits for v2.
- v1 = endpoint + thin Learn: passes the acceptance test; we accept the classifier "wow" and dashboards waiting for v2.
- Name is **mxconsole** (2026-10-03, ADR 0003, superseding Hotfire): it is a console, and "MX" ties it to MAX/Mojo. Command `mxc`; `hotfire` and `mst` stay as aliases for one release.
- Security = LAN HTTPS + per-app keys: Caddy with its internal CA at https://<host>.local, revocable per-app keys in our own SQLite checked via Caddy forward_auth, LiteLLM routing on its master key only (no Postgres): other devices can use it safely; we accept trusting Caddy's CA once per client device.
- Lifecycle = load on demand + idle unload (keep_alive-style, with pinning to stay on): register many models without filling RAM; we accept a cold-start wait (30s+) on the first call after idle.
- Look = "Graphite" (Geist + Geist Mono, Lucide icons, near-monochrome with colour for live signal and per-section hues, dark-grey contrast chrome) with one signature moment per page (live tiles, pull cloud, patch bay, drag race, safeguards). Canvas: https://claude.ai/artifact/21NSyXKpzcrqanmqr8bEe5
- Fresh core on fh-saas, porting useful modules (resolver, HF/MAX compat, downloads, notebooks, import-model wiring) from the old FastAPI code: matches the house stack and drops the single-model design and evidence-gate ceremony; we accept rework before new features ship and fh-saas tenancy that a local app barely needs (one local tenant).

## Open questions
- None. Deferred to v2: benchmark dashboards, hardware fit, malleability (classifier, LoRA), plugins, public installer.
