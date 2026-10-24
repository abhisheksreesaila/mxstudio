# How omxconsole works

The moving parts, and how to develop on them. Using it: [user guide](USER_GUIDE.md) · [HTTP API](API.md) · [CLI reference](CLI.md).

## How a request travels

```
 your apps ──HTTPS──▶ Caddy :8443 ──▶ LiteLLM :4000 ──▶ mxc daemon :11435 ──▶ max serve (one per model)
 (LAN, after expose)   TLS, key check     routing          start on demand,     loopback ports
                                                            unload when idle
 local apps ─────────────────────────────────────────────▶ (same daemon, http://127.0.0.1:11435)
```

- **Daemon** (`mxconsole/gateway.py`, `supervisor.py`): checks the key, starts the model if it's cold (concurrent
  callers share one start), streams the answer back, and stops idle models, never in the middle of a request.
- **Keys** (`keys.py`): `mst-…`, stored only as SHA-256 hashes, shown once.
- **Expose** (`expose.py`): runs Caddy (its own certificate authority) and LiteLLM as your user. Only the four `/v1`
  routes reach the daemon from the LAN.
- **Port** (`port.py`, `port_auto.py`, `port_try.py`): a sandboxed Claude Code run, then omxconsole's own check
  against Hugging Face before anything is registered. [Step by step](https://abhisheksreesaila.github.io/mxstudio/site/porting.html) ·
  [PORTING.md](PORTING.md).
- **State** lives in `~/.mxc/` (or `MXC_HOME`). Weights stay in the Hugging Face cache.

## Development

```bash
pixi run pytest tests/mxconsole -q      # uses a fake `max`, so it needs no GPU
```

Checking it on real hardware, step by step: [docs/VERIFY.md](VERIFY.md). Plans and decisions:
[brief](brief.md) · [tickets](plan.md) · [architecture](adr/0001-mst-core-architecture.md). The
older MX Studio (`mxs`) code is still in `notebooks/` until omxconsole replaces it; its guide is
[LEGACY_NUCLEUS.md](LEGACY_NUCLEUS.md).
