# MX Console design system: Graphite

Chosen 2026-10-02 after four rounds (canvas: https://claude.ai/artifact/21NSyXKpzcrqanmqr8bEe5, pages
"Graphite · base" and "Graphite · personality"). The console is an instrument for watching models run:
calm graphite chrome, white cards, and colour that means something (a section's hue, a model's trace, a state).

## Principles
1. **Structure from Azure, identity our own.** Settings in the left menu, summary in the top half, tiles
   below; nothing copied from Microsoft (no Segoe, no Fluent assets).
2. **Colour carries meaning.** Every section has a hue (menu icon, tiles). Every running model has a signal
   colour (its trace, dot, bars). Everything else is greyscale.
3. **One signature moment per page.** Live tiles, the pull cloud, the patch bay, the drag race, the
   safeguards. Never more than one per page; the rest stays quiet.
4. **Every view is a command.** Each page and action shows its CLI twin (`mxc …`); ⌘K runs the same.
5. **Motion explains state,** never decorates; all of it stops under `prefers-reduced-motion`.

## Type
- UI: **Geist** 400/500/600/700. Numbers, ids, paths, commands: **Geist Mono** 400/500. Both SIL OFL;
  ship the woff2 files with MX Console (no font CDN at runtime).
- Scale: page title 30/600 (tracking -0.035em) · section 15/600 · body 13.5–14 · labels 12.5 · mono
  meta 11.5–12.5 · big numbers 26–34/600 tabular (tracking -0.03 to -0.04em) · menu group labels mono 10.5
  uppercase, tracking 0.08em.

## Themes (CSS custom properties; the default is **contrast**)
| Token | contrast (default) | light | dark |
|---|---|---|---|
| `--page` (main background) | `#2e2e33` | `#fafafa` | `#09090b` |
| `--chrome` (top bar, menu, status bar) | `#232327` | `#ffffff` | `#111113` |
| `--chrome-line` | `#3a3a40` | `#e8e8ea` | `#26262a` |
| `--chrome-text` / `--chrome-muted` | `#ededef` / `#a1a1aa` | `#0a0a0b` / `#71717a` | `#ededef` / `#8b8b93` |
| `--page-text` / `--page-muted` (on `--page`) | `#f4f4f5` / `#b4b4bb` | `#0a0a0b` / `#71717a` | `#ededef` / `#8b8b93` |
| `--card` | `#ffffff` | `#ffffff` | `#111113` |
| `--card-line` / `--card-line-2` | `#e8e8ea` / `#f1f1f3` | same | `#26262a` / `#1c1c20` |
| `--text` / `--text-2` / `--muted` / `--faint` (in cards) | `#0a0a0b` / `#3f3f46` / `#71717a` / `#a1a1aa` | same | `#ededef` / `#c4c4c8` / `#8b8b93` / `#5f5f66` |
| `--sunk` (selected, wells) | `#f4f4f5` | `#f4f4f5` | `#18181b` |
| `--primary` / `--on-primary` | `#0a0a0b` / `#ffffff` | same | `#ededef` / `#09090b` |
| `--ok` / `--warn` / `--bad` | `#16a34a` / `#d97706` / `#dc2626` | same | `#22c55e` / `#f59e0b` / `#ef4444` |
| `--code` / `--code-text` | `#0d0d10` / `#e4e4e7` | same | `#000000` / `#e4e4e7` |

### Code and accents
The same in every theme. The console takes them from these custom properties, never as bare hex.

| Token | Value | Used for |
|---|---|---|
| `--cp-str` / `--cp-num` / `--cp-kw` | `#ce9178` / `#b5cea8` / `#569cd6` | code panel (API page): strings, numbers, keywords |
| `--cp-com` / `--cp-key` / `--cp-fn` / `--cp-txt` | `#6a9955` / `#9cdcfe` / `#dcdcaa` / `#d4d4d4` | code panel: comments, keys, functions, plain text |
| `--accent` | `#0090ff` | focus ring, active tab underline, info tint |
| `--accent-text` | `#0070d6` (dark theme `#52a9ff`) | text on the accent tint (info pill, POST badge) |
| `--magenta` | `#c239b3` | the key name in the `Authorization` line |

The code panel background is `--code`. `--tk-*` colours are the light-on-card variants used by inline code blocks.

**Tokyo Night (Omarchy) theme, optional:** on a machine running Omarchy, MX Console can read
`~/.local/state/omarchy/current/theme/colors.toml` and map it (background → page, dark_background → chrome,
lighter_background → card, accent → primary, its colours → hues and signals), with JetBrains Mono as the
UI font. Shown as G5 on the canvas.

## Colour that means something
- **Section hues** (menu icon on a tint, its tiles): Overview `#0090ff` · Activity `#7c66ff` · Models
  `#8e4ec6` · Pull `#12a594` · Ports `#ad7f58` · API keys `#e09d00` · Network `#00a2c7` · API reference
  `#3e63dd` · Metrics `#e93d82` · Benchmarks `#e5484d` · Logs `#697177` · Gateway `#f76b15` · Daemon `#30a46c`.
  Tint = hue at 9% alpha (`17`), 15% (`26`) when active.
- **Model signals,** in load order: `#7c66ff` `#0090ff` `#12a594` `#f76b15` `#e5484d`. A model keeps its
  colour everywhere (live trace, table dot, race car, chart line).
- **States:** running = signal dot with a soft halo · starting = amber ring + "compiling · 41 s" · idle =
  hollow grey ring · crashed = red dot + "see log". Never colour alone: always the word too.

## Shape and depth
- Radius: cards 12 · sheets and the ⌘K palette 14 · buttons and inputs 7 · chips 6–8 · pills 999.
- Lines: 1px `--card-line`; tables use `--card-line-2` between rows.
- Elevation only for floating things: side sheet and palette `0 24px 64px rgba(10,10,11,.16)` plus a 1px
  ring. Cards are flat on the page.

## Icons
Lucide (ISC), 16px in menus and buttons (15 in chips), stroke 1.6 (1.8 on colour). Menu icons sit on a
24px tinted square of the section hue.

## Components
- **Top bar** (52): mark, product, `/ mxc@host` with a live dot, centred ⌘K search, theme switch, Docs, avatar.
- **Menu** (232): mono group labels, items 32 high, hue chip + label; active = `--sunk`-equivalent on chrome
  + weight 500. A bottom card: "Every view is a command" with three example commands.
- **Status bar** (30, mono 11.5): daemon uptime · local endpoint · LAN endpoint · MAX version · GPU memory ·
  req/min · ⌘K.
- **Page header:** title 30, mono subtitle, secondary buttons then one black primary.
- **Live strip:** one lane per running model: dot, name, dotted-grid trace with a breathing "now" dot,
  tok/s + ttft + memory.
- **Number strip:** one card, 4 cells split by hairlines.
- **Tiles** (live tiles): hue chip + title + arrow, a big number, mono footnote; some animate (cycling
  models, scrolling activity ticker, travelling packets, filling progress). Any tile can later be pinned
  to a personal dashboard.
- **Side sheet** (540): name in mono, repo@rev, actions, underline tabs, trace card, settings rows with a
  36×20 switch, a dark "same from the terminal" box, Apply/Discard.
- **⌘K palette** (660): input, grouped rows with icon + label + the CLI equivalent right-aligned, ↵ on the
  selected row, footer: ↑↓ move · ↵ run · ⌘C copy as CLI.
- **Signature pages:** Pull (cloud rains shards into a churning cartridge + real steps) · Network (patch
  bay: devices → Caddy → LiteLLM → daemon → a jack per model port, flowing cables) · Benchmarks (drag race
  with an optional synthesized "vroom" on click, speedometer, results table) · Gateway (six safeguards as
  solid friendly illustrations with counters + "the crew" of background processes with heartbeats) ·
  Metrics (bold colour blocks, "what you should care about", later the Ask MX Console chat).

## Motion
Page change 180 ms fade + 6 px rise · sheet 240 ms spring · palette 120 ms scale from 98% · traces scroll
each second, "now" dot breathes 2 s · numbers tick · live tiles cycle every 3–4 s · all off under
`prefers-reduced-motion`. Sound only ever on an explicit click (the race).
