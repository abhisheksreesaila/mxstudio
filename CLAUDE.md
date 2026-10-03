# This project

<!-- Project-specific facts only. The shared workflow and stack live in ~/.claude/CLAUDE.md. -->

- What it is, in one line: Hotfire, a local AI runtime on Modular MAX. The `hotfire` command serves small models behind one secure OpenAI-compatible endpoint and teaches you about them.
- Run it: `pixi run hotfire serve qwen2.5-0.5b` (the daemon is `pixi run hotfire daemon`; LAN HTTPS is `pixi run hotfire expose`)
- Test it: `pixi run pytest tests/hotfire -q` (no GPU needed; tests use a fake `max`). Live check: `pixi run python scripts/hotfire_acceptance.py`
- Differences from the usual stack (if any): a local appliance, not SaaS. FastHTML gateway without fh-saas auth/tenancy (one owner, per-app API keys); one SQLite file at `~/.hotfire/mst.db`. LiteLLM runs in its own pixi env (`-e gateway`). New code is in `hotfire/`; `notebooks/mxstudio` is the legacy core, kept until hotfire replaces it. See docs/adr/0001.
- Design system: `design-system/DESIGN-SYSTEM.md` ("Graphite", chosen 2026-10-02): Geist + Geist Mono, Lucide icons, contrast theme by default
