# Goal: finish omxconsole (autopilot, 2026-10-21 overnight)

**End goal:** "continue with autopilot": close F-045 and the small leftovers from the reskin, so omxconsole is finished and reviewed when the owner checks in the morning.

## Milestones (each proven by `scripts/check_omxconsole_finish.sh`)
1. **M1 Leftovers:** the model blade's command bar fits on one line at 1280 px; the Tokens card never says "No traffic yet" next to a non-zero request count; an empty Benchmark shows one Run button, not two.
2. **M2 Live states seen:** a real pull through Add a model, watched in the running console: the Beacon's pull state, Add's setup stages and its Ready card; screenshots saved under `docs/goals/omxconsole-finish/`.
3. **M3 Docs tour:** `docs/assets/screens/*.png` retaken from the reskinned console (1280×800), and every hotspot box and description in `docs/site/console.html` matches the new screens (no "hardware chip", no "dark top bar").
4. **M4 Reviewed:** the reviewer passes F-045 and this batch; `docs/plan.md` marks F-045 done; full suite green.

## Done means
The checker prints PASS for M1 to M4, and `docs/goals/omxconsole-finish-report.md` lists what changed, the screenshots, and the decisions worth a second look.

## Out of bounds
No pushing (the omxqua v0.1.5 push still waits for the owner), no deploys, no paid services, no secrets, no new Claude Code port runs (they cost real tokens), no removing the owner's models or keys.

## Assumptions
- The pull uses a small Hugging Face model (SmolLM2-360M-Instruct, about 0.7 GB) so the Ready card appears in minutes; it stays registered afterwards (`mxc rm` undoes it).
- The port state and Security's mid-setup state stay covered by unit tests: a port needs a paid agent run, and the machine is already secured.
- Budget: 60 turns.
