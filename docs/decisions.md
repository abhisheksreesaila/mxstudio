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
- D-12 · left the approved mock unchanged · it only ever shows two crumbs (My models › model), which is what the parent+current breadcrumb shows too, so there is nothing to bring into line · reversible: yes
- D-13 · kept security.png as is, though it shows /home/abhisheksreesaila/... in the CA path and the host ablearn.local · neither is a secret (the username is the public GitHub name), and the old screenshots showed the same; blur it if the docs site should stay anonymous · reversible: yes
- D-14 · overnight, built F-050/F-051 (Mac memory, port guard, first-run launcher, .app bundle) before F-049's Mac run, though the brief put the Mac check first · the owner said "finish all three" and went to sleep; the code is small, platform-guarded and inert on Linux, so the only cost of MAX failing on the Mac is this night's work · reversible: yes
- D-15 · on a Mac, the supervisor sizes each model's memory share from the unified memory but doesn't pass `--devices gpu:0`; MAX picks the device as plain `max serve` does · the Mac check's step 1 (plain max serve) and step 2 (mxc serve) then run the same device, so F-049 tests what we ship; whether MAX on Apple silicon wants gpu:0 is unknown until then · reversible: yes
