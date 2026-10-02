# This project

<!-- Project-specific facts only. The shared workflow and stack live in ~/.claude/CLAUDE.md. -->

- What it is, in one line: MX Studio, a local AI runtime on Modular MAX. The `mst` command serves small models behind one secure OpenAI-compatible endpoint and teaches you about them.
- Run it: `pixi run mst serve qwen2.5-0.5b` (the daemon is `pixi run mst daemon`; LAN HTTPS is `pixi run mst expose`)
- Test it: `pixi run pytest tests/mst -q` (no GPU needed; tests use a fake `max`). Live check: `pixi run python scripts/mst_acceptance.py`
- Differences from the usual stack (if any): a local appliance, not SaaS. FastHTML gateway without fh-saas auth/tenancy (one owner, per-app API keys); one SQLite file at `~/.mst/mst.db`. LiteLLM runs in its own pixi env (`-e gateway`). New code is in `mst/`; `notebooks/mxstudio` is the legacy core, kept until mst replaces it. See docs/adr/0001.
- Design system: `design-system/DESIGN-SYSTEM.md` ("Azure Blade", chosen in F-010), not Terminal Ledger
