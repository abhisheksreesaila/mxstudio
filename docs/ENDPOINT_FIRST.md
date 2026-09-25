# Endpoint-first: mission and implementation check

[README](../README.md) · [Usage](USER_GUIDE.md) · [Endpoint operations](MXSTUDIO_NUCLEUS.md)

## The product in one sentence

**Give MX Studio a model; get one working, authenticated HTTPS endpoint powered
by MAX, with the complete setup history available for debugging.**

The primary experience is a terminal command, not a studio dashboard or a model
router. The current CLI/API now connect model resolution, revision-pinned
download, local generation proof, a setup handoff, and separate server/client
HTTPS evidence. The command does **not** install services or change host
configuration. "Fastest" describes the intended low-friction workflow, not a
measured performance claim. A running HTTP process alone is not success.

## Three entry paths

1. **A model listed as supported by MAX.** Resolve the listing to an exact
   checkpoint and revision, verify local compatibility, download if needed,
   validate inference, and publish the endpoint. A MAX listing can refer to
   weights on Hugging Face; these are not necessarily separate model stores.
2. **A Hugging Face checkpoint with a supported MAX architecture.** Inspect the
   config, weights, license/access requirements, and hardware fit, then use the
   existing MAX implementation. Do not add a fictitious format-conversion step
   when MAX can already load the checkpoint.
3. **A Hugging Face checkpoint without a supported MAX architecture.** Enter a
   skill-driven adaptation loop: inspect → select implementation/donor → adapt
   graph and weights → strict load → compile → generate → compare with a reference
   → diagnose and repair → repeat within explicit limits. Publish only after
   validated success. If blocked, retain the evidence and report the blocker;
   never label generated scaffolding a working model.

The **third path integrated into the same experience** is the intended
differentiator. It cannot promise that every architecture is automatically portable
or fits the machine. Existing hand-written ports must be preserved.

## What is actually implemented?

Code-level check of the current working tree, 2026-09-25. Hermetic tests exercise
adapters and state handling; no live model download, MAX generation, service
installation, distro acceptance, or second-client LAN/TLS verification was
performed.

| Capability | Finding | Source |
| --- | --- | --- |
| One command from model reference to verified HTTPS URL | **Orchestrated, not live-accepted.** `mxs up` creates a durable run and queues work; first-time publication pauses at `needs_setup`. Actual distro install and second-client proof remain explicit steps. | [CLI](../notebooks/mxstudio/cli.py), [runs](../notebooks/mxstudio/deployments.py), [installer](../deploy/nucleus/install.sh) |
| MAX support discovery and Hugging Face inspection | **Advisory discovery plus evidence gates.** Catalog/compatibility metadata helps resolve candidates; local generation, HTTPS generation, and client proof are distinct evidence. | [Inventory](../notebooks/mxstudio/inventory.py), [compatibility](../notebooks/mxstudio/compat.py), [runs](../notebooks/mxstudio/deployments.py) |
| General model resolution from the terminal | **Implemented for durable runs.** `catalog`, `pull`, and `up` accept supported catalog/model references and persist immutable revisions; legacy `download`/`serve` commands remain. | [CLI](../notebooks/mxstudio/cli.py), [inventory](../notebooks/mxstudio/inventory.py) |
| Managed MAX lifecycle and authenticated chat | **Implemented components; target proof remains live.** One managed MAX process, revision/load-path identity, reservations, authenticated chat, and streaming generation evidence are recorded. | [Lifecycle](../notebooks/mxstudio/servers.py), [Nucleus](../notebooks/mxstudio/nucleus.py), [API](../notebooks/mxstudio/api.py) |
| Skill-driven unsupported-model workflow | **Guided only.** Approval, donor/scaffold work, and skill stages exist through API/jobs. `inspect` runs a skill script; the stage called `implement` runs a gates/preflight command, not an autonomous coding agent. No complete repair/retry loop or architecture CLI exists. | [Architectures](../notebooks/mxstudio/architectures.py), [job dispatch](../notebooks/mxstudio/jobs.py) |
| Evidence-gated promotion of a new port | **Not implemented as a trustworthy automated gate.** Gate records can be updated without validating the claimed execution artifacts; smoke checks do not establish serve readiness. No automatic validated promotion to HTTPS serving exists. | [Architecture gate records](../notebooks/mxstudio/architectures.py), [gate update API](../notebooks/mxstudio/api.py) |
| Caddy HTTPS deployment | **Explicit per-run setup handoff and verification stages.** `needs_setup` includes a unique protected EnvironmentFile destination and generated dry-run, guarded copy, and `--apply` commands; none execute automatically. Verification first checks the active systemd service's single protected environment and its user/group, state/cache paths, model/revision, and hostname against the run. DNS and CA trust remain operator actions. | [Caddy](../deploy/nucleus/Caddyfile), [installer](../deploy/nucleus/install.sh), [preflight](../deploy/nucleus/preflight.py) |
| Complete debug history for one deployment | **Durable run timeline implemented.** A run ID connects model/revision, stages, jobs, endpoint evidence, and recovery; worker failure is surfaced for safe resume. | [Deployments](../notebooks/mxstudio/deployments.py), [jobs](../notebooks/mxstudio/jobs.py) |
| Independent client completion proof | **Trusted-operator report, not remote attestation.** A streamed HTTPS probe records a distinct machine fingerprint and fresh timestamps; the operator imports the JSON proof to transition to `ready`. Evidence expires after 24 hours. | [CLI](../notebooks/mxstudio/cli.py), [API](../notebooks/mxstudio/api.py), [deployments](../notebooks/mxstudio/deployments.py) |

**Conclusion: the terminal/API integration is implemented, but the appliance
acceptance is not complete.** Hermetic tests do not establish successful MAX
execution, distro installation, LAN reachability, TLS trust, or independent
client generation on real hardware.

The explicit catalog reference syntax is `catalog:<exact MAX architecture>`,
for example `catalog:Qwen2`; matching is case-insensitive but exact. A unique HF
example resolves to its real repository with `source="catalog"`. Zero examples
is an actionable `missing` result; multiple examples return `ambiguous`
candidates and require an exact HF ID, never auto-select; unavailable installed
MAX catalog data is an actionable `blocked` result rather than a guessed fallback.
Before `up` downloads, known HF dtype/quantization metadata is compared with
normalized MAX encodings; unsupported/unknown architectures or clear encoding
mismatches block serving before fetch. `pull` still permits unsupported
architecture/encoding weights for later port work, retaining `supported=false`;
missing safetensors artifacts alone block the pull. Compatibility checks are
guidance, not runtime proof; pull errors and blockers are structured results.

## What “done” must mean to the user

- Enter one model reference; resolve ambiguity and missing authorization explicitly.
- See concise terminal stages and a clear failure/recovery action when something fails.
- Reuse cached weights and a suitable running model instead of repeating work unnecessarily.
- Require recorded model-load and real generation evidence; unsupported ports also need
  strict weight loading and reference-comparison evidence before promotion.
- Receive an authenticated HTTPS base URL and model identity only after the
  corresponding stages provide evidence.
- Verify a real chat through HTTPS from both the server and an independent client;
  server-side evidence alone must remain `awaiting_client_verification`.
- Preserve a per-run timeline: resolved source/revision, compatibility decisions,
  commands, versions/hardware, durations, exit status, logs, and validation artifacts.
  Redact credentials; do not record user prompts by default.
- Inspect, retry/resume safely, and stop the service without losing diagnostic history
  or interrupting another active managed request.

A LAN domain is a real usable URL; it need not expose the appliance to the public
internet. Caddy does not make a `.local` certificate trusted on every phone.
First-time DNS/mDNS and client CA enrollment must be explicit, or an operator can
choose an appropriate publicly trusted DNS/certificate setup. Do not report
universal client readiness before those checks succeed.

## Keep optional features outside the critical path

- **LiteLLM:** optional advanced routing, provider fallback, and per-app key policy.
  The core needs none of it. Multiple simultaneous local models would require
  additional backends; adding a router does not change the one-managed-MAX invariant.
  PostgreSQL is currently required only by the supplied optional virtual-key profile.
- **UI:** retain the existing Studio as an optional interface for learning,
  inspection, notebooks, and future experiences. The core workflow must not require it.
  These are optional surfaces today, not a completed general plugin framework.
- **Local persistence:** keep SQLite jobs and the existing FastAPI controller;
  changing the web framework or database is not the missing product feature.

The next work is live A4/A5 acceptance: approved Linux installation, Caddy
hostname/CA setup, and an independent client request. Unsupported-model
adaptation (B) and LiteLLM endpoint routing (C) remain deferred; guided
preflight/scaffolding and optional gateway files are not those milestones.

## Shipped run states and evidence

- `downloaded`: a revision-pinned checkpoint was downloaded/validated; it is not
  proof that MAX can load or generate with it.
- `local_ready`: the managed MAX process passed a local authenticated streamed
  generation check for the pinned revision. No LAN claim follows.
- `needs_setup`: service installation, hostname resolution, or CA setup needs an
  operator. This is a handoff, not success; `mxs up` does not invoke sudo or apply
  host changes.
- `awaiting_client_verification`: server-side HTTPS authenticated generation and
  private-route checks passed. Independent client trust/generation is still
  missing, so this is not `ready`.
- `ready`: server proof plus an imported, fresh independent-client probe report
  were accepted. Provenance is `trusted_operator_report`, not cryptographic
  remote attestation. Fingerprints must differ; server/client evidence is limited
  to 24 hours, client time cannot precede server time and may be at most 300
  seconds in the future, and the probe is bounded to 300 seconds.
- `blocked`, `failed`, `interrupted`, and `stopped` retain their distinct
  recovery/ownership meaning; resume does not claim a completed stage without
  evidence.
