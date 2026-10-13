# Port study (F-033)

Can the import-model skill port unsupported architectures reliably, and which steps are mechanical enough
for a small local model (LoRA) to do? Each run: `mxc port <ref> --auto`, Claude Code, MAX 26.7.0.dev2026100905.

## Results

| Architecture | Repo | Outcome | Attempts | Clean workspace | Notes |
|---|---|---|---|---|---|
| OlmoeForCausalLM (MoE, 64 experts, top-8) | allenai/OLMoE-1B-7B-0924-Instruct | passed parity, serving, answers | 2 | no (reused 2026-10-11) | run 11, 2026-10-13 |

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

## Harness and skill fixes found
- [ ] let the agent run the skill's checks in the form it actually uses (OLMoE)
- [ ] a writable, per-job MAX compile cache in the sandbox (OLMoE)
- [ ] decide: parity flags vs the arch opt-out for the overlap scheduler (OLMoE)

## Verdict
Pending (needs 5 to 10 clean runs).
