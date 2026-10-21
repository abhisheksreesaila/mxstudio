# Decisions (autopilot)

One line per fork: what was chosen over what, why, and whether it can be undone. The owner reads this instead of being asked.

- D-01 · 2026-10-21 · worked in mxstudio itself, not a separate repo · omxconsole isn't live (Ship: direct, nobody uses it yet) and this is finishing work on approved tickets · reversible: yes (git)
- D-02 · pull SmolLM2-360M-Instruct (~0.7 GB) for the live states, not qwen2.5-3b (6 GB) · the smallest supported model that still goes through fetch, compile, serve and smoke · reversible: yes (`mxc rm`)
- D-03 · leave the port state and Security's mid-setup state to unit tests · a port is a paid Claude Code run, and the machine is already secured · reversible: yes
- D-04 · the model command bar drops Logs, rather than shrinking every button to an icon · the left menu already opens Logs, so it was there twice, and dropping one button lets Remove fit on the line at 1280 px · reversible: yes
- D-05 · an unloaded model with recent requests says "Not loaded right now · N requests in the last 5 min" instead of "No traffic yet" · the old line contradicted the count beside it · reversible: yes
- D-06 · an empty Benchmark hides the header's Run button and keeps the empty state's one action · one filled verb per view; the empty state's button is the one that explains itself · reversible: yes
