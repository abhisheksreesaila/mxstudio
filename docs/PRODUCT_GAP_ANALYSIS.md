# MX Studio Product Gap Analysis

This document compares the current MX Studio product with baseline local-AI workflows documented by Ollama and LM Studio. It is a prioritization aid, not a claim that every competitor feature belongs in MX Studio.

The comparison was reviewed against public Ollama and LM Studio documentation on 2026-08-05. Competitor behavior changes quickly; recheck the linked primary sources before using this document for compatibility claims.

## Product thesis

MX Studio should match the small, composable path that makes a local model useful to an application, then go substantially deeper on MAX inference engineering.

The winning sequence is:

1. discover a compatible model and understand why it should fit;
2. download, serve, and call it from a stable local API or CLI;
3. inspect measured runtime evidence;
4. open an editable lab or architecture workflow to understand and improve it.

Ollama is strongest at command-line distribution and integration. LM Studio is strongest at desktop model management and experimentation. MX Studio already has a credible differentiator neither product centers: evidence-oriented MAX model bring-up, editable learning artifacts, hardware inspection, and benchmark context in one workflow. Product work should protect that identity rather than add a second-rate copy of every consumer feature.

## Current position

MX Studio already ships:

- curated and cached Hugging Face model discovery, model-card context, license visibility, compatibility guidance, background downloads, and uninstall;
- one managed `max serve` process with health checks, persisted identity, logs, streaming chat, and a direct OpenAI-compatible chat endpoint;
- CLI, FastAPI, and browser entry points for the main supported workflow;
- bounded local tool use with a visible trace;
- MAX benchmark execution with an explicitly labeled stream-timing fallback;
- deterministic, cited, editable notebooks and Reveal presentations;
- a non-overwriting custom-architecture workflow with explicit validation gates;
- durable jobs, hardware observations, path containment, and a hermetic reliability suite.

These capabilities are more technically inspectable than a typical model launcher, but the common application path is less complete than the competitors' paths.

## Material gaps

| Capability | MX Studio today | Product consequence |
| --- | --- | --- |
| Remote model discovery | Exact Hugging Face IDs can be checked through the API and browser; the CLI and catalog remain registry-oriented. | New users must already know which checkpoint they want, and terminal parity is incomplete. |
| Stable application API | MAX exposes `/v1/chat/completions` on its managed port; MX Studio exposes a narrower browser SSE route. | Clients couple to the implementation server and lose a stable control-plane address across lifecycle changes. |
| API capability contract | There is no studio-level `/v1/models` or machine-readable declaration of chat, tools, vision, embeddings, or structured-output support. | Integrations must guess what a model and the current MAX version can do. |
| Reusable inference configuration | Browser inference controls are ephemeral. | System prompts and generation settings cannot be named, reviewed, or reproduced across CLI, API, and GUI runs. |
| Conversation persistence | Browser chat state is process-local JavaScript state. | Sessions cannot be resumed, exported, compared, or attached to benchmark and model evidence. |
| Embeddings and retrieval | No embedding operation or embedding-model lifecycle is shipped. | RAG and semantic-search applications have no honest foundation inside MX Studio. |
| Vision input | Compatibility hints include some multimodal architecture names, but chat accepts text only. | Vision-capable MAX models cannot be exercised through the product. |
| Structured output | No JSON Schema request, constrained generation, or validation path is exposed. | Application developers cannot rely on machine-readable responses. |
| Lifecycle policy | Start, stop, switch, and crash recovery exist; idle TTL and demand loading do not. | A local application must manage memory occupancy manually. |
| Integration guidance | Generic OpenAI clients work against MAX, but there is no tested integration matrix or focused client guide. | Compatibility exists without enough evidence for users to adopt it confidently. |
| Request observability | Process logs, jobs, hardware facts, and benchmark history exist; request history and token accounting do not. | Runtime diagnosis stops at process-level evidence. |

Several apparent gaps are deliberate scope choices. MX Studio supports one managed MAX server, not a pool of runtimes. It does not manage GGUF, MLX, llama.cpp, or vLLM, and it should not add them merely to match a catalog checkbox. Quantization, conversion, speculative decoding, embeddings, vision, and constrained generation should only be exposed when the installed MAX version and selected architecture can be checked and the behavior can be tested.

## Recommended sequence

### P0: Complete the developer loop

#### 1. Stable OpenAI-compatible studio gateway

Expose the supported subset of `/v1/models` and `/v1/chat/completions` from the MX Studio control-plane port. Proxy to the single managed MAX server, preserve streaming semantics and usage fields, and return clear errors when no model is healthy. Publish the exact compatibility subset in generated OpenAPI output and tests.

Observable outcome: an existing OpenAI client changes only `base_url` and keeps working while MX Studio owns server lifecycle and port details.

#### 2. CLI parity for arbitrary Hugging Face checkpoints

Add terminal operations to check an exact `org/model`, explain compatibility, download it through the existing durable job operation, and report why serving is blocked. Do not bypass the current advisory checks or imply that download proves MAX compatibility.

Observable outcome: a user can complete the supported model-to-endpoint path without opening the browser or editing the curated registry.

#### 3. Machine-readable capability evidence

Return capability state for the selected model and installed MAX environment: `verified`, `available-unverified`, `unsupported`, or `unknown`. Start with chat, streaming, and tool calls. Record the command, model revision, MAX version, device, and result for every live verification.

Observable outcome: the CLI, API, and GUI render the same evidence and never infer support from a model name alone.

### P1: Make experiments reproducible

#### 4. Named presets

Persist system prompt and supported inference parameters in a small versioned schema. Allow list, inspect, apply, import, and export from the CLI before adding a GUI editor. Keep unsupported parameters out of generated MAX requests.

Observable outcome: the same named preset produces the same request shape from the CLI, API, browser, and notebook.

#### 5. Durable conversation records

Store conversations locally with model identity, preset, timestamps, messages, tool traces, and optional request metrics. Add explicit export and deletion. Avoid sync, accounts, folders, and RAG in the first slice.

Observable outcome: a conversation survives a UI restart and can be reproduced or attached to a bug report without exposing secrets.

#### 6. Request-level measurements

Capture time to first token, generation time, token counts when available, stop reason, model revision, and active hardware context. Keep raw request content out of telemetry by default and make retention visible.

Observable outcome: a user can move from a slow response to a comparable benchmark or lab with the relevant runtime facts already attached.

### P2: Expand only through verified MAX capabilities

Add embeddings, structured output, vision input, and idle unload as independent vertical slices. Each slice requires:

- a terminal path and shared domain operation;
- a capability probe rather than architecture-name inference;
- a focused API contract and hermetic tests;
- one supported model exercised on target hardware;
- an editable lab that explains the operation and its limits.

Embeddings should precede document retrieval. A retrieval feature without measured embedding support, chunking controls, source citations, and evaluation would dilute the product's evidence standard.

## Explicit non-goals

Do not prioritize these until the recommended sequence is complete:

- managing llama.cpp, GGUF, MLX, or vLLM as alternative runtimes;
- concurrent model pools or an inference scheduler;
- a custom SDK before the stable HTTP contract is proven;
- a general autonomous tool marketplace or arbitrary command execution;
- cloud model hosting, account sync, or a remote model registry;
- consumer chat organization, localization, or theme breadth;
- claiming Apple GPU, numerical parity, regulated-workflow readiness, or broad compatibility without target-specific evidence.

## Success measures

Track outcomes rather than feature count:

- median time from a clean install to the first streamed token;
- percentage of supported workflows completed without the browser;
- percentage of served models with recorded load, chat, and benchmark evidence;
- success rate of OpenAI-client compatibility tests;
- number of reproducible experiments carrying model, MAX, device, and preset data;
- number of architecture ports advancing through strict load, compile, generation, and parity gates.

## Primary competitor references

- Ollama documentation index: <https://docs.ollama.com/llms.txt>
- Ollama OpenAI compatibility: <https://docs.ollama.com/api/openai-compatibility>
- Ollama tool calling: <https://docs.ollama.com/capabilities/tool-calling>
- Ollama structured outputs: <https://docs.ollama.com/capabilities/structured-outputs>
- LM Studio documentation index: <https://lmstudio.ai/llms.txt>
- LM Studio local server: <https://lmstudio.ai/docs/developer/core/server>
- LM Studio OpenAI compatibility: <https://lmstudio.ai/docs/developer/openai-compat>
- LM Studio model management: <https://lmstudio.ai/docs/developer/rest>
