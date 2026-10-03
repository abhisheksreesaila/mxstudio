# Design handoff: MX Console (the console)

Built in **Graphite** (F-016): tokens and components in `design-system/DESIGN-SYSTEM.md`, canvas
https://claude.ai/artifact/21NSyXKpzcrqanmqr8bEe5. This replaces the earlier Azure Blade skeleton (F-010, F-012).

## Where it lives
Served by the daemon at `http://127.0.0.1:11435/console` (loopback-only, like `/openapi.json`; `/docs`
redirects to `/console#api`). Plain HTML, CSS and ES modules in `mxconsole/console/`, no framework, no CDN,
CSP `default-src 'self'`. Data goes in as text nodes (never HTML); inline styles are set through the CSSOM.
The page calls the daemon's `/admin/*` routes with the `X-Mst-Admin: 1` header (same origin). Nothing is
reachable through `mxc expose`. A new file in `mxconsole/console/` must be added to `CONSOLE_ASSETS` in
`mxconsole/gateway.py` (a test fails otherwise).

## Pages (hash routes) and where their data comes from
| Page | Route | Data |
|---|---|---|
| Overview: live strip, number strip, live tiles | `#overview` | `/admin/models`, `/admin/metrics`, `/admin/series?range=5m`, `/admin/requests`, `/admin/info` |
| Activity | `#activity` | `/admin/requests?limit=200` |
| Models: table + side sheet (Overview, Settings, Logs, Requests) | `#models`, `#models/<name>[/<tab>]` | `/admin/models`, `PATCH /admin/models/<n>`, `/admin/models/<n>/log`, `/admin/requests?model=` |
| API keys | `#keys` | `/admin/keys` (GET, POST, DELETE) |
| Network (patch bay) | `#network` | `/admin/network`, `POST /admin/expose`, `POST /admin/expose/stop` |
| API reference + Try it | `#api` | `/admin/models`, `/admin/info`, `POST /admin/try/chat` |
| Metrics: blocks, "what you should care about", charts | `#metrics` | `/admin/metrics?range=`, `/admin/series?range=`, `/admin/insights` |
| Pull, Ports, Benchmarks, Logs, Gateway, Daemon | `#pull` … | "coming soon" panels with their CLI command |

`⌘K` / `Ctrl K` opens the palette: every row shows its CLI twin, Enter runs it through the admin API, `⌘C`
copies the command; actions the console can't do (pull, serve a model that isn't registered) copy the command.

## Honest controls only
The sheet shows only settings mxc really has: **keep loaded** (pin) and **unload after idle**. Per-model
memory is not shown (MAX doesn't report it per model); the number strip shows GPU memory in total.

## States
- Empty: no models → "Get started" tile and an empty Models page with `mxc serve …`; no requests → hint with a curl.
- Loading: "Loading…" rows until the first poll answers.
- Error: daemon unreachable → a banner with `mxc serve` / `mxc daemon`; status bar says "daemon not answering".
- Narrow screens: under 900 px the menu becomes a drawer; under 640 px the sheet is full-width and grids stack.
  The patch bay scrolls sideways inside its panel.
- `prefers-reduced-motion`: CSS animations and transitions are off, and the scroll, cycle and count-up timers stop.

## Later (not in F-016)
Pull page (needs pull progress from the daemon), Benchmarks race (needs `mxc bench`), Gateway safeguard
counters, the "Ask MX Console" chat on Metrics, pinning tiles to a personal dashboard.
