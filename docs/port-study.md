# Port study (F-033)

Can the import-model skill port unsupported architectures reliably, and which steps are mechanical enough
for a small local model (LoRA) to do? Each run: `mxc port <ref> --auto`, Claude Code, MAX 26.7.0.dev2026100905.

## Results

| Architecture | Repo | Outcome | Attempts | Clean workspace | Notes |
|---|---|---|---|---|---|
| OlmoeForCausalLM (MoE, 64 experts, top-8) | allenai/OLMoE-1B-7B-0924-Instruct | passed parity, serving, answers | 2 | no (reused 2026-10-11) | run 11, 2026-10-13 |
| GPT2LMHeadModel | openai-community/gpt2 | passed parity, serving | 1 | yes | run 13: 13 min total, agent 9.3 min, 109 turns, $3.85; ran its own checks 13 times |

## Findings

### OLMoE (run 11)
- Attempt 1 failed parity, but not because of the model: both logit gates got HTTP 400, since the overlap
  scheduler refuses logprobs. mxconsole already starts the parity server with `--no-enable-overlap-scheduler`
  `--no-device-graph-capture`, and MAX 26.7 still turned it on. Claude fixed it in the port's `arch.py`
  (`supports_overlap_scheduler=False`, `supports_device_graph_capture=False`). Reasoning, not mechanics: it
  read MAX's source to find out why. Cost: the ported model is served without the overlap scheduler.
- The agent worked blind. Every Bash command it tried was denied: it calls the checks as
  `pixi run --manifest-path <skill>/pixi.toml python <skill>/scripts/check_port.py …`, and the allowlist only
  has `pixi run python scripts/check_port.py:*` from the skill root; `cd …; ls`/`cat`/`grep` are denied by
  design (it has Read/Glob/Grep). So it never ran check_port.py itself; only mxconsole's outer loop checked.
- MAX's compile cache isn't writable inside the sandbox, so every parity check compiles cold (minutes each).
- Raw transcripts (F-032) weren't kept for this run: the daemon started before that change.

### GPT-2 (run 13)
- First attempt passed: the agent ran check_port.py itself this time (13 allowed skill-script calls).
- 4 denials, all harmless: a shell `for` loop over scripts, `python -c` to grep MAX's source (Grep would do),
  `ruff format` on the port (the skill uses ruff; worth allowing on the port dir), and a check piped to `tail`.
- Compile cache: the MODULAR_CACHE_DIR warning is gone, but MAX's engine cache (`share/max/.max_cache/mof/mef`,
  from the env's modular.cfg `cache_dir`) is still read-only in the sandbox. Open: find its override.

## Harness and skill fixes found
- [x] let the agent run the skill's checks in the form it actually uses (OLMoE; c2b329d)
- [x] a writable, per-job MAX compile cache in the sandbox (OLMoE; c2b329d)
- [x] overlap scheduler: the parity server forces it off (`--force`) instead of each port opting out, so
      ported models keep it for normal serving. `--force` also skips an architecture's required arguments, so
      the parity server turns prefix caching and chunked prefill off itself. Trade-off: parity never exercises
      the overlap scheduler (logprobs need it off); normal serving uses it.
- [ ] MAX engine compile cache (`.max_cache/mof/mef`) still read-only in the sandbox (GPT-2)
- [ ] allow `ruff format`/`ruff check` on the port dir through the skill env (GPT-2)
- note: the OLMoE port still has `supports_overlap_scheduler=False` from run 11; it can be dropped now.

## Verdict
Pending (needs 5 to 10 clean runs).
