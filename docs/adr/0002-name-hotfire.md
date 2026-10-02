# 0002 · The product is named Hotfire

Context: "MX Studio" is taken (Moxa's MXstudio industrial network suite; Macromedia Studio MX before it),
reads as a Modular product next to MAX/Mojo, and "mst" carries no story. The person wanted a drastic,
memorable name in the spirit of Ollama, tied to Modular's fire and rocket mascots.

Decision: **Hotfire**. A hot fire is a rocket engine lit on the test stand to measure its thrust: the
product lights up a model and measures it. The web console is **Hotfire Mission Control**.
- Command: `hotfire` (`hotfire serve qwen2.5-0.5b`). `mst` stays as an alias for one release.
- Python package and PyPI name: `hotfire` (free on PyPI and npm on 2026-10-02; no Arch/AUR package; no
  software trademark found; GitHub has only unrelated hobby repos). Not a legal clearance.
- State folder `~/.hotfire` (an existing `~/.mst` is moved on first run); env vars `HOTFIRE_*`, with
  `MST_*` still read as a fallback.

Consequences: a mechanical rename across code, tests and docs. Vocabulary follows the story where it
helps (benchmark = static fire, live data = telemetry) but commands stay plain (`serve`, `pull`, `ls`).
Don't use Modular's artwork or the 🔥 file extension as our mark.
