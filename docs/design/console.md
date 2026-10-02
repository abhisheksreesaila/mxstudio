# Design handoff: operations console

Chosen: **option C, Azure Blade**, as the skeleton (canvas https://claude.ai/artifact/21NSyXKpzcrqanmqr8bEe5).
Later pages borrow B's gauges and benchmark comparison, restyled in these tokens.
Tokens and components: `design-system/DESIGN-SYSTEM.md`.

## Where it lives
Served by the daemon at `http://127.0.0.1:11435/console` (loopback-only, like `/docs`). Plain HTML,
CSS and JavaScript; the page calls the daemon's `/admin/*` routes with the `X-Mst-Admin: 1`
header (same origin, so no CORS). Nothing is reachable through `hotfire expose`.

## Pages (resource menu)
- Overview · Activity log
- Models: **Models** (grid + blade), Pull a model, Ports
- Access: API keys, Network (expose)
- Monitoring: Metrics, Benchmarks (v2), Logs
- Settings: Gateway (LiteLLM), Daemon

## Honest controls only
The blade shows only settings hotfire really has: **pin** and **unload after idle**. Device,
encoding and max length appear once the supervisor supports them (not as dead controls).

## States to build
- Empty: no models yet → the grid says so with a "Serve model" call to action.
- Loading: grid rows as skeleton lines; a model in `starting` shows elapsed time.
- Error: daemon unreachable → a full-width banner with `hotfire serve` / `hotfire daemon` hint;
  a crashed model's blade opens on the Logs tab with the log tail.
- Long content: long model names truncate with a title tooltip; blade body scrolls.
- Narrow screens: under 900px the blade becomes a full-width sheet over the grid and the
  resource menu collapses behind a menu button.

## Future direction (not v1)
Azure-style **custom dashboards**: pin any tile (a gauge, a model's throughput, a benchmark
chart) from any page onto your own dashboard. The blade + resource-menu shape is chosen
partly because it scales to that.
