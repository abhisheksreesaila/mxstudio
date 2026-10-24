# Brief: omxconsole without blades, and a front door

Status: draft (2026-10-23)

## Why (the mission)
- Using the reskinned console daily, the blades got in the way: pick a model, open a section, the blade closes, go back, pick the next model, open the section again. Every model's screen is the same template, so stacking blades buys nothing. There are only a handful of screens, and blades pay off when there are many.
- People don't read long READMEs. The README should hand them the mission and a link, and the site should do the selling, the way omxcode's does.
- If this isn't done, the console keeps a navigation that its owner finds irritating, and newcomers meet a 139-line README.

## Who it's for
- Abhishek, daily, comparing and running a few local models (desktop window and phone).
- A developer landing on the GitHub repo or the site, deciding in 30 seconds whether to try it.

## What it does
- Core job: one page at a time, with the model as something you switch, not something you open.
- v1:
  1. **Plain pages, one nav, every destination once:** five places (Models, API keys, Gateway, Security, Settings) live in one nav: a left rail on desktop, a bottom tab bar on the phone (the same nav, two shapes). My models is home (list or cards, as today). Add a model stays as it is, as a page under Models with Back. The top bar only says where you are ("My models › model ▾") plus the Beacon. Theme moves into Settings.
  2. **One model page with a picker:** clicking a model opens one page. The model picker in the top bar swaps the model in place. The model's sections (Overview, Benchmark, Logs, Notebook) are tabs inside the page, and switching models **keeps you on the same tab** (A's Benchmark → B's Benchmark). The notebook is the same: pick a model, and its metadata loads. The page's buttons are only the model's verbs (Start/Stop, Restart, Copy endpoint, Remove); a section's own action (Run benchmark, Open in Jupyter) lives inside that section. Security is one place for all models, so it is not a model tab; Overview links to it. The URL holds the model and the section, so Back and links work.
  3. **Scale to 100 models, organised by labels:**
     - Labels, not folders: a model can be in several groups at once (`qwen2.5-coder` is both Coder and #work). Every model gets one automatic **kind** from its model card (Chat, Coder, Vision, Embeddings, Image); on top of that you add your own labels (#work, #experiments, anything), from the model's Overview.
     - My models gets a search box (`/` to focus) and a row of chips: All, Running, the kinds, then your labels, each with a count. Tap a chip to filter; running models sort first.
     - The picker is a searchable switcher, not a plain dropdown: type to narrow (`coder 7b`), arrow keys and Enter, `/` or Ctrl+K to open, the same chips, Recent (last 3) at the top, then all models grouped by kind. On the phone it is a bottom sheet with the same search and chips.
  4. **Cosmetic fixes:** every button is flat, with no elevation, and the overview's metric tiles use a regular 2×2 grid with one surface treatment.
  5. **Front door:**
     - The README is half a page: the one-line mission, a screenshot, "see it live", then links to the user guide, API reference, CLI reference and porting.
     - `site/index.html` becomes an omxcode-style story page: a hero, live demos, how a request travels, an FAQ, and "available today".
     - The clickable console tour stays as the user guide.
- Won't do: other new features (labels are the one addition), nested folders, label colours or label management screens, per-model custom screens (later, as an addition), renaming anything.

## How it should look and feel
- The same omxqua skin and Beacon as the omxconsole brief. Three words: calm, alive, precise.
- A model switch **morphs** the numbers in place (gentle, 220 ms). No page flash, and you keep your scroll position and section.
- The screen that matters most: the model page, with the picker open.
- Phone: the same pages one at a time, touch first (targets 40 px or more). The nav is a bottom tab bar, the model tabs a segmented control, the picker a bottom sheet.

## Success
- Comparing a section across two models takes two taps (open the picker, pick), down from about five today.
- No blade code is left (the blade engine and the stack are gone), and the docs tour shows the new pages.
- The README fits on one screen, and every reference page is one click from it.

## Decisions and trade-offs
- Plain pages everywhere, not only the model screen: one way to move around, at the cost of the biggest rewrite and retaking the docs tour.
- Switching keeps the section: easy comparison, at the cost of a new model sometimes landing on an empty state.
- An omxcode-style front page: the same front door as the other projects, at the cost of one more page to keep in step.

## Risks
- The rewrite touches routes, the top bar's breadcrumb and about 30 test files that assume blades. Do it as vertical slices, each one green: the model page first, then the global pages, then delete the engine.
- Riskiest assumption: one page with a picker covers everything blades did. The cheapest test is the mock, before any code is written.

## Open questions
- (assumed) The kind comes from the model card's task (Hugging Face `pipeline_tag`), and you can correct it. Your labels live in `~/.mxc/mst.db` and in the CLI: `mxc label qwen2.5-0.5b work`, `mxc ls --label work`.
- (assumed) The breadcrumb is "My models › qwen2.5-0.5b ▾"; the section shows only as the active tab (mock v2, after "sidebar and top bar conflict").
- (assumed) The Beacon's details and the Add-a-model flow keep their current behaviour, as pages.
