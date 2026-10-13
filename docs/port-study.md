# Port study (F-033)

Rule (the person's call, 2026-10-13): a port that is too hard gets its findings written down and is left for
later, not chased. Popular architectures will be ported anyway; obscure ones only matter if someone asks.

Can the import-model skill port unsupported architectures reliably, and which steps are mechanical enough
for a small local model (LoRA) to do? Each run: `mxc port <ref> --auto`, Claude Code, MAX 26.7.0.dev2026100905.

## Results

| Architecture | Repo | Outcome | Attempts | Clean workspace | Notes |
|---|---|---|---|---|---|
| OlmoeForCausalLM (MoE, 64 experts, top-8) | allenai/OLMoE-1B-7B-0924-Instruct | passed parity, serving, answers | 2 | no (reused 2026-10-11) | run 11, 2026-10-13 |
| GPT2LMHeadModel | openai-community/gpt2 | passed parity, serving | 1 | yes | run 13: 13 min total, agent 9.3 min, 109 turns, $3.85; ran its own checks 13 times |
| BloomForCausalLM (ALiBi) | bigscience/bloomz-560m | failed parity after 3 attempts | 3 | yes | run 14: 24 min; logits close but off (rel_diff 0.30); attempt 3 lost to a Hub 429 |
| GPTNeoXForCausalLM (parallel residual, partial rotary) | EleutherAI/pythia-1.4b-deduped | passed parity, serving | 1 | yes | run 15: 17.5 min total, agent 5.9 min, 83 turns, $2.98 |
| StableLmForCausalLM | stabilityai/stablelm-2-1_6b-chat | failed after 3 attempts (but attempt 1 was probably right) | 3 | yes | run 16: 22.5 min; agent 4-5 min and ~$2 per attempt, $5.68 total |
| Starcoder2ForCausalLM (GQA group 12, sliding window) | bigcode/starcoder2-3b | passed parity, serving | 2 | yes | run 17: 40 min total (12 GB download), agent 10.8 min over 2 attempts, $4.86 |
| PhiForCausalLM (parallel attn+MLP, partial rotary) | microsoft/phi-2 | passed parity, serving | 1 | yes | run 18: 17.7 min total, agent 6.4 min, 82 turns, $3.29; rel_diff 0.003 |

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

### BLOOM (run 14)
- A real numerical problem: ALiBi, which the skill lists as a wall (no first-class ALiBi in MAX 26.x). The agent
  found a workaround (flash attention's `rel_logits` bias), and the logits came out close but wrong
  (top-1 logprob -0.27 vs -0.21; first mismatch at prefix length 5).
- Attempts 1 and 2 gave identical numbers: the second "fix" changed nothing that mattered. The agent was guessing,
  because it can't compile or serve the model itself (check_port only binds weights) and was told not to start
  `max serve`, so it never saw a logit and couldn't run the skill's layer-by-layer debugging (compare_layers needs
  a server). It said so each time.
- Attempt 3 rewrote ALiBi entirely (heads padded, ALiBi carried through the RoPE kernel), but the verify gate's
  `checkpoint_meta` got HTTP 429 from huggingface.co (unauthenticated: the HF token is scrubbed), so the logit
  gates were skipped and the attempt was wasted on an infrastructure error.
- Parked: not retried. Revisit only if someone asks for a BLOOM-family model.
- For the LoRA question: writing the port was mechanical; debugging numerics against a wall is the hard part,
  and even Claude can't do it without seeing intermediate values.

### StableLM 2 (run 16)
- Attempts 1 and 2 were very likely correct: fluent output ("The capital of France is" → " Paris, and it is one of
  the most visited cities in the world."), logits_prefill passed (rel_diff 0.02, then 0.003). Only
  logits_multi failed, and only at prefix length 1.
- Suspected false negative in Modular's gate: compare_layers.py `probe_multi_position` gives HF the token ids
  `ids[:pos]` but gives MAX the decoded *text*, which the server re-tokenizes. At one token, any difference in
  BOS handling between the HF tokenizer call and the server changes the whole context. The skill already skips
  BOS-only prefixes for this reason; a one-word prefix has the same problem. Not confirmed yet.
- The agent, told to fix a bug that probably wasn't there, changed working code; attempt 3 produced empty
  output (smoke failed). The harness threw away a probably-good port.

### StarCoder2 (run 17)
- Attempt 1 built but failed to compile at serve time: MAX's NVIDIA attention kernel only supports GQA groups
  of up to 8, and StarCoder2-3B has 24 query heads over 2 KV heads (group 12). Nothing the agent can run
  (check_port) compiles the graph, so only the harness's serve revealed it, after a 2-minute compile.
- Attempt 2 fixed it from the compile error fed back, and parity passed. A hardware/kernel limit learned from
  an error message: the kind of thing the skill could list as a known constraint.

## Harness and skill fixes found
- [x] let the agent run the skill's checks in the form it actually uses (OLMoE; c2b329d)
- [x] a writable, per-job MAX compile cache in the sandbox (OLMoE; c2b329d)
- [x] overlap scheduler: the parity server forces it off (`--force`) instead of each port opting out, so
      ported models keep it for normal serving. `--force` also skips an architecture's required arguments, so
      the parity server turns prefix caching and chunked prefill off itself. Trade-off: parity never exercises
      the overlap scheduler (logprobs need it off); normal serving uses it.
- [ ] MAX engine compile cache (`.max_cache/mof/mef`) still read-only in the sandbox (GPT-2)
- [ ] allow `ruff format`/`ruff check` on the port dir through the skill env (GPT-2)
- [ ] let the agent compile, serve and run compare_layers itself (a harness-run server it can call), so numeric
      bugs are debugged with data, not guessed (BLOOM). The biggest open item.
- [ ] an infrastructure failure (Hub 429, network) retries the gate instead of using up an attempt; read
      checkpoint metadata from the local weights where possible (BLOOM)
- [ ] stop early on a known wall (check_walls flags it): say what blocks it and park, instead of spending all
      attempts (BLOOM)
- [ ] keep the best attempt: if a later attempt does worse (e.g. smoke fails where it passed), restore the better
      package and report it as "close" rather than failed (StableLM)
- [ ] confirm the logits_multi short-prefix false negative (send MAX token ids, or skip prefixes under ~4 tokens)
      and report it upstream to modular/skills (StableLM)
- [ ] feed compare_layers' per-layer output into the retry prompt, not only the gate summary (BLOOM)
- note: the OLMoE port still has `supports_overlap_scheduler=False` from run 11; it can be dropped now.

## Verdict
Pending (needs 5 to 10 clean runs).
