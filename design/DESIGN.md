# omxconsole design system: omxqua · Developer · Graphite

omxqua = Material texture (opaque tonal surfaces, state layers, one soft shadow scale) with Liquid motion (springs under the finger, calm settles for panels). Developer family "Graphite": neutral, compact, mono-forward, numbers and source lines everywhere. One accent: ink (near-white in dark mode, near-black in light mode). Calm, alive, precise.

Product: omxconsole, a local AI runtime on Modular MAX. Marketing pages turn the app's language up: huge type, one idea per screen, real product screenshots in browser frames.

## Colour, dark mode (default for marketing)
- surface-0 (page): #0b0b0d
- surface-1 (cards): #151518
- surface-2 (tonal buttons, menus, nav pill): #1d1d21
- surface-3 (dialogs): #25252a
- ink (text): #ededef · ink-2 (secondary text, heading continuations): #a9a9b1 · ink-faint (never text, decoration only): #3a3a40
- line (hairlines): #2a2a30 · line-2 (inner hairlines): #202025
- accent: ink #ededef with on-accent #0b0b0d (the one filled button per card, focus ring)
- states, always with a word: ok #3ccf6e, warn #f5a524, bad #f2555a, info #52a9ff
- data colours (kinds, diagram nodes, chart series, never the accent): #8b7bff violet, #3aa0ff blue, #2bc3ad teal, #ff8a3d orange, #f2555a red, #e0b400 yellow
- live dot / Beacon accent: #3ccf6e

## Colour, light mode
- surface-0 #eeeef1 · surface-1 #ffffff · surface-2 #ffffff · surface-3 #ffffff
- ink #0b0b0d · ink-2 #5b5b63 · line #e3e3e8 · line-2 #efeff2
- accent ink #0b0b0d, on-accent #ffffff
- states: ok #137036, warn #8f5400, bad #c01f25, info #0960b5
- data: #6d5cf0, #0a7fe0, #0f9483, #e0661a, #d42a2f, #a88400

## Typography
- Sans: Geist. Mono: Geist Mono (every number, port, path, command, model id, and every card's source line). No other fonts.
- App text 13.5 px; labels 12 px; card titles 15 px semibold.
- Marketing hero: Geist 800, 64 to 168 px, line-height 0.95, tracking -5%. Section titles 40 to 88 px, Geist 750, tracking -4.5%, ending in a full stop.
- Two-tone headings: the claim in ink, its continuation in ink-2 ("One endpoint. *Every model you've pulled.*").
- Body 17 to 19 px, ledes 22 to 26 px in ink-2. Sentence case everywhere; tiny mono overlines only.

## Shape
- Pill (fully rounded) = tappable: buttons, chips, segmented controls, nav links.
- Status = a square-cornered (4 px) mono badge with a dot and a word: "works today", "coming soon", "ready", "cold".
- Cards 12 px radius (compact). Inputs 10 px. Menus 14 px, items 8 px. Browser frames around screenshots 14 px.
- Concentric corners: inner radius = outer minus padding.

## Depth
- Depth is tone plus one shadow scale: page surface-0; cards surface-1 + shadow-1 (a 1 px inner top highlight + a tight 2 px drop); menus surface-2 + shadow-2; the product frame shadow-4 (a long soft drop).
- No glass, no blur, no translucency on content. No gradients on chrome or text. No glow.
- Hover / press / selected = a state layer of ink at 8 / 12 / 16 % over the surface, never a new colour.

## Components
- Buttons: filled (accent + shadow-1, lifts 1 px on hover), tonal (surface-2 + shadow-1), text (no fill, ends in ›). One filled button per card or section, with a specific verb ("Serve qwen2.5-0.5b", "Get it on GitHub").
- Cards end in a mono source line: "$ mxc keys new my-app", with a small copy pill on the right.
- Chips: no outline; off is a light ink tint, on is a 16 % accent tint with 600 weight.
- Segmented control: pill track on surface-0 with a sliding surface-2 thumb.
- Tables: hairline rows, mono right-aligned tabular numbers.
- Stat strip: 4 columns (2 on phone) of a big mono number + a small label + a source note.
- Diagrams: boxes are surface-2 cards with a coloured 7 px square kind marker and a mono port label (":8443", ":4000", ":11435"); arrows are 1.5 px ink-faint hairlines.
- Icons: Lucide-style line icons, 1.7 stroke, round caps. Never emoji.
- Terminal block: surface-0 inset on a card, mono 13 px, "$" prompts in ink-2, a copy pill top-right.

## Shell (marketing)
- Sticky top bar: brand (a small "omx" mark + "omxconsole") on the left, a pill of section links in the centre, one filled CTA on the right. Solid background.
- Content in a centred column, max 1120 px; 140 px between sections on desktop, 88 px on phone.
- Phone: no hamburger for the main nav; section links become a horizontally scrolling chip row under the brand, the CTA stays visible; touch targets at least 44 px.

## Motion (described for the build; Stitch screens are static)
- Springs only for small things under the finger (press squash 0.96, segmented thumbs). Panels never bounce.
- Scroll motion on the landing page only: hero words rise once, sections rise 28 px as they enter, the product frame unfolds (scale 0.82 to 1), numbers count up once. Reduced motion: everything static.
