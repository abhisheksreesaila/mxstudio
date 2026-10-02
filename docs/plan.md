# Plan

<!-- Tickets, one per vertical slice (each independently shippable and testable).
Status is one of: todo, doing, review, done, blocked. A blocked ticket gets a
"Blocked:" line saying exactly what the person has to do.
Architecture: docs/adr/0001-mst-core-architecture.md. New code lives in `hotfire/`,
tests in `tests/hotfire/`. The old `notebooks/mxstudio` stays untouched until mst replaces it. -->

## F-001 mst skeleton, store and `mst ls` [done]
Brief: docs/brief.md · Needs: none
- [x] `pixi run mst --help` lists the commands; `mst` is a pixi task and a console entry point
- [x] `~/.mst/mst.db` (or `$MST_HOME`) is created with `models` and `keys` tables
- [x] `mst ls` prints registered models (name, source, state, pinned, keep_alive), or "no models yet"

## F-002 Resolve and pull a model [done]
Brief: docs/brief.md · Needs: F-001
- [x] `mst pull <ref>` resolves in order: MAX catalog (`max list --json`) → HF repo whose `config.json` architecture MAX supports → unsupported
- [x] short names work for the curated small set (e.g. `qwen2.5-0.5b` → `Qwen/Qwen2.5-0.5B-Instruct`)
- [x] supported: weights download to the HF cache at a pinned revision and the model is registered
- [x] unsupported: nothing is downloaded; prints the architecture and `mst port <ref>` as the next step
- [x] gated/missing repos give a one-line fix (set HF_TOKEN / check the name)

## F-003 Supervisor: on-demand start, idle unload, pin [done]
Brief: docs/brief.md · Needs: F-001
- [x] `ensure_running(model)` starts one `max serve` per model on a free loopback port and waits for health
- [x] concurrent requests for a cold model trigger exactly one start
- [x] a model idle past keep_alive is stopped; a pinned model is never stopped
- [x] a crashed child is reported and restarted on the next request
- [x] tested with a fake `max` binary (no GPU needed)

## F-004 Gateway: OpenAI-compatible routing with per-app keys [done]
Brief: docs/brief.md · Needs: F-003
- [x] `mst daemon` serves `/v1/models` and proxies `/v1/chat/completions` and `/v1/completions` (streaming and not) by `model`
- [x] unknown model → 404 OpenAI-style error naming `mst pull`
- [x] requests need `Authorization: Bearer <key>`; bad or revoked key → 401
- [x] `/auth/check` answers Caddy forward_auth (200/401)
- [x] `/docs` serves an OpenAPI page listing the endpoints and models

## F-005 Per-app keys [done]
Brief: docs/brief.md · Needs: F-001
- [x] `mst keys new <app>` prints a key once; only its hash is stored
- [x] `mst keys ls` shows app, prefix, created, last used; `mst keys revoke <app>` disables it

## F-006 `mst serve <model>`: one command to a working endpoint [done]
Brief: docs/brief.md · Needs: F-002, F-004, F-005
- [x] pulls if needed, registers, starts the daemon (in the background) if not running, warms the model
- [x] prints the base URL, a key (made on first use) and a ready-to-paste curl and Python snippet
- [x] `--pin` keeps it loaded; `--keep-alive 10m` sets the idle timeout; `mst stop <model>` unloads it

## F-007 Secure LAN endpoint: Caddy + LiteLLM [done]
Brief: docs/brief.md · Needs: F-004
- [x] `mst expose` writes a Caddyfile (`tls internal`, forward_auth → `/auth/check`) and a LiteLLM config listing every registered model
- [x] it starts Caddy and LiteLLM as user processes and prints `https://<host>.local:<port>/v1` plus how to trust the CA on another device
- [x] `mst expose --no-litellm` points Caddy straight at the daemon
- [x] `mst expose --stop` stops them

## F-008 Learn: `mst learn <model>` [done]
Brief: docs/brief.md · Needs: F-002
- [x] builds a notebook from the HF API only (model card, config.json, safetensors parameter count, license); no scraping
- [x] includes a layer/parameter table from config.json and sample code (OpenAI client, streaming, raw HTTP) wired to the local endpoint and a key from the environment
- [x] opens it in Jupyter Lab (`--no-open` just writes it)

## F-009 Unsupported architectures: `mst port <ref>` [done]
Brief: docs/brief.md · Needs: F-002
- [x] installs/updates Modular's skills (github.com/modular/skills) into `~/.mst/skills`
- [x] creates a port workspace with the HF config and a `PORT.md` naming the architecture and the closest supported donor
- [x] prints the command to run the import-model skill with Claude Code in that workspace
- [x] a finished port can be registered with `mst pull <ref> --port <dir>`

## F-010 Console design [done]
Brief: docs/brief.md · Design: docs/design/console.md
- [x] visual direction chosen: Azure Blade (option C), tokens in design-system/DESIGN-SYSTEM.md

## F-011 Live acceptance on this machine [blocked]
Blocked: stop the other agents to free RAM, then run `pixi run python scripts/hotfire_acceptance.py` (downloads ~6 GB the first time). For the second-device check, copy ~/.local/share/caddy/pki/authorities/local/root.crt to that device and run the curl line the script prints.
- [ ] 3 small models (qwen2.5-0.5b, smollm2-1.7b, qwen2.5-1.5b) answer through one keyed HTTPS endpoint
- [ ] an idle model unloads and comes back on the next request
- [ ] the same call works from a second device on the LAN

## F-012 Console shell and Models blade [done]
Brief: docs/brief.md · Design: docs/design/console.md · Needs: F-006
- [x] `http://127.0.0.1:11435/console` serves the console (loopback-only, refused through a proxy or a foreign Host)
- [x] top bar, resource menu (all groups; unbuilt pages say "coming soon"), breadcrumb, command bar
- [x] Models grid from `/admin/status` + the store: name, state badge, source, lifecycle, refreshed every 3 s
- [x] clicking a model opens the blade: Overview (repo, revision, architecture, state) and Settings (pin, unload after idle) with Apply/Discard, saved through a new `PATCH /admin/models/{name}`
- [x] Load and Stop in the command bar and the blade work; a crashed model opens on Logs with its log tail
- [x] empty, loading, daemon-down and narrow-screen states as in the handoff

## F-013 Overview page with gauges [done]
Brief: docs/brief.md · Design: docs/design/console.md (gauges from option B) · Needs: F-012
- [x] the daemon records per-request metrics (model, app, status, prompt/completion tokens, TTFT, duration) in SQLite
- [x] Overview shows essentials (endpoints, MAX version, GPUs, daemon uptime, active keys) and gauges for decode tok/s, TTFT p50, GPU memory, requests/min
- [x] Recent requests: the last 10 on Overview, the last 200 on Activity log

## F-014 API keys and Network pages [todo] (folded into F-016)
Brief: docs/brief.md · Design: docs/design/console.md · Needs: F-012
- [ ] API keys grid (app, prefix, created, last used, status); New key shows the key once in the blade; Revoke with confirmation
- [ ] Network page shows expose status, LAN URL, the CA path, and Expose/Stop buttons

## F-015 Command-line parity for agents [done]
Brief: docs/brief.md · Needs: F-013
- [x] `mst set <model> --pin | --unpin | --keep-alive 10m` changes any setting the console has
- [x] `--json` on every read command: `mst ls`, `mst keys ls`, `mst metrics`, `mst requests`, `mst status`
- [x] `mst metrics [--range 5m|1h|24h|7d]` and `mst requests [--limit N]` print the console's numbers as a table
- [x] `mst console [page]` opens the console; `mst serve`, `mst ls` and `mst status` print the matching console link
- [x] `mst status`: daemon, endpoints, models, keys, expose, in one screen

## F-016 Hotfire Mission Control in Graphite [todo]
Brief: docs/brief.md · Design: design-system/DESIGN-SYSTEM.md (Graphite) + canvas pages "Graphite · base" and "Graphite · personality" · Needs: F-017
- [ ] shell: top bar, coloured menu (section hues), status bar, ⌘K palette whose rows run the same actions as the CLI and show the command; themes contrast (default) / light / dark, remembered per browser
- [ ] Geist + Geist Mono and Lucide icons ship with Hotfire (no CDN); CSP stays `default-src 'self'`
- [ ] Overview: live strip (a trace per running model), number strip, live tiles (models cycling, activity ticker, network packets, fastest model, gateway, pulling)
- [ ] Models: table with mini traces + side sheet (trace, keep-loaded, idle timeout, logs, CLI twin); replaces the F-012 grid and blade
- [ ] Metrics: number blocks, "what you should care about" (rules over the request metrics, no model needed), charts per model
- [ ] API reference page in the console skin replaces /docs (Scalar)
- [ ] API keys and Network pages: keys table with new/revoke; the patch-bay diagram from real expose and port data
- [ ] motion per the design system, all off under prefers-reduced-motion; page transitions between menu pages
- [ ] later tickets, not this one: Pull page (needs pull progress from the daemon), Benchmarks race (needs `hotfire bench`), Gateway safeguards counters, Ask Hotfire chat
## F-017 Rename to Hotfire [done]
Brief: docs/brief.md · ADR: docs/adr/0002-name-hotfire.md · Needs: none
- [x] Python package `mst/` becomes `hotfire/`; PyPI/project name `hotfire`; commands `hotfire` and `mst` (alias, one release)
- [x] state folder `~/.hotfire` (an existing `~/.mst` is moved on first run); `HOTFIRE_HOME`, `HOTFIRE_PORT`, … with `MST_*` read as a fallback
- [x] every user-facing string says Hotfire / `hotfire …` (CLI output, console title, notebooks, docs, README); console title "Hotfire Mission Control"
- [x] full test suite passes; `hotfire --help` and `mst --help` both work
