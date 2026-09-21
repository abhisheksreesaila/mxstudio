# Endpoint-first: mission and implementation check

[README](../README.md) · [Usage](USER_GUIDE.md) · [Endpoint operations](MXSTUDIO_NUCLEUS.md)

## The product in one sentence

**Give MX Studio a model; get one working, authenticated HTTPS endpoint powered
by MAX, with the complete setup history available for debugging.**

The primary experience is a terminal command, not a studio dashboard or a model
router. After any necessary first-time permissions and trust setup, the command
should resolve the model, check the machine, download or adapt it, validate real
inference, configure Caddy, and print the usable URL and connection instructions.
"Fastest" describes the intended low-friction workflow, not a measured performance
claim. Running the HTTP process alone is not success.

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

Code-level check of the current working tree, 2026-09-20. No model download,
GPU execution, service installation, or LAN/TLS acceptance was performed for this check.

| Capability | Finding | Source |
| --- | --- | --- |
| One command from model reference to verified HTTPS URL | **Missing.** Download, serve, preparation, API startup, and deployment remain separate operations. | [CLI](../notebooks/mxstudio/cli.py), [installer](../deploy/nucleus/install.sh) |
| MAX support discovery and Hugging Face inspection | **Partial foundation.** Reads `max list --json` when available and inspects HF metadata; support/fallback heuristics are advisory, not inference proof. | [Compatibility](../notebooks/mxstudio/compat.py) |
| General model resolution from the terminal | **Incomplete.** `download` and `serve` use curated registry IDs or unique substrings; arbitrary HF/model-catalog discovery is not integrated into those commands. | [CLI resolver](../notebooks/mxstudio/cli.py), [registry](../notebooks/mxstudio/registry.py) |
| Managed MAX lifecycle and authenticated chat | **Implemented components.** One managed model, cross-process reservations, bounded preparation, OpenAI chat forwarding, and cancellation cleanup. Target-model success still requires real execution. | [Lifecycle](../notebooks/mxstudio/servers.py), [Nucleus](../notebooks/mxstudio/nucleus.py), [API](../notebooks/mxstudio/api.py) |
| Skill-driven unsupported-model workflow | **Guided only.** Approval, donor/scaffold work, and skill stages exist through API/jobs. `inspect` runs a skill script; the stage called `implement` runs a gates/preflight command, not an autonomous coding agent. No complete repair/retry loop or architecture CLI exists. | [Architectures](../notebooks/mxstudio/architectures.py), [job dispatch](../notebooks/mxstudio/jobs.py) |
| Evidence-gated promotion of a new port | **Not implemented as a trustworthy automated gate.** Gate records can be updated without validating the claimed execution artifacts; smoke checks do not establish serve readiness. No automatic validated promotion to HTTPS serving exists. | [Architecture gate records](../notebooks/mxstudio/architectures.py), [gate update API](../notebooks/mxstudio/api.py) |
| Caddy HTTPS deployment | **Configuration and installer exist, not seamless onboarding.** Requires prepared service user/cache/secrets, hostname resolution, and certificate trust. The install probe checks API responsiveness and service activity, not inference through HTTPS from another device. | [Caddy](../deploy/nucleus/Caddyfile), [installer](../deploy/nucleus/install.sh), [preflight](../deploy/nucleus/preflight.py) |
| Complete debug history for one deployment | **Partial.** MAX logs, SQLite jobs, worker logs, and bring-up artifacts exist. There is no single deployment run ID/history covering discovery through verified HTTPS; repeated skill stages overwrite their stage artifact. | [Server logs](../notebooks/mxstudio/servers.py), [worker](../notebooks/mxstudio/job_worker.py), [architecture artifacts](../notebooks/mxstudio/architectures.py) |

**Conclusion: the core mission is not finished.** Prior implementation delivered
useful components, not the integrated experience described above. Unit tests of
those components do not establish completion of this product contract.

## What “done” must mean to the user

- Enter one model reference; resolve ambiguity and missing authorization explicitly.
- See concise terminal stages and a clear failure/recovery action when something fails.
- Reuse cached weights and a suitable running model instead of repeating work unnecessarily.
- Require recorded model-load and real generation evidence; unsupported ports also need
  strict weight loading and reference-comparison evidence before promotion.
- Receive an authenticated HTTPS base URL, model ID, and safe credential instructions.
- Verify a real chat through that HTTPS URL, not only localhost health or model listing.
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

The next milestone is to connect the existing supported-model path to verified
Caddy publication as one logged, terminal-first workflow. The unsupported-model
adaptation loop is a separate substantial gap that must then join the same
success/failure contract—not be represented as already solved by skill preflight.
