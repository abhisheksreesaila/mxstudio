# MX Studio

Local models on Modular MAX that you can see into and reshape. `mst` serves small
models behind one secure, OpenAI-compatible endpoint, loads them on demand, unloads
them when idle, and writes a notebook that teaches you each one.

## Quickstart (`mst`)

```bash
pixi run mst serve qwen2.5-0.5b          # pull, start the daemon, load, print URL + key + snippets
pixi run mst serve smollm2-1.7b --pin    # stays loaded; others unload after 5 idle minutes
pixi run mst ls                          # models and live state
pixi run mst learn qwen2.5-0.5b          # notebook: model card, architecture, parameter breakdown, sample code
pixi run mst expose                      # https://<host>.local:8443/v1 via Caddy (TLS + key check) and LiteLLM
pixi run mst keys new my-app             # one key per app; mst keys ls / revoke
pixi run mst port <hf-repo>              # unsupported architecture: workspace + Modular's import-model skill
pixi run mst stop --all                  # unload everything and stop the daemon
```

The endpoint is `http://127.0.0.1:11435/v1` locally (API reference at `/docs`), and any
OpenAI client works with it. Architecture: [ADR 0001](docs/adr/0001-mst-core-architecture.md).
Brief: [docs/brief.md](docs/brief.md). Tickets: [docs/plan.md](docs/plan.md).

---

## Legacy core (`mxs`, being replaced by `mst`)


> ## 🎯 Mission
> **Make local AI simple:** take a supported Hugging Face or MAX model from
> discovery to a reproducible, authenticated endpoint—without hiding the MAX,
> hardware, evidence, or operator decisions underneath.

**One model, one command, a secure endpoint.** MX Studio is a terminal-first
local AI studio powered by Modular MAX and published through Caddy. It records
what ran, which immutable model revision was used, and what has actually been
verified.

## ✅ Current status

**Experimental, with a real local acceptance run completed.** MX Studio has
resolved and downloaded `Qwen/Qwen2.5-0.5B-Instruct` at immutable revision
`7ae557604adf67be50417f59c2c2f167def9a775`, generated content through MAX on
NVIDIA hardware, and installed active Nucleus and Caddy systemd services on
Arch/Omarchy. The Caddy HTTPS ingress has also answered an authenticated,
CA-verified request on the LAN address.

🔒 `up` never invokes `sudo` or changes the host. Service installation remains
an explicit human-approved step. 🌐 The final independent second-device DNS,
CA-trust, and HTTPS proof is still required before calling a LAN run `ready`.
Unsupported-model adaptation remains guided, not autonomous.

## Three ways in, one endpoint out

| Starting point | Intended workflow | Available today |
| --- | --- | --- |
| ✅ MAX-supported model | Resolve its checkpoint → download → validate → serve → HTTPS URL | `catalog`, `pull`, and `up` use durable run history. Real local MAX generation and server-side Caddy HTTPS proof have completed; second-device acceptance remains. |
| ✅ Hugging Face model supported by MAX | Inspect compatibility → use the supported MAX architecture → serve → HTTPS URL | General model references and immutable revisions are resolved by the run workflow; support checks become evidence only after actual generation. |
| 🧭 Hugging Face model not supported by MAX | Skill-driven inspect → implement/adapt → load → compile → generate → compare → fix/retry → serve only after validation | Guided architecture tools exist; autonomous implementation, repair, and evidence-enforced promotion do not. |

Already-supported checkpoints do not need an invented conversion step. Unsupported
architectures require a real MAX implementation, not just renamed weights or a scaffold.

## Try the current supported-model path

Install [Pixi](https://pixi.sh/), clone this repository, and run from its root.
`pixi run mxstudio` is the primary CLI; `pixi run mxs` remains a compatible alias.

```bash
pixi install
pixi run mxstudio download Qwen/Qwen2.5-0.5B-Instruct
pixi run mxstudio serve Qwen/Qwen2.5-0.5B-Instruct
pixi run mxstudio chat "Say hello in one sentence."
pixi run mxstudio logs --lines 80
pixi run mxstudio stop
```

This legacy path downloads weights and launches real MAX on suitable hardware.
It is a **localhost smoke test**, not the endpoint workflow.

## Durable model-to-endpoint commands

```bash
pixi run mxstudio catalog --json
pixi run mxstudio pull Qwen/Qwen2.5-0.5B-Instruct --no-wait
pixi run mxstudio runs list
pixi run mxstudio runs inspect RUN_ID
pixi run mxstudio up Qwen/Qwen2.5-0.5B-Instruct --local-only
```

`up` defaults to LAN mode and waits for the run; `--no-wait` only schedules it.
`--revision`, `--hostname`, and `--ca-file` pin or configure the request.
`local_ready` proves local generated content only. LAN runs can stop at
`needs_setup` for a first-time, explicit installer/DNS/CA handoff, then
`awaiting_client_verification` after server-side HTTPS proof. Neither is `ready`.
`up` never invokes sudo or applies system changes. See the
[endpoint guide](docs/MXSTUDIO_NUCLEUS.md) for approval and independent-client
verification.

### 🧪 Acceptance evidence so far

| Check | Result |
| --- | --- |
| 📦 Immutable model pull | Qwen2.5 0.5B Instruct, revision `7ae557604adf67be50417f59c2c2f167def9a775` |
| ⚡ MAX inference | Completed locally with authenticated streamed content on NVIDIA hardware |
| 🛡️ System services | `mxstudio-nucleus.service` and `mxstudio-nucleus-caddy.service` are active |
| 🔐 HTTPS ingress | Caddy internal-CA TLS served authenticated `/v1/models` on `192.168.50.226:443` |
| ⏳ LAN completion | Pending a separate LAN device's hostname mapping, CA trust, probe, and imported evidence |

The setup handoff uses a unique protected environment file per run and supplies
separate read-only dry-run, guarded environment-copy, and explicitly approved
installer commands. HTTPS verification checks the installed systemd service
identity/configuration against the run before probing the endpoint.

MAX catalog architectures can be queried as `catalog:<exact architecture>`,
for example `catalog:Qwen2` (case-insensitive exact architecture match). A sole
HF example resolves to its real repository ID with `source="catalog"`; no
example is reported as missing, multiple examples return ambiguous candidates
and require an exact `org/name` (never auto-select), and an unavailable installed
MAX catalog is reported as blocked.
Before `up` downloads, known HF dtype/quantization metadata is checked against
MAX encodings; unsupported/unknown architectures or clear encoding mismatches
block serving. `pull` can still fetch unsupported architecture/encoding weights
for later port work while retaining `supported=false`; missing safetensors blocks
the pull. Compatibility guidance is not runtime proof, and pull failures are
returned as structured results.

## 🌐 Finish LAN acceptance

The installed endpoint is `https://api.mxstudio.local/v1`. Keep TLS verification
enabled. On this server and on one **different** LAN device, make the name resolve
to `192.168.50.226` using your LAN DNS or a deliberate hosts-file entry. On the
client, trust the Caddy local CA at
`/var/lib/mxstudio-caddy/caddy/pki/authorities/local/root.crt`, then run the
endpoint probe and import the resulting client evidence. The deployment record,
not a local curl alone, is the source of truth for `ready`.

## Keep the core small

- **Core:** terminal-first, one managed MAX model, FastAPI, SQLite jobs, Caddy for HTTPS.
- **Optional:** browser Studio, notebooks, benchmarks, and advanced LiteLLM routing.
- LiteLLM is not needed to serve one model. It does not make this controller run multiple models concurrently.
- Linux deployment installer: Debian/Ubuntu and Arch/Omarchy x86_64. Apple-Silicon Linux/GPU support is not established.

## Documentation

- [Mission, current gaps, and completion criteria](docs/ENDPOINT_FIRST.md)
- [Installation, CLI, apps, and optional Studio experiences](docs/USER_GUIDE.md)
- [Authenticated endpoint, Caddy, operations, and troubleshooting](docs/MXSTUDIO_NUCLEUS.md)
- [Architecture and module ownership](docs/MXSTUDIO_ARCHITECTURE.md)

Project licensing has not yet been declared. Model weights retain their publishers' licenses.
