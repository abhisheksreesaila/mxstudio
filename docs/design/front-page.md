# F-060 Front page: design handoff (draft)

Status: **pick pending.** Tick Stitch / Claude per option and device on the compare page; both ticked = merge.
Brief: docs/briefs/console-pages.md (v1 item 5, "Front door"). Ticket: docs/plan.md F-060.

- Compare page: `python3 -m http.server 8790 --bind 127.0.0.1 --directory docs`, then http://127.0.0.1:8790/design/front-page/compare.html
- Engine: Stitch + Claude side by side (per the taste file).
- Stitch project: `5461452906425196744` ("omxconsole site", set in `.stitch.json`); design system uploaded from `design/DESIGN.md` (asset `007df74018cc4586ba3073a51bd0cf13`).
  - A desktop `a345b5e607cd4733b101e2efd45d9fe6` · A phone `9596208b04774475aa7eb9165e74f9ae`
  - B desktop `62ddc3eeef9a4511b55ef684ab9542d9` · B phone `54d9410faf7e43fdbc3ee387dd88a2a0`
  - Two extra screens, `a951deae…` and `31190714…`, were asked for as phone but came back desktop (`--device MOBILE` was ignored; the JSON payload worked). They're left on the canvas as extra desktop variants.
  - Saved under `docs/design/front-page/stitch/` (`<option>-<device>.html|png`). Stitch's HTML uses Tailwind and a font CDN: use it as a reference only.
- Claude drafts: `docs/design/front-page/claude-a.html`, `claude-b.html`. Opened directly they show themselves framed at 1440 × 900 and 390 × 844; `?bare=1` is the page itself. Shared CSS and JS: `fp.css`, `fp.js`.
- Language: omxqua v0.1.5, Developer · Graphite (`oq-root oq-family-dev`, ink accent, compact, Geist + Geist Mono), the landing layer. Web and phone use the same language.

## Options

| | A · Statement | B · Bento |
|---|---|---|
| Theme | Night (`oq-night`) | Light (`oq-light`) |
| Hero | centred, huge two-tone type, a diagram (command → endpoint → models), then the console frame unfolding | split: headline left, a terminal that types the first run right, then the console frame with two floating pills |
| Sections | one idea per screen, single column, "usual way vs mxc" timelines, step strip, stat strips | each section a bento of tiles holding real parts (states, pin switch, keys, routes, screenshots) |
| Console | segmented tabs swap the real screenshot (Overview, Benchmark, Logs, Notebook) beside a read-along paragraph | a bento of five real screenshots, each with its `mxc` command |
| FAQ | 3 × 2 grid of cards, each answer a small visual | accordion, the first one open, a visual next to each answer |
| Phone | the same story stacked; FAQ cards swipe | bentos for Sharing and Console swipe, with the next tile peeking |

Both share the structure taken from omxcode: sticky nav (brand · section pill · one CTA), hero with an alpha badge and two CTAs, sections with a bold heading ending in a full stop and a status label ("works today", "interactive", "text LLMs only"), stat strips, "Go on, ask." FAQ, "Available today." with copyable commands and two CTAs, and a 4-column link footer.

What Stitch added and Claude didn't: a fake "Active Model Matrix" table in the hero (B), richer data rows. What Stitch made up and must not be built: RTX 4090, "218 tok/s", "38 ms TTFT", "v0.4.1", "zero-copy weight remapping", Llama 3 / Mistral / Qwen / Gemma as "ported", a "porting throughput" source. Take its layout, not its numbers.

## Content rules

- Numbers only from the repo docs: 8 architectures ported (GPT-2, Pythia, Phi-2, StarCoder2, StableLM 2, SmolLM3, Granite MoE, OLMoE; BLOOM and EXAONE parked), 7–40 minutes and $2–5 per port, 3 attempts, up to 4 tries each, keep_alive 5 minutes, ports :8443 / :4000 / :11435, 4 `/v1` routes on the LAN, qwen2.5-0.5b about 1 GB, 2× RTX 3090 and MAX 26.7. Anything else is labelled `illustrative` (the model-day timelines).
- Screenshots are the real ones in `docs/assets/screens/`, inside browser frames. No invented UI.
- The interactive "How a request travels" keeps today's behaviour (where from × cold/loaded, Play), now built from omxqua parts.
- The console tour (`docs/site/console.html`) stays as the user guide; the page links to it.

## Tokens and components

- Tokens: `--oq-surface-0..3`, `--oq-ink`, `--oq-ink-2`, `--oq-line`, `--oq-line-2`, `--oq-accent`, `--oq-on-accent`, `--oq-ok`, `--oq-warn`, `--oq-info`, `--oq-data-1..5`, `--oq-shadow-1..4`, `--oq-radius-card` (12), `--oq-radius-xs`, `--oq-spring`, `--oq-spring-fast`, `--oq-spring-slow`, `--oq-ease-in`, `--oq-beacon-accent` (the live dot).
- omxqua parts: `.btn.fill|tonal|text` (`.lg` for CTAs), `.badge.ok|warn`, `.chip`, `.seg2` (+ `Omxqua.placeThumb`), `.switch`, `.src` / `.src.note` with `.cp`, `.mark` (writes itself on press).
- Landing parts (in `fp.css`, `L-` prefix, to move into the site build): nav `.L-nav`/`.L-links`, `.L-hero-h`, `.L-h2` (two-tone with `em`), `.L-status`, `.L-stats`, `.L-frame` (browser frame), `.L-term`, `.L-chips`, FAQ visuals `.L-pills` / `.L-vs` / `.L-rows` / `.L-lineage`, `.L-get`, `.L-foot`, `.L-swipe`; the request demo (`[data-rt]`, `.rt-*`).
- Build note: `docs/site/omxqua/` vendors only `tokens.css`; the build should vendor `components.css` and `omxqua.js` there too (the drafts carry a copy in `docs/design/front-page/omxqua/`).

## Motion (budget applied)

- On scroll only, never idle: hero words rise once (70 ms stagger, `--oq-spring`), sections rise 28 px as they enter, the console frame unfolds (scale .82 → 1, corners 40 → 14) with the scroll, numbers count up once (900 ms), one read-along paragraph (A) lights word by word. B's terminal types once on load (its one hero moment).
- Springs only under the finger: press squash (`omxqua.js`), segmented thumbs, the switch, the FAQ "+" turning.
- The request demo's hops step every 850 ms; log lines fade in 4 px (Gentle). Console tab swap: 180 ms fade.
- `prefers-reduced-motion`: everything static and visible, the demo plays instantly, the terminal prints at once.

## Gestures

| Gesture | Does | Visible alternative | Threshold |
|---|---|---|---|
| Tap | every link, button, segmented option, Play, Copy, FAQ row | itself | 44 px targets on phone (CTAs 48) |
| Swipe sideways (phone) | move through parallel cards (FAQ in A; Sharing and Console tiles in B) | the next card peeks 16 % at the edge; the row also scrolls with a trackpad | scroll-snap, one card per flick |
| Swipe sideways on the nav chip row (phone) | reach the later section links | chips are cut at the edge to show there's more | native scroll |
| Scroll | drives the reveals, unfold and read-along | none needed; content is there under reduced motion | — |

## States to build

- Copy: "Copied" for 1.6 s; no clipboard permission → "Select and copy".
- Request demo: idle shows "press Play"; changing an option resets the log; Play again restarts it.
- Images: lazy below the fold, fixed 16:10 box so nothing jumps; alt text on each.
- Long content: hero and section titles wrap with `text-wrap: balance`; terminal lines scroll sideways inside their block, never the page.
- No JS: all content is in the HTML (only the request demo needs JS; say so in a `<noscript>` line).
- Offline: not applicable (static page).

## Phone vs desktop

- Nav: section links become a scrolling chip row under the brand; the CTA stays top right. No hamburger.
- Grids stack to one column; stat strips go 2 × 2; diagrams stack vertically (A's hero diagram, the request hops).
- Parallel cards swipe in one row with the next peeking (taste: "the story, not the detail").
- Hero type: 52–72 px on phone; CTAs full width, 48 px.
- Segmented controls in the demo go full width, Play is a full-width 48 px button.
