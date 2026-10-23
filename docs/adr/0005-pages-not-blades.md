# 0005: The console is plain pages, not blades

Status: proposed (2026-10-23, brief docs/briefs/console-pages.md)

## Context
Concept D (2026-10-11) gave the console Azure-style blades: each choice opened a panel beside the last. In daily use the owner found it irritating: every model shares one screen template, so comparing models meant closing and reopening blades. The console has about ten screens, too few for stacking to pay off.

## Decision
One page at a time. My models is home. A model is one page with a model picker at the top that swaps the model in place and keeps the current section. API keys, Gateway, Settings and Security are plain pages with Back. The blade engine (`blades.js`, `blade-stack.js`, `blade-layout.js`) is removed once nothing uses it.

## Consequences
- Simpler routing: the URL is `#/models/<name>/<section>` or `#/<page>`, and Back works like a browser.
- The omxconsole brief's "blades kept" no longer holds, but its skin and Beacon do.
- About 30 blade tests are rewritten against pages, and the docs tour is retaken.
- Per-model custom screens can come later as sections on the same page.
