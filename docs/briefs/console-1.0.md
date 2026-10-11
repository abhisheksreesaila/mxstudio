# Brief: mxconsole 1.0, the blades console and the desktop app

Status: approved (in chat, 2026-10-11: "we will stick to the current plan… build the azure like interface we planned… we also need a mac and linux app… go proceed")

## Why
- The current console looks like a generic cloud console; people will reject it. The engine (pull, serve, secure, port, learn, bench, keys, gateway) is built. This is a UI change plus one new behaviour (automatic porting).
- Concept D is the chosen design: https://claude.ai/artifact/CSZMDSYDhyCzja8pjHpotw (file `d-console.html` in that artifact; local copy `.lavish/concepts/d-console.html`).

## Who it's for
- Abhishek first, daily, on Omarchy Linux; then anyone on Linux or a Mac who wants a model from the MAX catalog or Hugging Face on localhost in as few steps as possible.

## What it does
1. **Blades layout (Azure-style).** A dark top bar (brand, breadcrumb, hardware chip, API keys, Gateway, Settings, theme toggle) and docked blades that open side by side and fold into rails on the left. No left sidebar, no global Logs.
2. **My models** (home blade): list or cards toggle, live tok/s, state, access (local or secure), crashed models called out with a fix. It collapses to a compact list when a model is open beside it.
3. **A model's resource blade**: menu (Overview, Security, Benchmark, Logs, Notebook), command bar (Start/Stop/Restart, Make it secure, Run benchmark, Open notebook, Logs, Copy endpoint, Remove), Overview with essentials, live tokens, "call it" (endpoint, key, curl) and hardware fit.
4. **Add a model**: one search over the MAX catalog and Hugging Face, each result marked fits / tight / too big / needs porting against this machine's GPU. Picking one runs the setup stages (check hardware, fetch, compile, serve, smoke test) with a live log, ends on a Ready card, and the model appears in My models.
5. **Unsupported architecture = automatic port.** When the architecture isn't supported, the console offers "Port it": it runs Modular's published skills (`import-model`) unattended through Claude Code, shows each stage and the detailed log, retries what fails (auto-fix), and on success registers and serves the model like any other. At minimum it shows what it did.
6. **Make it secure**: one click gives HTTPS on the LAN with an API key (existing `mxc expose` + keys).
7. **Notebook** (Learn): opens wide (the blades to its left fold into rails): chapters, the generated notebook's cells, the architecture; "Open in Jupyter" for running it for real.
8. **Benchmark**: standard charts (bars for decode tok/s and TTFT across your models), no car race.
9. **Logs**: per model only.
10. **API keys** (new, rotate, revoke) and **Gateway** (LiteLLM routes) as shared blades.
11. **Hardware awareness**: the top-bar chip shows GPU and VRAM in use by each model; Add a model warns before you pick something that won't fit and suggests what will.
12. **Colour-coded outline icons; light and dark themes** (dark follows the system until toggled).
13. **Desktop app for Linux and Mac**: `mxc app` opens the console in its own native window (starting the daemon if needed), with a launcher entry on Linux. Packaging for others (AppImage, .app/.dmg) follows once it runs.

## Out of scope for 1.0
- The Omarchy bar plugin (later, as a thin layer over `mxc`).
- Big models beyond what the GPU fits; multi-machine; a public installer.

## How we'll know it worked
- From an empty machine state, Add a model → Ready in the console with no terminal, and the card shows live tok/s.
- Make it secure works from the model's blade; the curl it shows succeeds.
- An unsupported model goes through Port it and either serves, or stops with a clear log of what it tried.
- Every action in the console has its `mxc` command shown beside it (lesson 2026-10-02: CLI first).
- `mxc app` opens the console in a native window on this Linux machine.

## Constraints and risks
- CSP stays `default-src 'self'`: no inline scripts or style attributes; data goes in as text nodes.
- The automatic port is the riskiest part: an agent run can take a long time or fail. It runs as a background job, can be cancelled, and the log is kept.
- Mac can't be tested on this machine; the app code must not assume Linux, and the Mac build is verified later.
