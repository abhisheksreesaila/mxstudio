# Console 1.0 migration: where the old pages went (F-031)

The 13 old pages, the left menu, the ⌘K palette and `console.css` are gone. Every capability is in a blade, in the CLI, or listed as dropped below with the CLI twin that still covers it.

## Capability map

| Old page / piece | What it did | Now |
|---|---|---|
| Overview | live strip, number tiles, per-model traces | **My models** blade: tok/s and trace per model, the "Requests · 5 min" tile, hardware chip in the top bar; model blade Overview has the live tokens |
| Models | table, side sheet (Overview, Settings, Logs, Requests), load/stop/pin/keep-alive | **My models** list/cards + the **model blade** (Start/Stop/Restart, Logs, Remove). Pin and keep-alive: Settings shows each model's idle rule; change with `mxc set <model> --pin / --keep-alive 10m` (CLI-only, as before the sheet) |
| Metrics | colour blocks, "what to care about", charts per model, 5m/1h/24h/7d range | Live tok/s, TTFT, requests and trace per model in My models and the model blade (5 min window). **Dropped:** longer ranges and the rules panel. CLI: `mxc metrics --range 1h` |
| Activity | last 200 requests | **Gateway** blade, "Recent requests" (newest 12 of the polled 40; kept 7 days). CLI: `mxc requests --limit 200` |
| API reference + "Try it" | endpoints, fields, examples, one test chat | **`/docs`**: a plain server-rendered page from the OpenAPI spec (base URL, auth, every endpoint, fields, a copyable curl for a real model); linked from the Gateway blade; `mxc console api`. "Try it" is **dropped** as a button: Add's Ready card and the smoke test already send a real chat; the curl on `/docs` and the model blade is the replacement |
| API keys | list, new (shown once), revoke | **API keys** blade (also rotate). CLI: `mxc keys` |
| Network (patch bay) | ports, expose / stop expose | **Security** blade per model (Make it secure = expose), **Gateway** blade (status, routes), **Settings** ports table. CLI: `mxc expose [--stop]`. The animated patch-bay drawing is **dropped** |
| Ports | every listener, state, what talks to it | **Settings**, "Ports & processes" (same columns). CLI: `mxc ps` |
| Logs | command cheat-sheet, log viewer for daemon, Caddy, LiteLLM, each model | model **Logs** blade (per model), **Gateway** blade (LiteLLM log). **Dropped:** daemon and Caddy log viewers. CLI: `mxc logs <model> -f`; daemon log file under `~/.mxc/logs` |
| Gateway | six safeguard illustrations, counters | **Gateway** blade: status, routes, "Safeguards" counters, LiteLLM log, restart. The illustrations are dropped |
| Daemon ("the crew") | pid, port, memory, uptime, heartbeat per process; safe actions | **Settings**: daemon info + Stop daemon + ports table; model processes in the model blade. **Dropped:** pid/memory/heartbeat columns and the Caddy/LiteLLM per-row actions. CLI: `mxc ps`, `mxc stop --all`, `mxc expose --stop` |
| Benchmarks (drag race, gauge) | run, results table, history | **Benchmark** blade of a model (run, results, history). The drag-race animation and sound are dropped |
| Pull | search, cloud-and-cartridge animation, per-file rows, Cancel, recent pulls | **Add a model** blade: search with fit, staged setup with live log and Cancel, Ready card. CLI: `mxc pull` |
| `palette.js` (⌘K) | jump / run actions | **Dropped.** The command bar in each model blade, the top bar and the breadcrumb cover the actions; the CLI covers the rest |
| `shell.js`, `actions.js`, `charts.js`, `console.css` | old frame, helpers, charts, styles | Replaced by `blades.js`/`topbar.js`, the blade files and `blades.css`. Unused helpers in `core.js` (theme cycle, dialog, terminal box, colour assignment, lifecycle text) removed |

## What `mxc console [page] [model]` opens

Old names keep working, in the CLI and as hashes typed into the console (`#overview` etc.). Pure mapping, tested in `tests/mxconsole/test_console_routes.py` on both sides (`report.console_hash`, `console/routes.js`).

| Name | Opens |
|---|---|
| home, overview, models, metrics, activity, ports | My models |
| keys | API keys |
| gateway, network | Gateway |
| daemon, settings | Settings |
| pull, add | Add a model |
| model `<name>` | that model's blade |
| security, bench (also `benchmarks`), logs, notebook `<name>` | model blade with that child blade open. With no name, `bench`, `security` and `notebook` take the first registered model (My models if there is none); `logs` and `model` with no name stay on My models. Names are URL-encoded in the link (`my%20model`) |
| api | `/docs` (not a blade) |

The console keeps the hash in step with what is open (`#keys`, `#bench/<name>`), so a link can be shared or reloaded.

## Decisions

- Recent requests live in Gateway (it is the `/v1` front door); ports live in Settings (it already holds the daemon); the API reference stays a page because it is reading material, not a panel next to a model.
- The theme has one setter (`setTheme`) and one event in `prefs.js`; the top-bar button and Settings both use it, and a system theme change repaints both.
- Dropped on purpose, each with its CLI twin above: long-range metrics, daemon/Caddy log viewers, per-process memory and heartbeat, the "Try it" button, the ⌘K palette, the decorative illustrations and animations.
