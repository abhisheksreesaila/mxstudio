# 0005: The console is plain pages, not blades

Status: accepted (2026-10-24, brief docs/briefs/console-pages.md, mock v4)

## Context
Concept D (2026-10-11) gave the console Azure-style blades: each choice opened a panel beside the last. In daily use the owner found it irritating: every model shares one screen template, so comparing models meant closing and reopening blades. The console has about ten screens, too few for stacking to pay off.

## Decision
One page at a time, and every destination appears once. Five places (Models, Keys, Gateway, Security, Settings) live in one nav: a rail on desktop, a bottom tab bar on the phone. My models is home. A model is one page with tabs (Overview, Benchmark, Logs, Notebook) and a switcher in the top bar that swaps the model in place and keeps the tab. Models are grouped by an automatic kind and your own labels, not folders. The blade engine (`blades.js`, `blade-stack.js`, `blade-layout.js`) is removed once nothing uses it.

## Consequences
- Simpler routing: the URL is `#/models/<name>/<section>` or `#/<page>`, and Back works like a browser.
- The omxconsole brief's "blades kept" no longer holds, but its skin and Beacon do.
- About 30 blade tests are rewritten against pages, and the docs tour is retaken.
- Per-model custom screens can come later as sections on the same page.
