# Goal: the desktop app, overnight (autopilot, 2026-10-21 → 22)

**End goal:** "go on autopilot": carry the approved desktop-app brief (docs/briefs/desktop-app.md) as far as it can go without a Mac or the owner: the Mac-specific code and the Mac app, built and proven on Linux, so that the owner's one Mac run (F-049) either confirms it or shows exactly what's left.

## Milestones (each proven by `scripts/check_desktop_app.sh`)
1. **M1 Mac memory (F-050):** on macOS, GPU memory for placement, the Beacon and `mxc status` comes from the Mac's unified memory (sysctl/vm_stat), not `nvidia-smi`. Unit-tested with recorded Mac output.
2. **M2 Porting is Linux-only (F-050):** on a non-Linux machine `mxc port`, `POST /admin/ports` and the console's Port button say porting needs Linux, before anything is downloaded or started. Tested.
3. **M3 First run from nothing (F-051):** one launcher script takes a machine with no pixi and no checkout to a running omxconsole: installs pixi, fetches the source, `pixi install`, starts the tray and turns on start-at-login, while a setup page in the browser shows honest steps and ends on the console. Proven live on Linux in a scratch HOME on scratch ports; screenshots of the page mid-setup and done in `docs/goals/desktop-app-overnight/`.
4. **M4 The Mac bundle (F-051):** `scripts/build_mac_app.py` makes `dist/omxconsole-mac.zip` holding `omxconsole.app` (Info.plist as a menu-bar app, the launcher, an `.icns` icon). Tested for structure; the launcher is the M3 script.
5. **M5 Reviewed:** reviewer passes the batch; full suite green; `docs/plan.md` updated; `docs/goals/desktop-app-overnight-report.md` written.

## Done means
The checker prints PASS for M1 to M5. What can't be proven without a Mac (MAX on Apple silicon, the bundle opening in Finder, Gatekeeper) is listed in the report as the owner's F-049 run.

## Out of bounds
No pushing, no deploys, no reboot, no paid services, no secrets. The owner's real `~/.mxc`, daemon on 11435, tray service and `~/.local/bin/mxc` are not touched: live checks use a scratch HOME, `MXC_HOME` and ports, and stop only PIDs they started.

## Assumptions
- Doing F-050/F-051 before F-049's Mac run (D-14).
- The setup page is a draft in the console's omxqua look, for the owner's taste in the morning.
- Budget: 80 turns.
