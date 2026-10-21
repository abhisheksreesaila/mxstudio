# Decisions (autopilot)

One line per fork: what was chosen over what, why, and whether it can be undone. The owner reads this instead of being asked.

- D-01 · 2026-10-21 · worked in mxstudio itself, not a separate repo · omxconsole isn't live (Ship: direct, nobody uses it yet) and this is finishing work on approved tickets · reversible: yes (git)
- D-02 · pull SmolLM2-360M-Instruct (~0.7 GB) for the live states, not qwen2.5-3b (6 GB) · the smallest supported model that still goes through fetch, compile, serve and smoke · reversible: yes (`mxc rm`)
- D-03 · leave the port state and Security's mid-setup state to unit tests · a port is a paid Claude Code run, and the machine is already secured · reversible: yes
- D-04 · the model command bar drops Logs, rather than shrinking every button to an icon · the left menu already opens Logs, so it was there twice, and dropping one button lets Remove fit on the line at 1280 px · reversible: yes
- D-05 · an unloaded model with recent requests says "Not loaded right now · N requests in the last 5 min" instead of "No traffic yet" · the old line contradicted the count beside it · reversible: yes
- D-06 · an empty Benchmark hides the header's Run button and keeps the empty state's one action · one filled verb per view; the empty state's button is the one that explains itself · reversible: yes
- D-07 · the Beacon gets a "starting" state (a model compiling or loading) between crash and running · during the live pull it said "idle" through a minute of compile, the one moment something is clearly happening · reversible: yes
- D-08 · on the breadcrumb the parent crumb never shrinks; only the current one truncates · "My mo…" next to a long repo name read as broken · reversible: yes
- D-09 · to catch the Beacon's pull state, pull Qwen/Qwen2.5-Coder-1.5B-Instruct (~3 GB, long enough to see) and cancel it from the Beacon's two-tap Cancel · proves the cancel path live too; the partial download stays in the HF cache · reversible: yes
- D-10 · left both pulled models registered (smollm2-360m-instruct, qwen2.5-coder-1.5b-instruct), not removed · they were added for the check, are small, and removing models is the owner's call; `mxc rm <name>` undoes each (weights stay in the HF cache) · reversible: yes
- D-11 · when a wide Beacon leaves the breadcrumb under 240 px, the top bar goes "tight": the tools fold to icons (as below 1240 px) before the breadcrumb loses its room · measured each time the Beacon or the window resizes · reversible: yes
