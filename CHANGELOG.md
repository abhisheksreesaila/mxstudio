# Changelog

## 0.1.5 — 2026-08-04

- **Never freeze the UI:** long work queues to SQLite `~/.mxstudio/jobs.db` and fh-saas `/api/jobs`
- Background kinds: `hf_check`, `download`, `arch_analyze` / `scaffold` / `smoke`, `bench`, `model_card`
- Activity bar reads processing jobs from SQL; frontend polls `waitForJob` instead of blocking sync HF calls

## 0.1.4 — 2026-08-04 (unreleased locally)

- Centered tab canvases; notebooks expand side-by-side when reveal.js slides open (animated)
- Benchmark dashboard: colorful KPI cards + ECharts (run tok/s, TTFT, history, engine scoreboard placeholder for vLLM/llama.cpp)
- Notebook **Slides** button loads a model deck beside the lab

## 0.1.3 — 2026-08-04 (unreleased locally)

- **Architectures** tab: 7-step bring-up wizard (identify → inspect → gaps → scaffold job → implement → smoke job → hand-off)
- Honest limits banner: what mxstudio can vs cannot auto-complete for custom MAX arches
- Background architecture jobs surface in the activity bar
- Learn tab links into Architectures (wizard removed from Learn body)
- Floating chat close fix (`.is-open` class + cache bust)

## 0.1.1 — 2026-08-04

Presentation-ready polish for filming:

- **Models:** step-by-step HF import; MAX architecture + HF token + disk + VRAM fit gates; "use at your own risk" force path
- **Settings:** save HF token to `~/.mxstudio/secrets.json` (mode 600) from the UI
- **Chat:** floating FAB (removed from sidebar) + browser voice input (SpeechRecognition)
- **Notebooks:** dedicated Sources & citations cell (Hugging Face primary, papers secondary)
- **Benchmark:** readiness/prerequisites panel; hardware warnings; still prefers Modular `max benchmark`
- **Learn:** custom architecture wizard — gap checklist and non-overwriting scaffolds under `ports/`; OLMoE remains parked until its MAX graph and weight adapter are implemented

## 0.1.0 — 2026-08-04

Initial mxstudio release:

- Theme and Learn tab
- Hugging Face download check
- Modular MAX benchmark flow
- Uninstall support
- Sidebar collapse
- fh-saas jobs bridge
