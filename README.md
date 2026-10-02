# Hotfire (`hotfire`)

Local models on **Modular MAX** that you can see into and reshape.

`hotfire` serves small models behind **one secure, OpenAI-compatible endpoint**. It loads each
model on its first request, unloads it when idle, and writes a notebook that explains the
model. If a model's architecture isn't supported yet, it sets up a workspace to port it with
Modular's own skills.

- **As easy as Ollama:** `hotfire serve qwen2.5-0.5b` gives you a working endpoint.
- **Safe on your network:** HTTPS through Caddy, one revocable key per app, LiteLLM for routing.
- **Not a black box:** `hotfire learn` shows the architecture, a parameter breakdown, and where
  MAX's Python implementation of the model lives so you can read and change it.

Brief: [docs/brief.md](docs/brief.md) · Tickets: [docs/plan.md](docs/plan.md) ·
Architecture decision: [docs/adr/0001](docs/adr/0001-mst-core-architecture.md)

---

## How it works

```
 your apps ──HTTPS──▶ Caddy :8443 ──▶ LiteLLM :4000 ──▶ hotfire daemon :11435 ──▶ max serve (one per model)
 (LAN or local)        │  TLS (own CA)     routing by        on-demand start,     loopback ports,
                       │  key check ───────▶ /auth/check      idle unload, pin     served by hotfire name
                       └ only /v1/models, /v1/chat/completions, /v1/completions, /v1/embeddings
```

| Piece | What it does | Code |
|---|---|---|
| **Resolver** | `qwen2.5-0.5b` → HF repo → architecture → is it in MAX's catalog (`max list --json`)? Downloads safetensors at a pinned commit. | `hotfire/resolve.py` |
| **Supervisor** (the "hypervisor") | One `max serve` child per model on a free loopback port. Starts it on demand (concurrent callers share one start), stops it after `keep_alive` idle (default 5m) unless pinned, never mid-request. Cleans up orphans after a crash. | `hotfire/supervisor.py` |
| **Gateway** (`hotfire daemon`) | OpenAI-compatible `/v1/*` on `127.0.0.1:11435`. Checks the Bearer key, routes by `model`, streams through, and serves the API reference at `/docs` (Scalar). Admin routes are for the CLI only. | `hotfire/gateway.py` |
| **Keys** | `mst-…` keys, one per app, stored only as SHA-256 hashes. Shown once. | `hotfire/keys.py` |
| **Expose** | Writes and runs Caddy (`tls internal`, forward_auth) and LiteLLM (its own pixi env, master key, no Postgres) as your user. | `hotfire/expose.py` |
| **Learn** | A Jupyter notebook built only from the Hugging Face API: model card, config table, parameter breakdown, sample code, and a pointer to MAX's source. | `hotfire/learn.py` |
| **Console** | `http://127.0.0.1:11435/console`: Azure-style operations console (resource menu, models grid, settings blade). This machine only. | `hotfire/console/` |
| **Port** | For unsupported architectures: clones `modular/skills`, picks a donor architecture, and writes `PORT.md` plus the `claude '/import-model …'` command. | `hotfire/port.py` |

State lives in `~/.hotfire/` (override with `HOTFIRE_HOME`; the old `MST_HOME` still works): `mst.db` (models, key hashes), `logs/`,
`run/`, `notebooks/`, `ports/` and `expose/`. Weights stay in the Hugging Face cache.

---

## Commands

```bash
pixi run hotfire serve qwen2.5-0.5b              # pull if needed → start daemon → load → print URL, key, curl, Python
pixi run hotfire serve smollm2-1.7b --pin        # never unloads
pixi run hotfire serve qwen2.5-1.5b --keep-alive 10m
pixi run hotfire ls                              # registered models + live state (cold/starting/ready/crashed)
pixi run hotfire stop qwen2.5-0.5b               # unload one now
pixi run hotfire stop --all                      # unload everything, stop the daemon

pixi run hotfire pull <name-or-hf-repo> [--dry-run] [--name N]   # resolve + download only
pixi run hotfire keys new my-app | ls | revoke my-app            # one key per app (printed once)
pixi run hotfire learn qwen2.5-0.5b [--no-open]                  # notebook → Jupyter Lab
pixi run hotfire expose [--host box.local] [--no-litellm] | --stop
pixi run hotfire port <hf-repo>                                  # workspace for an unsupported architecture
pixi run hotfire daemon                                          # the gateway in the foreground (hotfire serve starts it for you)
```

Short names: `qwen2.5-0.5b`, `qwen2.5-1.5b`, `qwen2.5-3b`, `smollm2-1.7b`, `all-minilm-l6`,
and `llama3.2-1b` (gated: accept the license on HF and set `HF_TOKEN`). Any HF repo id
works too.

---

## Morning checklist: verify it for real

Everything below has been built and reviewed, and 175 tests pass against a **fake** `max`. Steps 3 to 6
are the first runs on real MAX and real hardware. Tick them off in order; each says what you
should see.

### 0. Free memory
Stop the other agents. Three small models need about 8 GB of GPU memory in total (you have
2× RTX 3090), plus some system RAM while they load.

### 1. Retire the old Nucleus services (needs your password)
The legacy `mxs` stack still runs as two systemd services on ports 443 and 8080:

```bash
sudo systemctl disable --now mxstudio-nucleus-caddy.service mxstudio-nucleus.service
sudo rm /etc/systemd/system/mxstudio-nucleus.service /etc/systemd/system/mxstudio-nucleus-caddy.service
sudo systemctl daemon-reload
sudo rm -r /etc/mxstudio        # old deployment env files, Caddyfile, install manifests
```
✅ `systemctl status mxstudio-nucleus` says *could not be found*, and `ss -ltn | grep ':443 '` is empty.

### 2. Tests (no GPU)
```bash
pixi run pytest tests/hotfire -q
```
✅ `175 passed`.

### 3. One model, local endpoint
```bash
pixi run hotfire serve qwen2.5-0.5b
```
✅ You see `pulled …`, `started the hotfire daemon`, `loading …`, `qwen2.5-0.5b is ready (Ns)`, then the
endpoint `http://127.0.0.1:11435/v1`, a key `mst-…` (**save it**: `export HOTFIRE_API_KEY=mst-…`), and
a curl and a Python snippet. Paste the curl and expect a JSON reply.
✅ Open http://127.0.0.1:11435/docs and you should see the API reference.
✅ Open http://127.0.0.1:11435/console: the operations console lists your models. Click one to open its
blade: Overview, Settings (pin, unload after idle; Apply saves) and Logs.
❌ If loading fails: the error includes the tail of `~/.hotfire/logs/qwen2.5-0.5b.log`.

### 4. Several models, on demand
```bash
pixi run hotfire serve smollm2-1.7b --pin
pixi run hotfire serve qwen2.5-1.5b --keep-alive 2m
pixi run hotfire ls
```
✅ `ls` shows all three as `ready`. Wait 2–3 minutes, run `hotfire ls` again, and `qwen2.5-1.5b`
is `cold`, the others aren't. Call it again (curl with `"model": "qwen2.5-1.5b"`) and it comes
back after a cold start.

### 5. Secure LAN endpoint
```bash
pixi run hotfire expose
```
✅ It prints `https://<your-host>.local:8443/v1` and the path to Caddy's `root.crt`. Then:
```bash
curl --cacert ~/.local/share/caddy/pki/authorities/local/root.crt \
  https://$(hostname).local:8443/v1/models -H "Authorization: Bearer $HOTFIRE_API_KEY"
```
✅ This lists your models. With a wrong key you get `401`, and
`https://…:8443/model/info` gives `404`, because LiteLLM's admin API is never exposed.
✅ LiteLLM's own UI is at http://127.0.0.1:4000/ui (this machine only).

### 6. The whole acceptance run in one script
```bash
pixi run python scripts/hotfire_acceptance.py
```
✅ It ends with `N/N checks passed`. That covers chat and streaming on all three models over HTTPS, all three at
once, a bad key refused, idle unload and reload, and a pinned model staying up. It prints a curl line for
a **second device**: copy `root.crt` there, run the line, and you get a reply. That proves the LAN path.

### 7. Learn and port
```bash
pixi run hotfire learn qwen2.5-0.5b          # opens Jupyter Lab; run the "Try it" cells with HOTFIRE_API_KEY set
pixi run hotfire port allenai/OLMoE-1B-7B-0125-Instruct   # prints the donor, workspace and Claude command
```
✅ The notebook shows the model card, a config table, a parameter breakdown (estimate ≈ actual)
and MAX's source folder for the architecture. `port` prints a workspace with `PORT.md`.

### When you're done
```bash
pixi run hotfire expose --stop && pixi run hotfire stop --all
```

---

## Development

- New code is in `hotfire/`; tests are in `tests/hotfire/` and use a fake `max` (`tests/hotfire/fake_max.py`), so they
  need no GPU. Run them with `pixi run pytest tests/hotfire -q`.
- LiteLLM lives in its own pixi environment: `pixi install -e gateway`.
- The legacy core (`notebooks/mxstudio`, `mxs` command, old docs) is still in the repo until
  `hotfire` has passed the live checks above. It will be removed after that, with your OK.
- Ports: daemon `HOTFIRE_PORT` (11435), LiteLLM `HOTFIRE_LITELLM_PORT` (4000), HTTPS `HOTFIRE_EXPOSE_PORT` (8443). The old `MST_*` names are still read as a fallback.
