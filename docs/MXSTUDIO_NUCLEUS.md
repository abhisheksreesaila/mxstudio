# MX Studio Nucleus

Nucleus extends the existing Studio controller; it does not introduce a second
MAX process owner. The simplest profile is **Caddy → Nucleus → MAX**. LiteLLM and
PostgreSQL are optional when satellite apps need separate virtual keys.

**Implementation status (2026-09-25):** durable runs, CLI/API adapters, installer
handoffs, and distinct server/client evidence states are implemented and have
hermetic coverage. No live Hugging Face download, MAX inference, distro install,
or LAN/client TLS acceptance was performed for this change. This is not a claim
that all plan-A live acceptance is complete. Unsupported-model port automation
(B) and gateway endpoint inventory/routing (C) are deferred.

## Recommended stack: FastAPI + SQLite, with MAX

**Yes, SQLite is a good fit for the current single-machine controller, and it is
already used.** PostgreSQL was included to support the optional LiteLLM
virtual-key configuration, not because Nucleus needs a database server.

| Layer | Current implementation | Required for the simple path? |
| --- | --- | --- |
| HTTP backend | FastAPI + Uvicorn; not FastHTML | Yes |
| Browser | Static HTML/CSS/JavaScript | No |
| Terminal | Typer commands over the same domain operations | Yes for this guide |
| Durable background jobs | SQLite at `$MXSTUDIO_HOME/jobs.db` (default `~/.mxstudio/jobs.db`) | Yes; created automatically |
| Process state and benchmark history | JSON at `$MXSTUDIO_HOME/state.json` | Yes; created automatically |
| Model weights | Hugging Face cache, configurable with `HF_HOME` | Yes |
| Inference engine | One managed MAX server | Yes |
| HTTPS ingress | Caddy | Only for the supplied LAN HTTPS profile |
| Per-app virtual keys | Optional LiteLLM gateway + PostgreSQL | No |
| LAN service advertisement | Optional Zeroconf/mDNS | No |

The `fh-saas` integration provides background-job interfaces and logging helpers.
Its presence does not make this a FastHTML backend. Switching web frameworks would
not itself improve model readiness or remove LiteLLM's configured database needs.

### Why not use SQLite for everything?

Keep SQLite for local jobs. The **supplied gateway profile** explicitly configures
PostgreSQL for LiteLLM's gateway records, and its installer validates a PostgreSQL
connection URL. Changing that URL to SQLite is not a supported substitution here.
This is a constraint of the integration shipped in this repository, not a claim
that API gateways or FastAPI generally require PostgreSQL.

Without LiteLLM, Nucleus checks one environment-provided controller key. It does
not store app keys in SQLite or implement per-app revocation, quotas, or budgets.
A native SQLite-backed app-key service could be a future simplification, but has
not been built. **Recommendation: use the core profile first; add the gateway only
when those extra capabilities are needed.** No database or framework changes are
required to try the current core.

## Durable deployment workflow

The terminal path is the primary interface; the Studio GUI is not required.
These commands create durable deployment runs and retain revision/stage evidence:

```bash
pixi run mxs catalog --json
pixi run mxs pull org/model --revision COMMIT_SHA
pixi run mxs runs list
pixi run mxs runs inspect RUN_ID
pixi run mxs up org/model --local-only
```

`pull` resolves and downloads a pinned checkpoint. `up` defaults to LAN mode and
waits for the run; `--no-wait` returns after scheduling. `--revision` can pin an
immutable revision, while `--hostname` and `--ca-file` configure HTTPS identity
and trust. Existing `download`, `serve`, `nucleus prepare`, and `nucleus run`
commands remain available as lower-level workflows.

MAX architecture catalog lookup uses `catalog:<exact architecture>`, e.g.
`catalog:Qwen2` (case-insensitive, exact match). If that architecture has one HF
example, the resolver uses its actual repository ID and sets `source="catalog"`.
No example produces an actionable `missing` state; multiple examples produce an
`ambiguous` response with candidates and require an exact HF `org/name`—the
resolver never auto-selects. Unavailable `max list --json` data produces an
actionable `blocked` result rather than a guessed fallback.
Before `up` downloads, known HF dtype/quantization metadata is checked against
normalized MAX encodings; unsupported/unknown architectures or clear encoding
mismatches block serving before fetch. `pull` permits unsupported
architecture/encoding weights for later port work while retaining
`supported=false`; missing safetensors artifacts block the pull. These checks
are guidance, not proof of runtime support, and direct pulls return structured
blocked/error records.

Run states describe evidence, not optimistic milestones:

| State | Meaning |
| --- | --- |
| `downloaded` | The selected snapshot/revision was downloaded and validated; no MAX load or generation claim. |
| `local_ready` | Pinned MAX load and authenticated local generated-content probe passed. No LAN claim. |
| `needs_setup` | First-time service installation or hostname/CA setup needs an operator. It is a handoff, **not success**. `mxs up` never invokes sudo or applies host changes. |
| `awaiting_client_verification` | Authenticated HTTPS generation and route-isolation checks passed from the server, but an independent client proof is still required. Not `ready`. |
| `ready` | Server evidence and a fresh independent-client report were accepted for the endpoint's model/revision. |
| `blocked`, `failed`, `interrupted`, `stopped` | An explicit blocker, operation failure, lost worker, or owned-resource stop; inspect recovery/run history before acting. |

The CLI prints the run ID before waiting. `mxs runs inspect RUN_ID` and
`mxs runs resume RUN_ID` inspect or resume it; resume reuses the recorded model
and options rather than silently selecting a new revision.

### First-time setup is an explicit operator decision

For a LAN run, `needs_setup` contains a private per-run environment file under
`$MXSTUDIO_HOME/deployments/<RUN_ID>/nucleus.env` and recovery instructions. The
key is never printed. Review the file locally; do not copy or share its contents.
The setup handoff supplies three exact commands:

1. `dry_run_command` runs the installer with `--dry-run`, the unique destination
   `/etc/mxstudio/deployments/<RUN_ID>/nucleus.env`, `--service-user-current`,
   and the selected private service group. Run it first; it is read-only and
   does not require sudo.
2. After reviewing the plan, `environment_install_command` uses a
   `sudo test ! -e ...` guard and `sudo install -D` to copy the per-run file to
   its unique destination, owned by `root:<service-group>` with mode `0640`.
   If the destination exists, stop and compare it privately; never overwrite it.
3. Only after explicit operator approval, run the supplied `install_command`,
   which uses `sudo bash ... --apply` and the same `--env-file`,
   `--service-user-current`, and `--service-group` options. It is not emitted
   with `--install` or a named `--service-user`.

Use the exact values and commands shown in that run record rather than replacing
the unique path with `/etc/mxstudio/nucleus.env`. The selected current account is
the service user; the service group must be private to that account. The installer
supports Debian/Ubuntu and Arch/Omarchy x86_64. `up` does not invoke any of these
commands. Resolve the advertised hostname and configure Caddy CA trust separately;
neither DNS/mDNS nor trust is changed automatically.

After an approved installation and restart, inspect the same run with
`mxs runs resume RUN_ID` or `mxs endpoint verify RUN_ID --ca-file CA_PATH`.
Before HTTPS probing, verification checks that the installed Nucleus systemd
service has exactly one protected environment file and that its loaded service
user/group, active state, `MXSTUDIO_HOME`, `HF_HOME`, model, revision, and hostname
match this run. A missing or mismatched identity returns the run to `needs_setup`.
Only after those checks pass does verification check TLS and perform a real
streamed response from the server, leaving the run in
`awaiting_client_verification`. The CLI command performs the check directly; the
authenticated API counterpart queues it and returns a job ID.

### Independent-client proof

The controller key is loaded from `MXSTUDIO_NUCLEUS_API_KEY`, or created at
`$MXSTUDIO_HOME/endpoint.key` (normally `~/.mxstudio/endpoint.key`) with owner-only
permissions. Keep it in a secret manager or protected environment; never put it
in a command argument, proof file, or shared logs.

On a second client, set the key in that machine's environment without echoing it.
Use the public canonical `id` and pinned `revision` from authenticated
`GET /v1/models`. MAX's backend-advertised model ID is an internal routing detail
and is not exposed as the public model ID.

```bash
pixi run mxs endpoint probe \
  --url https://api.mxstudio.local/v1 \
  --model CANONICAL_MODEL_ID \
  --ca-file /path/to/trusted-caddy-ca.crt > client-proof.json
```

The probe requires HTTPS, validates the CA, checks authentication and route
isolation, and verifies nonempty streamed generation. It prints machine-readable
proof without a key. Transfer that JSON proof to the appliance through a trusted
operator channel, then import it there while the authenticated loopback Nucleus
API is available:

```bash
pixi run mxs endpoint accept-client RUN_ID --evidence client-proof.json
```

The file is limited to 64 KiB. At acceptance, the controller validates
endpoint/model/revision binding, a different machine fingerprint,
server-before-client timestamps, evidence freshness (24 hours), clock skew (at
most 300 seconds into the future), and a 300-second probe time budget. `ready`
records that accepted evidence; it is not a continuous health monitor. Accepted provenance is
`trusted_operator_report`: this is an operator-imported probe record, **not
cryptographic remote attestation**. Protect the proof file and remove it when no
longer needed.

### CLI and private management API

Additional commands:

```text
mxs runs list [--json]
mxs runs inspect RUN_ID [--json]
mxs runs resume RUN_ID [--no-wait] [--json]
mxs endpoint list [--json]
mxs endpoint verify RUN_ID [--ca-file CA_PATH] [--json]
mxs endpoint stop RUN_ID [--json]
mxs endpoint probe --url HTTPS_BASE_URL --model ID [--ca-file PATH]
mxs endpoint accept-client RUN_ID --evidence JSON_PATH
```

All `/nucleus/*` management routes require the controller key and are private;
Caddy exposes only `GET /v1/models` and `POST /v1/chat/completions`, never run,
verify, stop, or client-evidence management:

| Route | Result |
| --- | --- |
| `POST /nucleus/runs` | Creates/enqueues a run; HTTP 202 with `run_id` and `job_id`. Body: `query`, optional `revision`, `mode` (`pull`, `local`, `lan`), `hostname`, and `ca_file`. |
| `GET /nucleus/runs`, `GET /nucleus/runs/{id}` | List or inspect durable run records and stage evidence. |
| `POST /nucleus/runs/{id}/resume` | Re-enqueues a validated existing run; HTTP 202 with IDs. |
| `GET /nucleus/endpoints` | Lists recorded endpoints and their distinct evidence. |
| `POST /nucleus/endpoints/{id}/verify`, `/stop` | Queue endpoint verification/owned-resource stop; HTTP 202 with `run_id` and `job_id`. Poll the run record for its resulting state. |
| `POST /nucleus/endpoints/{id}/client-evidence` | Authenticated local import of the probe JSON; validates the proof before recording it. Never route through Caddy. |

`GET /v1/models` keeps the canonical HF identity as its public `id` and
`model_id` metadata, and includes the pinned `revision`. Chat accepts the
canonical ID and maps it to MAX's learned backend-advertised ID internally; the
backend load-path-derived ID is not a client-facing identifier.

## Manual local Nucleus profile

Run these Bash commands from the checkout. They download a starter model into
this user's Hugging Face cache and start real MAX; packages/weights require
network access and sufficient disk/memory. Stop a Studio listener already using
8080 before launching Nucleus. No database server or root access is needed:

```bash
pixi install
pixi run mxs download Qwen/Qwen2.5-0.5B-Instruct
export NUCLEUS_MODEL_ID=Qwen/Qwen2.5-0.5B-Instruct
read -rsp 'Paste a long random API key saved in your password manager: ' MXSTUDIO_NUCLEUS_API_KEY; echo
export MXSTUDIO_NUCLEUS_API_KEY
pixi run mxs nucleus prepare --timeout 600
# Continue only after preparation reports ready.
pixi run mxs nucleus run --no-prewarm
```

Keep the key in a protected environment or secret store. The launcher requires
both variables, binds **127.0.0.1:8080**, permits one worker, and queues a durable
preparation job at startup. `--no-prewarm` skips that job. MAX binds
**127.0.0.1:8100** using `MAX_SERVE_HOST` (the pinned MAX CLI has no `--host` flag).
Do not expose the ordinary `mxs ui` listener or MAX directly to a LAN.

The sequence above prepares before starting the HTTP listener. Alternatively,
`pixi run mxs nucleus run` alone queues preparation at startup; while that job is
running, requests or competing preparation commands may return busy errors.

In another terminal using the same user, state/cache paths, and exported model/key
values (terminal environments are not shared automatically):

```bash
pixi run mxs nucleus status
pixi run mxs nucleus prepare --timeout 600
pixi run mxs nucleus lesson --output nucleus-ttft.ipynb
```

`nucleus start` is an alias for synchronous `prepare`. The editable notebook
measures first **content-token** latency and total duration only when executed.
It retains request parameters, hardware/version notes, and raw SSE evidence,
without including the API key. Generation is deterministic and refuses to
overwrite hand edits. A first request is not automatically a cold-compile
measurement; caches and prewarming must be recorded explicitly.

`pixi run mxs nucleus render-config` prints a non-secret profile manifest.
`--output profile.json` writes it to a new file and refuses to overwrite an
existing file. This manifest does not install or replace deployment templates.

## Connect an app and verify a real response

Use the [copy-paste streaming client in the user guide](USER_GUIDE.md#2-make-a-real-streaming-request).
For an OpenAI-compatible app, configure:

- **Base URL:** `http://127.0.0.1:8080/v1` on the appliance, or
  `https://api.mxstudio.local/v1` after LAN HTTPS setup.
- **Model:** `nucleus-local` (alias for `NUCLEUS_MODEL_ID`).
- **Key:** the controller key for core; a restricted app key for the gateway.

Choose chat-completions mode, not the OpenAI Responses API. Embeddings, speech,
transcription, images, and arbitrary `/v1/*` paths are not exposed by this profile.
A voice application needs its own speech-to-text/text-to-speech components; mDNS
advertises an endpoint but does not build or automatically configure that app.

A model-list response proves authentication and HTTP connectivity only. A real
chat request must produce content tokens before you mark inference as working.
For the core profile, this unauthenticated probe should return **401**:

```bash
curl --silent --output /dev/null --write-out '%{http_code}\n' http://127.0.0.1:8080/v1/models
```

The notebook kernel must also receive `MXSTUDIO_NUCLEUS_API_KEY`. If VS Code was
started before the terminal export, add a temporary setup cell **before** the
notebook's environment-reading cell; do not paste a literal key into notebook code:

```python
import getpass
import os
os.environ["MXSTUDIO_NUCLEUS_API_KEY"] = getpass.getpass("Nucleus API key: ")
```

Use the project environment's Python kernel. `NUCLEUS_ENDPOINT` in the notebook
is the server root, such as `http://127.0.0.1:8080`, **without** `/v1`.

## API and lifecycle contract

All Nucleus routes require `Authorization: Bearer <key>` or `X-API-Key`.
Only these inference routes are exposed by either Caddy profile:

- `GET /v1/models`: canonical public model `id`, canonical `model_id` metadata,
  and immutable `revision`, with no preparation side effects.
- `POST /v1/chat/completions`: streaming or non-streaming OpenAI-compatible chat.

Chat accepts the canonical public ID, leases the pinned managed model, and maps
to the backend-advertised ID only on the private MAX hop. `nucleus-local` and
`local` resolve to `NUCLEUS_MODEL_ID`. MAX compatibility is still required; a
downloaded checkpoint or scaffold is not proof of support.

Private loopback management routes, **never routed through Caddy**:

- `GET /nucleus/status`: server state and this process's readiness/counters.
- `POST /nucleus/prepare`, body `{"model":"...","timeout_s":600}`: returns
  HTTP 202 and `job_id` using the existing detached job worker.
- `GET /nucleus/jobs/{job_id}`: durable preparation progress/result.
- Deployment run, endpoint, verify/stop, and client-evidence routes described
  above. They require the same controller authentication and are not reachable
  through either Caddy profile. Poll run records after queued verify/stop jobs.

Shared filesystem reservations protect Nucleus requests, Studio chat/agents,
CLI chat, and managed benchmarks for their entire execution. Preparation and
all legacy start/stop paths take an exclusive reservation under `MXSTUDIO_HOME`.
Use the **same service user, state directory, and cache** for API, CLI, and jobs.
Different state directories cannot coordinate the same fixed MAX port. Requests
sent directly to MAX, including arbitrary external notebooks, are not tracked.

Busy transitions return HTTP 503 with retry guidance (legacy management returns
409); they do not terminate active inference or implement an unbounded drain
queue. The controller bounds pending preparations to 32 per process. Same-process
preparations are single-flight; cross-process contention asks callers to retry.
Normal chat waits up to 30 seconds for readiness. Explicit preparation permits
up to 600 seconds. A deadline returns an error but leaves MAX loading; inspect
status or prepare again rather than repeatedly restarting it. Cancelled callers
do not cancel the bounded preparation worker. Stream disconnects release leases.

Successful OpenAI SSE passes through unchanged: there are no invented
"compiling graph" or "loading weights" token events. Upstream errors received
before streaming retain their HTTP status. Once headers have been sent, a
transport failure ends the stream; it cannot retroactively change HTTP status.
`active_leases`, pending counts, and readiness history are process-local; durable
job records and server state provide cross-process evidence.

A stored PID is signalled only when its saved process identity can be verified.
Legacy state without `process_identity` is deliberately refused: inspect and
stop the old MAX instance explicitly before migrating. Do not delete state to
bypass an active/unverified process. CLI-launched MAX may outlive the API;
`mxs stop` deliberately stops it once reservations are released.
When switching models for a non-service endpoint, MX Studio stops only its
tracked controller under the lifecycle reservation before changing the managed
MAX model. Existing systemd service configuration is never silently rewritten.

## Optional systemd installation

The installer is **read-only by default**:

```bash
./deploy/nucleus/install.sh --dry-run --with-caddy
```

Installation supports Debian/Ubuntu and Arch/Omarchy **x86_64**. The Linux
installer does not support Apple Silicon Linux; do not infer GPU/hardware support
from the presence of deployment files.

The commands below describe a standalone/manual installation. A deployment run's
`needs_setup` handoff instead uses a unique protected environment path under
`/etc/mxstudio/deployments/<RUN_ID>/nucleus.env`; follow its generated commands
and do not substitute the shared manual-install path.

Before explicit installation:

1. Provide a dedicated checkout readable/traversable by the non-root `mxstudio`
   user. Its `.pixi` environment must be writable by that user. Install Pixi at
   a shared executable path visible to `sudo` and the service user.
2. Populate the selected absolute `--env-file` path (default
   `/etc/mxstudio/nucleus.env`) from [the example](../deploy/nucleus/nucleus.env.example)
   or the per-run protected setup handoff. It must be owned `root:GROUP` with
   mode 0640. The installer creates the system user/group if absent but never
   generates or overwrites secrets.
   Use one literal assignment per line, optionally quoted; no multiline values,
   escapes, or shell expansion. URL-encode PostgreSQL passwords.
3. Download the selected model as the service user with
   `HF_HOME=/var/cache/mxstudio/huggingface`. An existing interactive user's cache
   is not automatically copied. Installation runs `pixi install --locked`, not
   obsolete Modular installers, and does not download model weights.
4. Ensure ports 8080/8100 are unused by another Studio/MAX instance. For Caddy,
   ensure 80/443 are available. Existing active/enabled host Caddy causes a clear
   refusal: deliberately integrate the template yourself or disable it first.

After completing those prerequisites and reviewing the dry-run plan, explicitly
apply the core HTTPS profile:

```bash
sudo ./deploy/nucleus/install.sh --apply --with-caddy \
  --service-user mxstudio --service-group mxstudio \
  --env-file /etc/mxstudio/nucleus.env
# Add --with-gateway only after provisioning PostgreSQL and gateway credentials.
```

The dry-run needs no sudo. The installer's legacy `--install` alias remains
available for manual use; deployment-run handoffs emit `--apply`. Review the
plan and environment file before either explicit apply form.

The units use `/var/lib/mxstudio` for state and `/var/cache/mxstudio` for cache.
Existing units, Caddyfiles, and environment files are preserved; profile upgrades
or switching core ↔ gateway require deliberate config review, not a silent
replacement. Fresh apt Caddy's stock service is disabled before starting the
separate `mxstudio-nucleus-caddy.service`. Its CA persists under
`/var/lib/mxstudio-caddy/caddy/pki/authorities/local/`. Installation probes the
controller (and optional gateway) for authenticated HTTP responsiveness; inspect
the preparation job and perform real inference before declaring a model ready.

## Optional LiteLLM virtual keys and cloud routing

`--with-gateway` installs [pinned proxy extras](../deploy/nucleus/litellm-requirements.txt)
in a separate distro-Python virtualenv, listening on **127.0.0.1:4000**. An
existing PostgreSQL database and `DATABASE_URL`/`LITELLM_MASTER_KEY` are required;
the installer does not provision a database, accounts, or automatic cloud access.
For a local PostgreSQL server, an administrator can create a dedicated login and
database (enter the password interactively):

```bash
sudo -u postgres createuser --pwprompt mxstudio_litellm
sudo -u postgres createdb --owner=mxstudio_litellm mxstudio_litellm
```

Configure the server's authentication rules appropriately and set
`DATABASE_URL=postgresql://mxstudio_litellm:URL_ENCODED_PASSWORD@127.0.0.1:5432/mxstudio_litellm`
in the protected file. Use LiteLLM's **private** `POST /key/generate` endpoint
with the master key to issue keys limited to `nucleus-local`; give satellites
those keys, not either master/controller key. Caddy never exposes key generation.
See [LiteLLM virtual-key documentation](https://docs.litellm.ai/docs/proxy/virtual_keys).

The default gateway has **no cloud fallback** and disables message/spend logging.
The separate [cloud example](../deploy/nucleus/litellm_config.cloud.example.yaml)
provides an explicitly opted-in `hybrid-local` alias with an error fallback to
`cloud-gpt-4o`, plus a direct cloud alias. Review data handling/costs and supply
`OPENAI_API_KEY` before selecting that config in the gateway unit. `nucleus-local`
remains local-only. Fallback responds to routing failures; it does not predict
whether a request exceeds GPU capacity.

## HTTPS and discovery

[Caddy core](../deploy/nucleus/Caddyfile) routes the two exact method/path pairs to
8080; [gateway](../deploy/nucleus/Caddyfile.gateway) routes the same pairs to 4000.
Both use `tls internal`, default hostname `api.mxstudio.local`, and unbuffered SSE.
Set `NUCLEUS_HOSTNAME` to change the hostname. Public ACME DNS/TLS is a separate
operator configuration, not an automatic promise for a `.local` domain.

Install/trust **only the CA certificate** on every client; never distribute its
private key. Caddy's local CA does not automatically become trusted on phones or
other LAN computers. Do not bypass certificate validation in production. Configure
DNS/hosts explicitly when multicast name resolution is unavailable.

Optional mDNS advertises `_mxstudio._tcp.local.` with TXT `v=1` and `path=/v1`.
Install the optional dependency in this project's environment, not globally:

```bash
pixi add --pypi zeroconf
```

Set `NUCLEUS_DISCOVERY_HOST=api.mxstudio.local`,
`NUCLEUS_DISCOVERY_ADDRESS=<this appliance's LAN IPv4 address>`, and optionally
`NUCLEUS_DISCOVERY_PORT=443`. The address belongs to the HTTPS ingress, not MAX.
Discovery is disabled by default; missing dependencies/registration failures fall
back to manual connection. It supplies neither trust nor authentication and does
not guarantee discovery across VLANs. No network interfaces are modified.

## Troubleshooting

Start with `pixi run mxs nucleus status` and `pixi run mxs logs --lines 80`.
CLI status reads persisted server state, but its readiness/lease counters belong
to that CLI process; use authenticated `GET /nucleus/status` on loopback to inspect
the API process's counters. Do not infer that zero CLI leases means no API clients.

| Symptom | Meaning / next action |
| --- | --- |
| Connection refused on 8080 | Start `nucleus run`; `nucleus prepare` starts MAX, not the HTTP controller. |
| Port 8080 already in use | Stop the previous Studio listener, or choose another Nucleus `--port` and update the client URL. Supplied deployment templates assume 8080. |
| HTTP 401 | Paste the same controller key into the core client; gateway clients need a LiteLLM app key instead. Check which profile/port you are calling. |
| Model not downloaded | Download it under the same user/cache used by MAX. A desktop user's cache is not automatically the service user's cache. |
| HTTP 503 / busy | An inference reservation or preparation is active, or the readiness deadline elapsed. Let it finish, inspect the preparation job/logs, then retry. Do not delete the lock file to bypass it. |
| Chat cold start exceeds 30 seconds | Prepare explicitly with `--timeout 600`, inspect logs, then retry once ready. A timeout may leave MAX loading. |
| HTTP 502 or 504 | Check MAX status/logs and its load/runtime errors; these codes are not evidence of a working model. |
| Listing works but generation fails | Listing does not load or validate a model. Inspect MAX errors, architecture support, available memory, and the actual request. |
| Model switch refused | Finish or cancel active managed requests before preparing another model. Other processes using the same state directory also participate in reservations. |
| Legacy/unverified PID refused | Inspect the old MAX process and stop it deliberately; never remove state to bypass process-ownership checks. |
| Notebook cannot read the key | Set it in the kernel with the password-prompt cell above; a terminal export may not reach an already-running editor. |
| Notebook already exists | Keep hand edits; generate with a new `--output` filename. |
| Phone cannot connect or reports a TLS error | Use the appliance hostname, not phone localhost; verify LAN DNS, CA trust, firewall, and Caddy. mDNS alone does not establish trust. |

The `/v1/models` response uses the canonical HF ID as public `id`, with the
immutable `revision`; `model_id` is also included as canonical metadata. Use that
public ID for direct OpenAI chat requests. The backend-advertised MAX ID is
resolved privately and is not exposed to clients. `nucleus-local` and `local`
remain aliases for configured `NUCLEUS_MODEL_ID`; changing a pinned run's
revision requires a new deployment run rather than relabeling the existing
endpoint.

### Stop and inspect installed services

For the local foreground workflow, stop the API with Ctrl+C, then use
`pixi run mxs stop` after requests finish to stop CLI-managed MAX. Do not delete
state or cached weights as part of normal shutdown.

For the optional systemd deployment, inspect the units actually installed:

```bash
sudo systemctl status mxstudio-nucleus.service
sudo journalctl -u mxstudio-nucleus.service -n 80 --no-pager
sudo systemctl stop mxstudio-nucleus.service
```

`mxs endpoint stop RUN_ID` refuses to stop MAX while the managed Nucleus service
is still answering. Explicitly stop the systemd service first, then run the
endpoint stop command; it stops only the MAX process owned by that run.

Caddy's unit is `mxstudio-nucleus-caddy.service`; the optional gateway's unit is
`litellm-nucleus.service`. They can be inspected/stopped separately. Commands and
state for the service use the configured service user/cache, not the interactive
user's default directory. Review logs for private data before sharing them.

## Does this complete the original specification?

**Not yet as an end-to-end validated appliance.** The core implementation and
optional deployment files exist. Target-hardware operation and several intended
product experiences still need evidence or additional work.

| Original requirement | Shipped behavior / deliberate difference | Remaining acceptance |
| --- | --- | --- |
| Run Hugging Face models with MAX | Download/compatibility workflows and one managed MAX server; not arbitrary-model conversion | Compile and generate on each intended model/hardware combination. |
| Intercept OpenAI chat requests | Authenticated FastAPI streaming/non-streaming chat proxy | Complete a real request from the intended satellite app. |
| Swap models without breaking active inference | Shared request reservations and exclusive lifecycle changes; busy conflicts are rejected, not silently killed | Exercise two compatible models and overlapping clients; inspect GPU memory release. |
| Hide compilation with progress SSE | Bounded waiting and separate status/preparation jobs; **no fake compilation-stage events in the OpenAI token stream** | Decide whether an app needs a separate progress UI; integrate private management deliberately rather than exposing Studio administration. |
| HTTPS via Caddy | Run-specific server HTTPS proof plus distinct independent-client report; accepted provenance is a trusted-operator report, not remote attestation. | Install Caddy, configure resolution, trust its CA on another device, and perform live HTTPS streaming/cancellation acceptance. |
| Virtual keys | Optional LiteLLM/PostgreSQL profile; core has one shared key | Provision the database and test generation, use, and revocation of app keys. Native SQLite app-key management is not implemented. |
| Hybrid local/cloud routing | Separate opt-in cloud example; local defaults do not send prompts to cloud | Configure provider credentials and explicitly approve/test a paid cloud fallback. No automatic GPU-capacity routing is implemented. |
| Zero-config discovery | Optional mDNS service advertisement | Test discovery in the real LAN and receiving app. TLS trust, firewall rules, and app discovery support are not automatic. |
| One-click boot deployment | Guarded, default-dry-run installer and non-root systemd templates for Debian/Ubuntu and Arch/Omarchy x86_64 | Complete user/cache/secret prerequisites, install explicitly, reboot, and test inference. Not a zero-prerequisite appliance image. |
| Omarchy/Linux on Apple Silicon; large Apple GPU target | **Not supported by the supplied Linux platform lock/installer**; macOS preview is a different CPU path | Establish OS/MAX/driver support before claiming this target works. No Apple GPU-on-Linux proof exists here. |
| Touch/voice apps and professional deployment | An inference endpoint, not finished satellite apps or a validated professional product | Build app integration, speech components, domain evaluation, and privacy/oversight controls separately. |

### Your first acceptance pass

- [ ] The selected model downloads and preparation reports ready on this machine.
- [ ] An authenticated chat request produces real text; a request without a key returns 401.
- [ ] A second warm request succeeds; the notebook records actual timing and hardware context.
- [ ] A model switch during an active managed request is refused rather than interrupting it.
- [ ] After requests finish, another downloaded compatible model can be prepared and queried by its canonical public `/v1/models` ID and pinned revision.
- [ ] The controller and MAX stop/restart as intended without deleting state.
- [ ] If LAN access is needed: a second device resolves the hostname, trusts TLS, and receives tokens.
- [ ] If virtual keys are needed: a restricted app key works and stops working after revocation.
- [ ] If boot automation is needed: inference works after a reboot under the service user.

Leave optional checks unchecked or explicitly mark them not required; do not
turn mocked test results into hardware/deployment evidence. Broader Studio goals
such as arbitrary architecture porting, executable Mojo lessons, and numerical
parity are also not completed by the Nucleus layer.

## Verification and limitations

Hermetic tests cover model identity/revisions, deployment-run stages, probe evidence
validation, process ownership, reservations across processes, busy responses,
cancellation, OpenAI forwarding, CLI/API adapters, detached job wiring, discovery
cleanup, safe config validation, and notebook measurements. They mock MAX/model/
network operations. Deployment dry-runs do not install services.

Actual Hugging Face downloads, MAX compilation/generation, GPU memory release,
distro installation, LAN multicast, Caddy certificate trust and independent-client
generation, PostgreSQL-backed LiteLLM keys, and cloud fallback still require live
acceptance on the deployment target. Plan A live acceptance is not complete.
Milestone B (unsupported-model port automation/promotion) and C (gateway endpoint
inventory/routing) are deferred. No Apple GPU on Linux, M5 appliance,
numerical-parity, or regulated-workflow readiness is claimed.
