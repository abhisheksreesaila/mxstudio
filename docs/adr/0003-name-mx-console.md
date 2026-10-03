# 0003 · The product is named MX Console (supersedes 0002)

Context: after naming it Hotfire (ADR 0002) the person settled on "MX Console": it is a console, not a
studio (cloud consoles use the word; Microsoft is the one that says "studio"), and "MX" ties it to the
MAX/Mojo stack it runs on. Checked 2026-10-03: Logitech's "MX Creative Console" (a hardware control
console in the Logitech MX series) and Moxa's MX* industrial network software (MXstudio, MXview,
MXconfig) exist; neither is called MX Console, and the person judged them different enough. `mc` is
avoided (Midnight Commander, MinIO client); `mxc` is free here, on Arch and in the AUR.

Decision: **MX Console**, command **`mxc`** (`mxc serve qwen2.5-0.5b`).
- Python package `mxconsole` (free on PyPI), import name `mxconsole`; commands `mxc`, plus `hotfire` and
  `mst` as aliases for one release.
- State folder `~/.mxc`; an existing `~/.hotfire` (or else `~/.mst`) is moved there once, with the same
  safety rules as before (no move while an old daemon holds its lock, carry on after a lost race, keep
  the old folder on a cross-device failure). Env vars `MXC_*`, then `HOTFIRE_*`, then `MST_*`.
- The web UI is "MX Console" (no separate "Mission Control" name). Wire identifiers stay as they are
  (`x-mst-admin`, `mst-` key prefix, `mst.db`) for compatibility.
- The Graphite design system and the rocket/fire signature moments stay; they never depended on the name.

Consequences: a second mechanical rename. Not a legal clearance; if Logitech or Moxa objects, revisit.
