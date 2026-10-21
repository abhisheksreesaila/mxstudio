# Report: finish omxconsole (overnight, 2026-10-21)

**Goal:** close F-045 and the reskin's leftovers so omxconsole is finished and reviewed. **Result: done.** All four milestones pass and the reviewer passed the batch and F-045.

```
$ scripts/check_omxconsole_finish.sh
PASS M1 leftovers
PASS M2 live states
PASS M3 docs tour
PASS M4 reviewed
```
Full suite: 795 passed. Nothing pushed.

## 30-second demo
```
pixi run mxc console        # starts the daemon if needed and opens omxconsole
```
Then open a model (one-line command bar, honest Tokens card), pull a small model from Add a model and watch the Beacon go "Pulling… → Starting… → 1 running".

Screens from the live run: `docs/goals/omxconsole-finish/` (add-stages, add-ready, beacon-pull, beacon-starting). The docs tour (`docs/site/console.html`) and README now show the new look.

## What changed
- Command bar fits one line (Logs lives in the model menu); the Tokens card says one true thing per state; an empty Benchmark has one Run button.
- Found in the live pull and fixed: the Beacon now has a **starting** state (it said "idle" through a minute of compile); the top bar folds tools to icons when a wide Beacon would squeeze the breadcrumb; the parent crumb never shrinks; the Ready card's curl stays inside its card.
- Docs: all 13 screenshots retaken and every hotspot re-measured; user guide and docs header say omxconsole.

## Worth a second look (from docs/decisions.md)
- **D-04:** Logs left the command bar (it's still one click away in the model menu).
- **D-10:** two models stay registered from the test pulls: `smollm2-360m-instruct` and `qwen2.5-coder-1.5b-instruct`. `mxc rm <name>` removes each (weights stay in the HF cache).
- **D-13:** `security.png` in the public docs shows your home path and host name (not secrets; blur it if you'd rather).

Still only unit-tested: Security mid-setup, a real port run, the Beacon's two-tap Cancel on a live pull.

## Next
Push omxqua v0.1.5 (waiting on your yes), then decide whether to push mxstudio's main.
