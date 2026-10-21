# Brief: omxconsole, the console in omxqua

Status: approved (in chat, 2026-10-20: "yes approved"; a skin plus the Beacon, phone dock deferred)

## Why (the mission)
- mxconsole 1.0 works, but it still looks like a generic cloud console (the 1.0 brief's own worry). omxqua is now the design language for every app; the console is its flagship, the first serious tool built in it.
- Now: the engine and the blades are done (F-023 to F-031), so this is a skin over working features, not new behaviour.
- If it's never done, the console stays the odd one out among the apps, and omxqua has no real product proving it.

## Who it's for
- Abhishek, daily, on Omarchy Linux (desktop window via `mxc app`), and on his phone over the LAN to glance at what's running.
- Trigger: serving, checking or securing a local model; watching a pull or a port.

## What it does
- Core job: the same console, re-dressed and renamed.
- v1:
  1. **Name on screen**: everything people see says omxconsole (window and page titles, wordmark, docs, README, CLI messages). The `mxc` command, the `mxconsole` Python package and `~/.mxc` stay.
  2. **omxqua skin, blades kept**: concept D's structure stays. The dark top bar becomes omxqua top islands: the writing logo and "omxconsole" on the left, the breadcrumb in the middle, API keys, Gateway, Settings and the theme toggle on the right, then the **Beacon**. Blades take omxqua surfaces, buttons, fields, badges, `.xbtn` closes and the one universal press.
  3. **Beacon**: shows what's happening now, in this order: a pull or port in progress (with %), a crashed model, running models with live tok/s; when idle, GPU memory in use. Tap it and it opens into the details. It replaces today's hardware chip.
- Phone (glance and act): what's running, start/stop, copy the endpoint, watch a pull. One blade at a time, as today, restyled. Notebook and Add a model stay desktop-first.
- Deferred: a bottom dock on phones (new navigation, not a skin); revisit if it's missed.
- Out of scope: new features, changes to the blade structure, renaming the command, the package or the state folder, the Omarchy bar plugin.

## How it should look and feel
- Three words: calm, alive, precise.
- omxqua Developer family (Graphite): ink accent, compact (34 px targets), mono numbers, a source line (the `mxc` twin) under each card. Night and Light; follows the system until toggled (as today).
- Motion: blades **settle** (fade + 12 px, no bounce) as they open, and older ones fold to rails; menus and toasts are gentle (220 ms); springs only for presses and the Beacon.
- The screen that matters most: **My models** with a model's blade open beside it.
- Colour-coded outline icons stay (the 2026-10-02 lesson: icons, colour and summary tiles, not hairlines alone), using omxqua's data colours.

## How we'll know it worked
- Side by side with `docs/showcase.html?fam=dev`, the console reads as the same family; you'd show it off.
- Every 1.0 check still passes (Add a model → Ready, Make it secure, Port it, `mxc app`), and the full test suite stays green.
- At 390 px: no sideways overflow; start/stop and copy the endpoint are one tap away.

## Constraints and risks
- CSP stays `default-src 'self'`: omxqua's files are vendored (no CDN), and `omxqua.js` must not inject a `<style>` tag (move that CSS into `components.css`, an omxqua fix).
- Riskiest assumption: omxqua's parts cover the blades without many console-only components. Cheapest test: the mock of My models + a model blade, before any tickets.
- Blade files were split so parallel agents don't collide (F-023); the reskin keeps that split.

## Decisions and trade-offs
- Name only, not a full rename: nothing breaks and `mxc` stays; con: the package and folder keep the old name.
- Keep the blades and reskin: keeps the structure you called "perfect"; con: blades aren't one of omxqua's shells, so the console adds one pattern (documented back into omxqua if it works).
- The Beacon shows whatever's happening now: always the most useful thing; con: no fixed place to glance at VRAM (it shows when idle, and the hardware fit stays in each model's Overview).
- Phone is glance and act: covers the real use; con: Notebook and Add a model stay awkward on a phone.
- A skin, not a redesign: only CSS, names and the Beacon change; con: the phone dock waits.
- Settle motion for blades, not the refresh flicker: calm while reading; con: less "console" feel.
- The `mxconsole` lesson (2026-10-03, one lowercase word) carries over: **omxconsole**, one lowercase word. (assumed)
- The Graphite design system (`design-system/DESIGN-SYSTEM.md`) is replaced by omxqua's Developer family and kept only as history. (assumed)

## Open questions
- None.
