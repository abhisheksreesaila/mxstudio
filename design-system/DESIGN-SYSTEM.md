# Hotfire design system: "Azure Blade"

Chosen in F-010 (2026-10-02). Flat, hairline-ruled, Azure-portal navigation. Canvas:
https://claude.ai/artifact/21NSyXKpzcrqanmqr8bEe5 (option C).

## Tokens

| Token | Value | Use |
|---|---|---|
| `--bg` | `#faf9f8` | page ground |
| `--surface` | `#ffffff` | nav, tables, blades |
| `--line` | `#e1dfdd` | borders, rules |
| `--line-soft` | `#edebe9` | row dividers |
| `--text` | `#242424` | body |
| `--text-2` | `#424242` | headings in tables, secondary text |
| `--muted` | `#605e5c` | help text, breadcrumbs (≥4.5:1 on white) |
| `--accent` | `#0f6cbd` | top bar, links, active nav, primary button |
| `--accent-soft` | `#ebf3fc` | selected row, active nav background |
| `--chip` | `#e8f1fa` on `--accent-ink` `#0c3b5e` | filter chips |
| `--ok` | `#107c10` | state Ready |
| `--warn` | `#c19c00` | state Starting |
| `--idle` | `#8a8886` | state Cold |
| `--bad` | `#c50f1f` | state Crashed, destructive actions (`#a4262c` for text) |
| `--input-border` | `#8a8886` | inputs and selects |
| `--hover` | `#f5f5f5` | row and button hover |
| `--disabled` / `--disabled-fill` | `#a19f9d` / `#c8c6c4` | disabled text / disabled primary button, blade edge |
| `--switch-off` | `#616161` | switch track and knob when off |
| `--log-bg` | `#1b1a19` (text `--line`) | log viewer |
| `--bad-border` / `--bad-soft` | `#f1bbbc` / `#fdf3f4` | error banner |

Type: **Source Sans 3** (400/600) for UI, **Source Code Pro** (400/500) for ids, paths, numbers.
Both ship as woff2 files with hotfire (`hotfire/console/`, SIL OFL): the console makes no outside requests.
Sizes: 22 page title, 20 blade title, 14 body/table, 13 secondary, 12.5 mono.
Radius: 0 everywhere except inputs/buttons (2px) and filter chips (12px). No shadows except the
blade's left edge (`-6px 0 16px rgba(0,0,0,.06)`).

## Components

- **Top bar** (40px, `--accent`): product name, host, global search, sample/status note.
- **Resource menu** (228px, white, right rule): filter input, groups (bold 13px label), items
  14px; active item = `--accent-soft` background + 3px inset accent bar + 600 weight.
- **Breadcrumb + title**: `Home > hotfire@<host>`, title `hotfire@<host> | <Page>`, muted subtitle.
- **Command bar**: borderless 36px text buttons between two hairlines; primary action in accent
  colour; vertical rule separates action groups.
- **Filter row**: text filter (280px) + chips `Field == value`, count on the right.
- **Data grid**: white, 1px `--line` frame, 600-weight headers, row hover `#f5f5f5`, selected
  row `--accent-soft`; first column is a link that opens the blade.
- **State badge**: 9px dot in the state colour + label (never colour alone).
- **Blade** (520px, right side): title + mono subtitle, close button, its own command bar,
  tabs (active = accent text + 2px underline), scrolling body, sticky footer with Apply
  (primary) and Discard (secondary).
- **Form**: 13px bold label above, 32px input, help text below in `--muted`; toggles are
  40×20 switches (`role="switch"` on a real button); a grey "command" box shows the exact
  CLI equivalent of the settings.
