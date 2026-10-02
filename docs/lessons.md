# Lessons

<!-- One line per lesson, added when you correct Claude or a mistake gets caught:
- YYYY-MM-DD · the rule that prevents it, and why
Claude reads this at the start of each session. -->
- 2026-10-01 · Developer-agent worktrees start from an old commit: tell each one to `git merge main` first and name the interfaces it builds on, or it builds on missing code.
- 2026-10-01 · Caddy's `path` matcher sees the cleaned path but `reverse_proxy` forwards the raw one: any path allowlist in front of a proxy must also reject unclean paths (`..`, `//`, `%`).
