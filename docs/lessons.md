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
- 2026-10-03 · Appended CSS blocks from parallel agents can leave a media query unclosed and silently swallow later blocks; a brace-balance test on console.css now guards it.
- 2026-10-03 · Don't drop a workaround from old code as 'environment-specific' without testing on the real hardware: MAX 26.5 device graph capture crashes on this native Linux box too (CUDA_ERROR_INVALID_DEVICE), not just WSL.
- 2026-10-03 · Concurrent starts read the same nvidia-smi snapshot: placement must count what this process already placed, not just live free memory.
- 2026-10-03 · The product is written as one lowercase word, never "MX Console"; the command stays mxc. (Since 2026-10-20 it is **omxconsole**, ADR 0004; the package, `~/.mxc` and wire names keep "mxconsole".)
- 2026-10-03 · ↗ only on links that leave the console and open a new tab; a link to our own page (the API reference) gets no arrow.
- 2026-10-03 · The menu must collapse to an icon rail (☰ in the top bar), with tooltips on hover.
- 2026-10-10 · Show design concepts and mockups as Claude Artifacts, not Lavish Editor: that's where the person reviews them.
- 2026-10-11 · A port must be built against the MAX that will serve it: the skill's env ran MAX 26.7 while mxconsole served 26.5, so OLMoE passed the build check and failed to load (max.tree, ModuleV3Outputs). Keep the two MAX versions aligned.
- 2026-10-13 · Ports: don't burn tokens chasing an architecture's edge cases. Popular architectures get ported (by Modular or the catalog); for a hard one, document the finding and move on, and help only if someone actually asks.
- 2026-10-13 · Sandboxed agents: the harness (outside the sandbox) must never read, kill by PID from, or write into a folder the agent can write. Give the agent read-only inputs and a fresh dir, and let the sandbox (bwrap's PID namespace) clean up.
- 2026-10-14 · Describe UI from a screenshot of the running app, not from what a mode is supposed to look like (Jupyter's /doc view still has menus and a file browser).
- 2026-10-14 · A cross-origin iframe on loopback must use the same host name as the page (localhost vs 127.0.0.1 are different sites; the browser holds back cookies). Check both.
- 2026-10-14 · In a classic script, never declare a global `const`/`let` named like a window property (`top`, `name`, `parent`): it throws and the whole script dies silently. Wrap page scripts or pick another name.
