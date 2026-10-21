# Plan

<!-- Tickets, one per vertical slice (each independently shippable and testable).
Status is one of: todo, doing, review, done, blocked. A blocked ticket gets a
"Blocked:" line saying exactly what the person has to do.
Architecture: docs/adr/0001-mst-core-architecture.md. New code lives in `mxconsole/`,
tests in `tests/mxconsole/`. The old `notebooks/mxstudio` stays untouched until mst replaces it. -->

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

## F-011 Live acceptance on this machine [deferred]
Deferred 2026-10-03 (the person's call: one machine for now). Only the second-device check is left. Copy ~/.local/share/caddy/pki/authorities/local/root.crt to a phone or laptop on the LAN and run the curl line `scripts/mxc_acceptance.py` prints (11/11 passed on 2026-10-03).
- [x] 3 small models (qwen2.5-0.5b, smollm2-1.7b, qwen2.5-1.5b) answer through one keyed HTTPS endpoint
- [x] an idle model unloads and comes back on the next request
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

## F-014 API keys and Network pages [done] (folded into F-016)
Brief: docs/brief.md · Design: docs/design/console.md · Needs: F-012
- [x] API keys grid (app, prefix, created, last used, status); New key shows the key once in the blade; Revoke with confirmation
- [x] Network page shows expose status, LAN URL, the CA path, and Expose/Stop buttons

## F-015 Command-line parity for agents [done]
Brief: docs/brief.md · Needs: F-013
- [x] `mst set <model> --pin | --unpin | --keep-alive 10m` changes any setting the console has
- [x] `--json` on every read command: `mst ls`, `mst keys ls`, `mst metrics`, `mst requests`, `mst status`
- [x] `mst metrics [--range 5m|1h|24h|7d]` and `mst requests [--limit N]` print the console's numbers as a table
- [x] `mst console [page]` opens the console; `mst serve`, `mst ls` and `mst status` print the matching console link
- [x] `mst status`: daemon, endpoints, models, keys, expose, in one screen

## F-016 Hotfire Mission Control in Graphite [done]
Brief: docs/brief.md · Design: design-system/DESIGN-SYSTEM.md (Graphite) + canvas pages "Graphite · base" and "Graphite · personality" · Needs: F-017
- [x] shell: top bar, coloured menu (section hues), status bar, ⌘K palette whose rows run the same actions as the CLI and show the command; themes contrast (default) / light / dark, remembered per browser
- [x] Geist + Geist Mono and Lucide icons ship with Hotfire (no CDN); CSP stays `default-src 'self'`
- [x] Overview: live strip (a trace per running model), number strip, live tiles (models cycling, activity ticker, network packets, fastest model, gateway, pulling)
- [x] Models: table with mini traces + side sheet (trace, keep-loaded, idle timeout, logs, CLI twin); replaces the F-012 grid and blade
- [x] Metrics: number blocks, "what you should care about" (rules over the request metrics, no model needed), charts per model
- [x] API reference page in the console skin replaces /docs (Scalar)
- [x] API keys and Network pages: keys table with new/revoke; the patch-bay diagram from real expose and port data
- [x] motion per the design system, all off under prefers-reduced-motion; page transitions between menu pages
- later tickets, not this one: Pull page (needs pull progress from the daemon), Benchmarks race (needs `hotfire bench`), Gateway safeguards counters, Ask Hotfire chat
## F-017 Rename to Hotfire [done]
Brief: docs/brief.md · ADR: docs/adr/0002-name-hotfire.md · Needs: none
- [x] Python package `mst/` becomes `hotfire/`; PyPI/project name `hotfire`; commands `hotfire` and `mst` (alias, one release)
- [x] state folder `~/.hotfire` (an existing `~/.mst` is moved on first run); `HOTFIRE_HOME`, `HOTFIRE_PORT`, … with `MST_*` read as a fallback
- [x] every user-facing string says Hotfire / `hotfire …` (CLI output, console title, notebooks, docs, README); console title "Hotfire Mission Control"
- [x] full test suite passes; `hotfire --help` and `mst --help` both work

## F-018 Rename to MX Console [done]
Brief: docs/brief.md · ADR: docs/adr/0003-name-mx-console.md · Needs: none
- [x] package `hotfire/` becomes `mxconsole/`; project name `mxconsole`; commands `mxc` (primary), `hotfire` and `mst` (aliases)
- [x] state `~/.mxc` (moves `~/.hotfire`, else `~/.mst`, with the existing safety rules); env `MXC_*` → `HOTFIRE_*` → `MST_*`
- [x] every user-facing string says MX Console / `mxc …`; the web UI title is "MX Console"
- [x] full suite passes; `mxc --help`, `hotfire --help`, `mst --help` all work

## F-019 Pull with progress: CLI and the Pull page [done]
Brief: docs/brief.md · Design: DESIGN-SYSTEM.md "Signature pages" (Pull) + canvas P2 · Needs: F-018
- [x] the daemon runs pulls as background jobs with progress (bytes per file, overall %, speed, step: resolve → architecture → download → verify → register); one at a time per model
- [x] `mxc pull <ref>` shows a live progress bar in the terminal (and `--json` lines for agents); Ctrl-C cancels cleanly
- [x] console Pull page: search box (curated names + any HF repo id), the cloud-churn animation driven by real progress, the step list, per-file rows, cancel; finished pulls appear in Models
- [x] tested with a fake Hugging Face client; nothing big is downloaded

## F-020 Benchmarks: `mxc bench` and the drag race [done]
Brief: docs/brief.md · Design: canvas P5 · Needs: F-018
- [x] `mxc bench [models…|--all]` runs the same streamed workload (prompt ~512 tokens, 128 out, batch 1, N runs) per model through the daemon and records decode tok/s, prefill tok/s, TTFT p50/p95 and duration in SQLite; `--json`
- [x] console Benchmarks page: the drag race from the latest run (cars placed by decode tok/s, finish line, speedometer, synthesized "vroom" only on click), results table, history of runs, Run benchmark button (runs in the background, progress shown)
- [x] honest labels: which models, workload, when; nothing shown before the first run
- [x] tested with the fake max (it streams at a fixed rate)

## F-021 System pages: Ports, Logs, Gateway, Daemon [done]
Brief: docs/brief.md · Design: canvas P6 + DESIGN-SYSTEM.md · Needs: F-018
- [x] Ports: every model's loopback port, the daemon, LiteLLM and Caddy ports, with state
- [x] Logs: a table of the CLI commands people need (copy buttons), plus a log viewer for the daemon and each model (tail, follow, filter)
- [x] Gateway: "what's protecting you": the six safeguards with real counters where the daemon can know them (401s, unknown routes, admin refusals) and "on" otherwise
- [x] Daemon: "the crew": daemon, Caddy, LiteLLM and each `max serve`, with pid, port, uptime and a heartbeat; restart/stop actions where safe
- [x] `mxc ps` prints the crew (with `--json`)

## F-022 SQLite safe under concurrent requests [done]
Brief: docs/brief.md · Needs: F-019, F-020, F-021 merged
- [x] the shared `Store` connection is never used from two threads at once (sync admin routes in the threadpool, pull and bench worker threads, the event loop recording metrics): one connection per thread, WAL, busy_timeout
- [x] stress test: 8 threads reading and writing metrics, keys and models at once, no errors, no lost writes

## Console 1.0 (brief: docs/briefs/console-1.0.md)
Design: concept D, https://claude.ai/artifact/CSZMDSYDhyCzja8pjHpotw (local copy `.lavish/concepts/d-console.html`).
Order: F-023 and F-024 first (in parallel); then F-025 to F-028 in parallel (each its own blade file); F-029 and F-030 any time; F-031 last.

## F-023 Blades shell and My models [done]
Brief: docs/briefs/console-1.0.md · Needs: none
- [x] `/console` is the new layout: dark top bar (brand → home, breadcrumb, hardware chip with a VRAM bar per running model and a popover, API keys, Gateway, Settings, theme toggle, daemon dot); no left menu, no global Logs
- [x] blade engine: blades dock side by side, the newest on the right; older ones fold into clickable rails when space runs out; breadcrumb and Esc close back; a wide blade (notebook, add) folds everything left of it
- [x] My models blade from `/admin/models` + `/admin/metrics`: list (default) and cards toggle remembered per browser, state, access, live tok/s, a crashed model called out with "View logs"; compact list when a model is open beside it; "Add a model"
- [x] blade registry: each later blade is its own `blade-*.js` + CSS section, stubs registered now so F-025..F-028 touch only their own files
- [x] light and dark themes from tokens (dark follows the system until toggled, remembered); colour-coded outline icons (icon in the action's hue, label a quieter tint; Remove red); CSP unchanged, data as text nodes
- [x] the daemon-down state; narrow screens show only the newest blade

## F-024 Admin API for the 1.0 console [done]
Brief: docs/briefs/console-1.0.md · Needs: none
- [x] `mxc rm <model>` and `DELETE /admin/models/{name}`: unregisters it, stops it, deletes mxconsole's own per-model files; weights stay in the HF cache unless `--purge`. MAX's compiled-graph cache is shared and keyed by graph hash, so it is left alone (decided 2026-10-11)
- [x] `GET /admin/search?q=`: curated names, MAX catalog and Hugging Face repo ids, each with source, params, size, estimated VRAM, `fit` (ok / tight / no / port) against this machine's free GPU memory, and for `no` up to 3 suggestions that fit
- [x] `mxc keys rotate <app>` and `POST /admin/keys/{app}/rotate`: new secret shown once, old one revoked
- [x] `GET /admin/learn/{name}`: the generated notebook (cells, kernel spec, path), generating it if missing; `POST /admin/learn/{name}/open` starts Jupyter on it and returns the URL
- [x] `GET /admin/system` also returns the GPU name, total and used VRAM, and VRAM per running model
- [x] every new route is admin-only and loopback-only like the others; each has its CLI twin with `--json`

## F-025 A model's resource blade, Security and API keys [done]
Brief: docs/briefs/console-1.0.md · Needs: F-023, F-024
- [x] model blade: menu (Overview, Security, Benchmark, Logs, Notebook), command bar (Start/Stop/Restart, Make it secure, Run benchmark, Open notebook, Logs, Copy endpoint, Remove with a second click to confirm)
- [x] Overview: essentials, live tokens (tok/s, TTFT, requests, trace), call it (endpoint, key, curl that changes to the LAN URL once secure), hardware fit bar
- [x] Security blade: Make it secure runs `/admin/expose` with its steps shown, then the HTTPS URL, key and CA path
- [x] API keys blade: list, new (shown once), rotate, revoke; each shows its `mxc` command

## F-026 Add a model [done]
Brief: docs/briefs/console-1.0.md · Needs: F-023, F-024
- [x] wide blade: search (`/admin/search`), results marked fits / tight / too big / needs porting; too big shows why and the suggestions
- [x] picking one runs the stages (check hardware, fetch, compile graph, serve on localhost, smoke test) from `/admin/pulls`, the load call and a one-line chat; live log; cancel
- [x] ends on a Ready card (endpoint, curl, Make it secure, Open); the model appears in My models with a short flash
- [x] needs porting → "Port it" hands over to F-029's job (until F-029 lands: shows the `mxc port` command)

## F-027 Benchmark and Logs blades [done]
Brief: docs/briefs/console-1.0.md · Needs: F-023
- [x] Benchmark: standard horizontal bar charts for decode tok/s and TTFT across your models (this one outlined), workload line, Run again (background, progress), history; data from `/admin/bench`
- [x] Logs: per model, follow/pause, filter, from `/admin/models/{name}/log`

## F-028 Notebook, Gateway and Settings blades [done]
Brief: docs/briefs/console-1.0.md · Needs: F-023, F-024
- [x] Notebook (wide): chapters, the generated notebook's markdown and code cells read-only with saved outputs, architecture panel, kernel chip, "Open in Jupyter" (`/admin/learn/{name}/open`)
- [x] Gateway: LiteLLM routes (model → upstream), status, restart; Settings: theme, idle unload default, daemon info
- Idle-unload default is shown read-only (no daemon-wide route yet); per-model via `mxc set <model> --keep-alive`.

## F-029 Automatic port for unsupported architectures [done]
Brief: docs/briefs/console-1.0.md · Needs: none (UI hook in F-026)
- [x] `mxc port <ref> --auto` runs Modular's `import-model` skill unattended with Claude Code (`claude -p`) in the port workspace, as a background job with stages (workspace, skills, generate, build, parity check, register) and a full log
- [x] a failing stage is retried with the error fed back (auto-fix, bounded attempts); on success the model is registered and served like any other; on failure the log says what it tried
- [x] `POST /admin/ports`, `GET /admin/ports/{id}`, cancel; `--json` progress lines
- [x] tested with a fake `claude` binary; nothing real runs in tests
- Runs under bubblewrap (private caches, read-only skills clone, scrubbed env, Claude login bound in); refuses without bwrap unless `--unsandboxed`. Accepted risks in port_auto.py's docstring.
- First real run (OLMoE-1B-7B, 2026-10-11): found and fixed four harness bugs (pixi activation leak, check cwd, weights re-downloaded per attempt, no GPU in the sandbox). The port then passed the build check every attempt but could not load in mxconsole's MAX 26.5 (written for the skill env's 26.7). Aligned 2026-10-11: mxconsole moved to MAX 26.7.0.dev2026100905 (11/11 live). Since 2026-10-13 the skill env's `max` is pinned to whatever MAX mxconsole serves, on every run, so the two can't drift; a manifest the pin can't rewrite fails the skills stage. Next: re-run the OLMoE port end to end.

## F-030 Desktop app for Linux and Mac [done]
Brief: docs/briefs/console-1.0.md · Needs: F-023
- [x] `mxc app` opens the console in a native window (pywebview), starting the daemon if it isn't running, and stops nothing on close
- [x] Linux: `mxc app --install` writes a `.desktop` launcher with the mxconsole icon
- [x] no Linux-only assumptions in the code path; Mac build verified later on a Mac
- Window: pywebview + Qt WebEngine in the pixi env `app` (`pixi install -e app`, ~1 GB, done by `--install`); falls back to a Chromium `--app` window. Mac still to verify on a Mac.

## F-031 Retire the old console pages [done]
Brief: docs/briefs/console-1.0.md · Needs: F-025..F-028
- [x] old page files and their CSS are gone; every old capability is reachable from a blade or the CLI; `mxc console [page]` maps old page names to blades
- [x] full suite green; live check in the running app (`/verify`) in both themes

## Parked: console layout redesign
Superseded 2026-10-11 by Console 1.0 above (concept D chosen).
Still mockup-only ideas, not built: `mxc set --gpu`, `mxc expose --deny`, the ask box.

## Standalone porting (goal set 2026-10-13)
Goal: porting an unsupported architecture works without the person installing anything or having Claude.
Agreed approach (2026-10-13):
1. The agent is swappable, not shipped: Claude by default through the bundled `claude-agent-sdk` (no separate
   install, only a sign-in or API key); later any endpoint, including a model mxconsole serves through its
   LiteLLM gateway, so porting can run fully offline without us training anything.
2. Measure before deciding: F-033 ports 5 to 10 architectures with Claude. Most pass → the skills are good
   enough; most fail → the fix is in the skills and harness, since a small model would do worse.
3. Fine-tuning comes later: the finished-ports catalog collects successful transcripts; a specialized (LoRA)
   model is tested against the same measurement once there are enough and the MAX API settles.
F-034 and F-035 need a brief (clarify) before building.

## F-032 Keep each attempt's raw agent transcript [done]
Brief: this section · Needs: F-029
- [x] every generate attempt writes the agent's raw stream-json to `ports/<id>/agent-<attempt>.jsonl` beside port.log (training data and study material)

## F-033 Port study with Claude [done]
Brief: this section · Needs: F-032
- [x] port 5 to 10 unsupported architectures, each in a fresh workspace, through `mxc port <ref> --auto`
- [x] `docs/port-study.md`: per architecture the outcome, attempts, time, which parity gate failed and why, the fixes Claude made, and which skill parts it used
- [x] the verdict: which steps are mechanical (a LoRA candidate) and which needed real reasoning; harness and skill fixes found along the way
- Findings so far (OLMoE, run 11): the run reused the 2026-10-11 workspace (not a clean test); parity attempt 1 failed (output differs from HF); MAX's compile cache isn't writable in the sandbox, so every parity check compiles cold

## F-034 Swappable porting agent [todo]
Brief: needed (docs/briefs/porting-agent.md) · Needs: F-033's verdict
- Claude via the bundled SDK by default, with a "Connect Claude" step in the console; then Codex and any OpenAI-compatible endpoint (incl. mxconsole's own gateway)

## F-035 Finished-ports catalog [todo]
Brief: needed (docs/briefs/ports-catalog.md) · Needs: F-033
- a port that passes the checks is published once per architecture; `mxc pull` uses it with no agent; the catalog keeps the successful transcripts

## F-036 Embeddings pooling check [todo]
Brief: docs/brief.md · Needs: none
- [ ] one live `/v1/embeddings` call against a pulled `all-minilm-l6`: does MAX read the pooling mode from `modules.json` / `1_Pooling/config.json` (never downloaded), or hard-code mean pooling? Download them if it reads them (found in review of 2ea97c4)

## F-037 Jupyter inside the Notebook blade [done]
Brief: docs/brief.md (small change to the approved notebook blade, F-028) · Needs: none
- [x] "Run in Jupyter" shows the notebook in Jupyter's single-document view inside the blade, not a new tab; "Saved view" goes back; a new-tab link stays
- [x] Jupyter lets only the console (http://127.0.0.1 or localhost on the daemon port) frame it; the console's CSP allows framing only http://127.0.0.1 on any port
- [x] a Jupyter server recorded by an older daemon, started without that framing permission, is not reused
- [x] checked in the running console (at 127.0.0.1 and at localhost): the iframe loads; restart-and-run-all ran the 3 Try-it cells, 200s from app `_notebook`
- [x] the console's Jupyter gets its own internal `_notebook` key and endpoint, so the Try-it cells need no setup; an outdated recorded server is stopped

## F-038 User guide for mxconsole [done]
Brief: docs/brief.md · Needs: none
- [x] `docs/USER_GUIDE.md` is the mxconsole guide: install, first ten minutes (console and CLI), the console tour, calling the endpoint from apps, securing it on the LAN, notebooks, porting (links PORTING.md), troubleshooting; commands link to docs/CLI.md
- [x] the old MX Studio / Nucleus material moves to `docs/LEGACY_NUCLEUS.md`, nothing deleted; README links the guide

## F-039 Docs site: a short README and interactive HTML pages [done]
Brief: the person's request of 2026-10-14 (README says why, setup, latest release, screenshots, API table; HTML for the console screens and porting) · Needs: F-038
- [x] README is short: why mxconsole (a convenience wrapper over MAX: local AI made easy, porting made fast), setup in three commands, latest release, screenshots, an HTTP API table, links to CLI.md, USER_GUIDE.md and the pages below
- [x] `docs/site/console.html`: every console screen from a real screenshot, with numbered hotspots that explain each part
- [x] `docs/site/porting.html`: behind the scenes of `mxc port --auto`, step by step, with what each step checks and what can come back
- [x] `docs/site/index.html`: the docs home, and how a request travels (local and LAN)
- [x] the pages work as plain files and on GitHub Pages; a test keeps the README's API table in step with the routes
- [x] the long morning checklist moves out of README to docs/VERIFY.md

## F-040 Port: refuse a repo with no safetensors before installing anything [todo]
Brief: docs/brief.md · Needs: none
- [ ] `mxc port --auto` on a repo with only .bin/GGUF weights fails in the workspace stage, before cloning skills or installing the env, with the same one-line message `mxc pull` gives (today: late, as "unexpected error: ValueError…"; found while writing docs/site/porting.html)
- [ ] the parity stage no longer says "downloading weights…" when they're already downloaded
- [ ] a remote-code model ends as "parked" in the CLI and console, not as a failed workspace stage

## omxconsole (brief: docs/briefs/omxconsole.md)
Design: the omxqua language, Developer family; mock `docs/design/omxconsole/mock.html` (approved 2026-10-20, with flat buttons inside blades).
A skin over the 1.0 console: no behaviour changes except the Beacon. Order: F-041 first (an omxqua release), then F-042; F-043 and F-044 in parallel (topbar files vs blade files); F-045 after F-044; F-046 any time.

## F-041 omxqua 0.1.5: CSP-safe and flat panel buttons [done]
Brief: docs/briefs/omxconsole.md · Needs: none · Repo: ~/Projects/omxqua
- [x] `omxqua.js` no longer injects a `<style>` element; that CSS lives in `components.css` (works under `default-src 'self'`)
- [x] buttons inside a panel are flat: `.oq-flat` (or a blade/panel container class) removes shadow and hover lift, tonal gets a state tint; documented in AGENTS.md and LANGUAGE.md
- [x] `Omxqua.settle` rises 12 px as the docs say (today 6 px)
- [x] tagged v0.1.5 with a changelog entry; the showcase still renders the same (spot-checked); `tests/csp.test.cjs` guards the no-injected-style rule. v0.1.5 also ships the Money family another session added. Reviewer: PASS (after fixing landing.html's logo and the changelog). Tag is local until the push is approved.

## F-042 Vendor omxqua into the console [todo]
Brief: docs/briefs/omxconsole.md · Needs: F-041
- [ ] `omxqua-sync mxconsole/console --ref v0.1.5` puts `omxqua/{src,fonts,VERSION}` in the package; the gateway serves exactly those files from an explicit list under `/console/omxqua/…` (still never a directory server); CSP unchanged
- [ ] `index.html` loads omxqua's `fonts.css` before the console CSS; the console's own copies of Geist and Geist Mono (and their licence file) are dropped; nothing changes visually
- [ ] (moved to F-043/F-044: `tokens.css`, `components.css`, `omxqua.js` and the root classes, since `tokens.css` restyles the root and would change the old look before the new one exists)
- [ ] a test checks every omxqua asset is served with the console headers and that nothing outside the list is

## F-043 Top islands and the Beacon [todo]
Brief: docs/briefs/omxconsole.md · Needs: F-042 · Files: topbar.js, topbar.css, hardware.js
- [ ] the dark top bar becomes islands: brand (`.mark` "omx" that writes itself, "omxconsole", "local"), breadcrumb in the middle, API keys · Gateway · Settings · theme on the right, then the Beacon
- [ ] the Beacon replaces the hardware chip and shows one thing, in order: a pull or port in progress (with %), a crashed model, running models with live tok/s, else GPU memory in use; tapping opens it (details, up to 3 actions), × or outside closes it
- [ ] the priority is a pure function with unit tests over the admin data (pull, port, crash, running, idle)
- [ ] at ≤ 760 px the tools fold into one ••• menu and the breadcrumb hides; no overlap at 390 px
- [ ] checked in the running console in both themes, desktop and 390 px

## F-044 Blades in omxqua: My models and a model's blade [todo]
Brief: docs/briefs/omxconsole.md · Needs: F-042 · Files: blades.css, blade-home.*, blade-model.*
- [ ] blades are omxqua surfaces (surface-1, card radius, shadow-1) that settle in (fade + 12 px, no bounce); older ones still fold to rails
- [ ] My models: stats card, filter (floating label), list/cards toggle (`.seg2`), table (`.tbl`), state as dot + word, a laptop "Local" badge; the compact list beside an open model and on phones
- [ ] a model's blade: menu, command bar of flat buttons (one filled verb), cards (Essentials, Tokens · live, Call it, Security, Hardware fit), each ending in its `mxc` source line; `.xbtn` close
- [ ] every console test still passes; checked in the running console, both themes, desktop and 390 px, against the mock

## F-045 The other blades in omxqua [todo]
Brief: docs/briefs/omxconsole.md · Needs: F-044 · Files: blade-add.*, blade-security.*, blade-keys.*, blade-bench.*, blade-logs.*, blade-notebook.*, blade-gateway.*, blade-settings.*
- [ ] Add a model, Security, API keys, Benchmark, Logs, Notebook, Gateway and Settings use omxqua parts (fields, chips, menus, toasts, progress, empty states) and flat buttons; no blade keeps the old look
- [ ] the old Graphite CSS that no blade uses any more is deleted; the brace-balance test still passes
- [ ] checked each blade in the running console, both themes

## F-046 Name on screen: omxconsole [todo]
Brief: docs/briefs/omxconsole.md · ADR: docs/adr/0004-name-omxconsole.md · Needs: none
- [ ] page and window titles, the wordmark, the API reference header, CLI messages and `/v1/models`' `owned_by` say omxconsole; the `mxc` command, the `mxconsole` package, `~/.mxc` and `MXC_*` stay
- [ ] README, USER_GUIDE, CLI.md and docs/site say omxconsole; lessons.md's naming line is updated
- [ ] a test checks `/v1/models` `owned_by` and the console title
