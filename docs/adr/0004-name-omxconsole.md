# 0004 · The product is named omxconsole (supersedes 0003 for the display name)

Context: on 2026-10-20 the person created omxqua (omx + aqua), the design language for all their apps, and renamed the console to match: **omxconsole**, the flagship app in omxqua. Like mxconsole before it, it is one lowercase word.

Decision: a name-only rename.
- Everything people see says omxconsole: window and page titles, the wordmark, docs, README, CLI messages and `/v1/models`' owner string.
- The command stays **`mxc`**, the Python package stays `mxconsole`, the state folder stays `~/.mxc`, env vars stay `MXC_*` (then `HOTFIRE_*`, `MST_*`). Wire identifiers stay too (`x-mst-admin`, the `mst-` key prefix, `mst.db`).
- The look moves from the Graphite design system to omxqua's Developer family (brief: docs/briefs/omxconsole.md).

Consequences: the code and the folder keep the old name, so "mxconsole" still shows up in paths, imports and `pixi` tasks; only user-facing strings change. Not a legal clearance.
