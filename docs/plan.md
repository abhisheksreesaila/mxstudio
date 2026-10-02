# Plan

<!-- Tickets, one per vertical slice (each independently shippable and testable).
Status is one of: todo, doing, review, done, blocked. A blocked ticket gets a
"Blocked:" line saying exactly what the person has to do.
Architecture: docs/adr/0001-mst-core-architecture.md. New code lives in `mst/`,
tests in `tests/mst/`. The old `notebooks/mxstudio` stays untouched until mst replaces it. -->

## F-001 mst skeleton, store and `mst ls` [todo]
Brief: docs/brief.md · Needs: none
- [ ] `pixi run mst --help` lists the commands; `mst` is a pixi task and a console entry point
- [ ] `~/.mst/mst.db` (or `$MST_HOME`) is created with `models` and `keys` tables
- [ ] `mst ls` prints registered models (name, source, state, pinned, keep_alive), or "no models yet"

## F-002 Resolve and pull a model [todo]
Brief: docs/brief.md · Needs: F-001
- [ ] `mst pull <ref>` resolves in order: MAX catalog (`max list --json`) → HF repo whose `config.json` architecture MAX supports → unsupported
- [ ] short names work for the curated small set (e.g. `qwen2.5-0.5b` → `Qwen/Qwen2.5-0.5B-Instruct`)
- [ ] supported: weights download to the HF cache at a pinned revision and the model is registered
- [ ] unsupported: nothing is downloaded; prints the architecture and `mst port <ref>` as the next step
- [ ] gated/missing repos give a one-line fix (set HF_TOKEN / check the name)

## F-003 Supervisor: on-demand start, idle unload, pin [todo]
Brief: docs/brief.md · Needs: F-001
- [ ] `ensure_running(model)` starts one `max serve` per model on a free loopback port and waits for health
- [ ] concurrent requests for a cold model trigger exactly one start
- [ ] a model idle past keep_alive is stopped; a pinned model is never stopped
- [ ] a crashed child is reported and restarted on the next request
- [ ] tested with a fake `max` binary (no GPU needed)

## F-004 Gateway: OpenAI-compatible routing with per-app keys [todo]
Brief: docs/brief.md · Needs: F-003
- [ ] `mst daemon` serves `/v1/models` and proxies `/v1/chat/completions` and `/v1/completions` (streaming and not) by `model`
- [ ] unknown model → 404 OpenAI-style error naming `mst pull`
- [ ] requests need `Authorization: Bearer <key>`; bad or revoked key → 401
- [ ] `/auth/check` answers Caddy forward_auth (200/401)
- [ ] `/docs` serves an OpenAPI page listing the endpoints and models

## F-005 Per-app keys [todo]
Brief: docs/brief.md · Needs: F-001
- [ ] `mst keys new <app>` prints a key once; only its hash is stored
- [ ] `mst keys ls` shows app, prefix, created, last used; `mst keys revoke <app>` disables it

## F-006 `mst serve <model>`: one command to a working endpoint [todo]
Brief: docs/brief.md · Needs: F-002, F-004, F-005
- [ ] pulls if needed, registers, starts the daemon (in the background) if not running, warms the model
- [ ] prints the base URL, a key (made on first use) and a ready-to-paste curl and Python snippet
- [ ] `--pin` keeps it loaded; `--keep-alive 10m` sets the idle timeout; `mst stop <model>` unloads it

## F-007 Secure LAN endpoint: Caddy + LiteLLM [todo]
Brief: docs/brief.md · Needs: F-004
- [ ] `mst expose` writes a Caddyfile (`tls internal`, forward_auth → `/auth/check`) and a LiteLLM config listing every registered model
- [ ] it starts Caddy and LiteLLM as user processes and prints `https://<host>.local:<port>/v1` plus how to trust the CA on another device
- [ ] `mst expose --no-litellm` points Caddy straight at the daemon
- [ ] `mst expose --stop` stops them

## F-008 Learn: `mst learn <model>` [todo]
Brief: docs/brief.md · Needs: F-002
- [ ] builds a notebook from the HF API only (model card, config.json, safetensors parameter count, license); no scraping
- [ ] includes a layer/parameter table from config.json and sample code (OpenAI client, streaming, raw HTTP) wired to the local endpoint and a key from the environment
- [ ] opens it in Jupyter Lab (`--no-open` just writes it)

## F-009 Unsupported architectures: `mst port <ref>` [todo]
Brief: docs/brief.md · Needs: F-002
- [ ] installs/updates Modular's skills (github.com/modular/skills) into `~/.mst/skills`
- [ ] creates a port workspace with the HF config and a `PORT.md` naming the architecture and the closest supported donor
- [ ] prints the command to run the import-model skill with Claude Code in that workspace
- [ ] a finished port can be registered with `mst pull <ref> --port <dir>`

## F-010 Endpoints page design [blocked]
Blocked: pick a visual direction with the designer skill (brief says explore, not Terminal Ledger). Say "design the endpoints page" when you're up.
- [ ] design for the loopback admin page: models, state, memory, keys, base URL, copy snippets

## F-011 Live acceptance on this machine [blocked]
Blocked: stop the other agents to free memory, then run `pixi run python scripts/mst_acceptance.py`.
- [ ] 3 small models (qwen2.5-0.5b, smollm2-1.7b, qwen2.5-1.5b) answer through one keyed HTTPS endpoint
- [ ] an idle model unloads and comes back on the next request
- [ ] the same call works from a second device on the LAN
