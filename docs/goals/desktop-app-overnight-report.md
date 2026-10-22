# Report: the desktop app, overnight (2026-10-21 → 22)

Goal: take the approved desktop-app brief as far as it goes without a Mac. Result: M1 to M5 pass
(`scripts/check_desktop_app.sh`). Everything Mac-specific is built and tested on Linux; your one Mac run
(F-049) proves the rest.

## What's new
- **Mac memory (F-050):** on a Mac, "GPU memory" is the unified memory (`sysctl` + `vm_stat`), so placement, the
  Beacon and `mxc metrics` work without `nvidia-smi`. MAX picks the device itself there (D-15).
- **Porting is Linux-only, said up front (F-050):** `mxc port --auto`, the API and the Port button refuse on a Mac
  before anything runs, and point to the guided `mxc port <ref>` (D-16).
- **First run from nothing (F-051):** `mxconsole/setup/install.sh` installs pixi, shows a setup page in the browser,
  downloads omxconsole, installs MAX/Python and Qt, turns on start at login, puts `mxc` on PATH, starts the tray and
  sends the page to the console. Later launches start the tray and open the window. **Live on Linux from a bare
  home: 22-33 seconds to a running console** (fast line; 4.9 GB downloaded).
- **omxconsole.app (F-051):** `pixi run python scripts/build_mac_app.py` → `dist/omxconsole-mac.zip` (72 KB): a
  menu-bar app whose launcher is that same script, with an icon on the macOS grid.
- **The Mac check got a step 3:** it builds the app from your checkout and runs it from nothing in a scratch home,
  so one run answers "does MAX work", "does mxc serve work" and "does the app work".

## Look at
- The setup page (a draft in the console's omxqua look, not run through the designer):
  `docs/goals/desktop-app-overnight/setup-progress.png`, `setup-done.png` (and `-dark`).
- Try the first run yourself (scratch home, nothing of yours touched):
  `docs/goals/desktop-app-overnight/live_run.sh`, then open http://127.0.0.1:18462/ while it runs.

## Decisions worth a second look
- **D-14:** built the Mac work before the Mac check, against the brief's order. Cost if MAX fails on a Mac: this
  night's work.
- **D-19:** the app is a shell launcher plus a browser setup page, not a compiled app: nothing to sign, builds
  anywhere; no native setup window.
- **D-20:** the app sets up from the `main` branch tarball. For strangers, build it from a tagged release.

## Needs you
1. **Push** (`main` is well ahead of GitHub). The Mac check clones from GitHub, so it needs the push first.
2. **On your Mac:** `git clone … && cd mxstudio && sh scripts/mac_check.sh`, then send back `mac-check.log`.
3. **Reboot once** (F-048's last check: the tray comes back on its own).
4. Your eye on the setup page; and whether to publish a download (a GitHub release).
5. `~/mxc-setup-scratch` (9.4 GB) is the live run's scratch home: delete it when you've looked.

## Seen, not fixed
- pywebview injects an inline style into the console window, which the console's CSP refuses (a console warning;
  the window works).
- Two setup launches within milliseconds can both take the lock (D-21).

What's next: your Mac run decides whether F-050/F-051 close or need fixes.
