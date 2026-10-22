# Brief: omxconsole as a desktop app (Linux, then Mac)

Status: approved (in chat, 2026-10-21: "yes please")

## Why (the mission)
- Running omxconsole today means a terminal: `pixi run mxc daemon`, keep it open, `mxc console`. It should be an app you open, with the server looked after for you.
- Now: 1.0 and the omxqua reskin are done, and `mxc app` already opens the console in a native window (pywebview + Qt). The 1.0 brief set the installer aside for v2; this is that step, scoped to the owner first.
- If it's never built, every use starts with a terminal, and the Mac (where MAX also runs) is never tried.

## Who it's for
- Abhishek first: Omarchy Linux daily, then an Apple-silicon Mac. Not strangers yet (see Decisions).
- Trigger: wanting a model up without thinking about a server; glancing at what's running from the bar.

## What it does
- Core job: open an app, and omxconsole is running.
- v1, in this order:
  1. **Tray app on Linux.** A tray icon in Waybar (Qt `QSystemTrayIcon`, StatusNotifierItem): Open console, a line for what's running (the Beacon's text), Start/Stop server, Start at login, Quit. Opening it starts the daemon if it isn't already running; the console opens in the existing native window. *(assumed: Quit stops the daemon only if the app started it)*
  2. **Start at login** without a terminal: a systemd user unit on Linux, a LaunchAgent on Mac, switched on and off from the tray and by `mxc app --autostart on|off`. *(assumed)*
  3. **Mac support and a Mac app.** `omxconsole.app` from a zip: on first launch it shows a setup screen, installs pixi and the environment into `~/.mxc/runtime` (a few GB, a few minutes), then behaves as on Linux, with a menu-bar icon. MAX runs on Apple silicon; GPU memory comes from the Mac's unified memory instead of `nvidia-smi`.
- `mxc` keeps working in a terminal on both. It talks to whichever daemon is running, so the app and the command are interchangeable. The app puts `mxc` on PATH. *(assumed)*
- Out of scope: Apple signing and notarization, Windows, bundling MAX inside the download, auto-update (the tray shows "update available" at most), Intel Macs. **Porting on Mac** (its sandbox is bubblewrap, Linux-only): the Port button explains it's Linux-only for now. *(assumed)*

## How it should look and feel
- Three words: quiet, ready, native.
- Feels like Ollama's and Docker Desktop's menu-bar icons: nothing to manage, one click to the console. The console itself is unchanged (omxqua, Developer family).
- Tray icon: the omxconsole mark, one look when idle and one when a model is running. *(assumed)*
- The screen that matters most: Mac first-run setup. One progress screen with honest steps and time left, which ends with the console open on My models.

## How we'll know it worked
- Linux: after a reboot, the tray icon is there, `mxc serve qwen2.5-0.5b` works with no daemon started by hand, and the console opens from the tray.
- Mac: from a downloaded zip on a clean user account, open it (right-click → Open once), finish setup, and `qwen2.5-0.5b` answers a chat request; the console shows it running.
- All existing tests stay green; new tests use the fake `max` as today.

## Constraints and risks
- Riskiest assumption: MAX serves our small models on Apple silicon well enough (GPU, or CPU at usable speed). Cheapest test: on the Mac, `pixi install` and `max serve` qwen2.5-0.5b by hand. This happens **before** any Mac app work. Also assumed: you have an Apple-silicon Mac to test on.
- `nvidia-smi` placement, Caddy for `mxc expose` and Jupyter all need checking on Mac.
- Unsigned app: macOS shows "can't be verified" once; the README says to right-click → Open.

## Decisions and trade-offs
- First user is the owner, not strangers: smaller and faster; the cost is that signing, polish for unsupported hardware and a stranger-proof first run come later.
- Small app that sets up on first launch, not everything bundled: a tiny download and easy updates; the cost is a one-time few-GB wait that needs internet.
- Unsigned Mac app for now: free, no Apple account; the cost is a one-time Gatekeeper warning that would stop most strangers.
- Linux tray first, then Mac: each step is usable alone; the Mac app reuses the tray and autostart code.
- Stay in Python + Qt (the existing `app` environment), not Tauri or Electron *(assumed)*: no new language, and the window already exists; the cost is that the Mac app is a thin launcher around pixi rather than a compiled binary.

## Open questions
- None blocking. Deferred: signing ($99/yr Apple Developer), public download page, Windows.
