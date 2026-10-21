# Journal (autopilot)

What was tried, what happened, dead ends. Read before each new attempt.

## 2026-10-21 · omxconsole finish (docs/goals/omxconsole-finish.md)
- M1: three leftovers test-first (test_console_finish.py); F-025's command test updated for D-04 and a new test keeps Logs in the menu. In the browser the idle model's bar is one row at 1280 px.
- M2: pulled HuggingFaceTB/SmolLM2-360M-Instruct through Add a model in the real console. Download 6 s, compile ~60 s, smoke test replied. Saw: setup stages, live log, Ready card (screenshots add-stages.png, add-ready.png). Found: the Beacon said idle during compile (no "starting" state); the Ready card's endpoint and curl overflow the card; the parent crumb truncates beside a long name. The Beacon's pull state wasn't caught: the download finished between two 3 s polls.
- M2: caught the Beacon's pull state with a 2.9 GB pull (beacon-pull.png): ring, percent, steps with real sizes, Open pull and Cancel. The download then finished in seconds, before the two-tap Cancel could be tried live; it stays unit-tested. Found: a long model name in the compact Beacon pushes the tools island over the breadcrumb.
- M2 fixes, seen live: the Beacon's new starting state ('Starting qwen2.5-coder-1.5b-instruct', beacon-starting.png); the tight top bar keeps the breadcrumb clear (478–802 px vs tools from 818); the Ready card's curl now scrolls inside the card; the parent crumb stays whole.
