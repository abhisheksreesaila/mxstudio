# Lessons

<!-- One line per lesson, added when you correct Claude or a mistake gets caught:
- YYYY-MM-DD · the rule that prevents it, and why
Claude reads this at the start of each session. -->
- 2026-10-01 · Developer-agent worktrees start from an old commit: tell each one to `git merge main` first and name the interfaces it builds on, or it builds on missing code.
- 2026-10-01 · Caddy's `path` matcher sees the cleaned path but `reverse_proxy` forwards the raw one: any path allowlist in front of a proxy must also reject unclean paths (`..`, `//`, `%`).
- 2026-10-02 · A console "flat" design still needs icons, colour and summary tiles: hairlines and text alone read as bland. Azure's pattern is coloured service icons + summary first, details below.
- 2026-10-02 · The command line is the primary interface: every console setting needs a CLI equivalent (with --json for agents), and the web UI is for verifying. Build the CLI side first.
- 2026-10-02 · "Professional / Microsoft polish" comes from restraint (4px corners, dividers over boxes, few pills, exact type sizes, real Segoe-class font), not added decoration. When the person can't name what's off, show the same UI in 2-3 finishes side by side instead of iterating blind.
- 2026-10-02 · A finished CSS animation with `animation-fill-mode: both` that ends on a transform still makes its element the containing block for position:fixed children (it pushed the side sheet mid-page): use `backwards`.
