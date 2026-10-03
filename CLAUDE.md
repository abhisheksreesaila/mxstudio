# This project

<!-- Project-specific facts only. The shared workflow and stack live in ~/.claude/CLAUDE.md. -->

- What it is, in one line: MX Console, a local AI runtime on Modular MAX. The `mxc` command serves small models behind one secure OpenAI-compatible endpoint and teaches you about them.
- Run it: `pixi run mxc serve qwen2.5-0.5b` (the daemon is `pixi run mxc daemon`; LAN HTTPS is `pixi run mxc expose`)
- Test it: `pixi run pytest tests/mxconsole -q` (no GPU needed; tests use a fake `max`). Live check: `pixi run python scripts/mxc_acceptance.py`
- Differences from the usual stack (if any): a local appliance, not SaaS. FastHTML gateway without fh-saas auth/tenancy (one owner, per-app API keys); one SQLite file at `~/.mxc/mst.db`. LiteLLM runs in its own pixi env (`-e gateway`). New code is in `mxconsole/`; `notebooks/mxstudio` is the legacy core, kept until mxc replaces it. See docs/adr/0001.
- Design system: `design-system/DESIGN-SYSTEM.md` ("Graphite", chosen 2026-10-02): Geist + Geist Mono, Lucide icons, contrast theme by default
