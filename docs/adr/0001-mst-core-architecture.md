# 0001 · mst core: a supervisor between apps and MAX

Context: v1 must serve several small models through one secure endpoint, loading each
on demand and unloading it when idle. The old core (FastAPI, `notebooks/mxstudio`) owns
exactly one `max serve` process and is built around evidence-gate ceremony.

Decision: a fresh package `mst/` on FastHTML + fh-saas helpers (SQLite, logging).
- `mst` daemon (the "hypervisor") on 127.0.0.1:11435 owns one `max serve` child per
  loaded model, each on its own loopback port. It starts a model on its first request,
  stops it after `keep_alive` idle minutes unless pinned, and proxies OpenAI-compatible
  `/v1/*` by the `model` field.
- Request path: client → Caddy (HTTPS, `tls internal`, forward_auth to the daemon's key
  check) → LiteLLM (routing + config UI, master key only, no Postgres) → daemon → MAX.
  LiteLLM is optional: Caddy can point straight at the daemon.
- State: one SQLite file `~/.mst/mst.db` (models, keys). Weights stay in the HF cache.
- No Google login or fh-saas tenancy: a local appliance has one owner; the admin page
  is loopback-only and apps authenticate with per-app keys.

Consequences: the daemon is the only process owner, so on-demand loading, idle unload
and routing live in one place. Every request takes an extra loopback hop. The old
`notebooks/mxstudio` package stays until mst replaces it, then gets removed (with approval).
