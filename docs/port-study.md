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
| ExaoneForCausalLM (remote code) | LGAI-EXAONE/EXAONE-3.5-2.4B-Instruct | failed: max serve won't load it without `--trust-remote-code` | 3 | yes | run 19: 18 min, $3.75; never reached the logit gates |
| SmolLM3ForCausalLM (NoPE layers) | HuggingFaceTB/SmolLM3-3B | failed: our bug (HF reference couldn't load) — port probably fine | 3 | yes | run 20: 17 min, $1.70; smoke fluent every attempt |
| GraniteMoeForCausalLM (MoE, 32 experts) | ibm-granite/granite-3.1-1b-a400m-instruct | passed parity, serving | 3 | yes | run 21: 17.7 min, $3.19; prefill rel_diff 0.10 → 0.13 → 0.036 |

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

### EXAONE 3.5 (run 19)
- Not a port problem. EXAONE's config.json points AutoConfig at Python code on the Hub, and `max serve` loads the
  config (with trust_remote_code=False) before it imports `--custom-architectures`, so the port's own config
  class registers too late. check_port passed (272/272 tensors). The agent worked this out from MAX's source and
  on attempt 3 changed nothing and said mxconsole must serve it with `--trust-remote-code`. Right call.
- A product decision, not a harness bug: `--trust-remote-code` runs the model author's Python. In the parity server
  that's inside the sandbox; in normal serving it would run unsandboxed in mxconsole's own process tree.
- The agent also couldn't read the weights snapshot (outside its workspace) to confirm config field names.
- Should have stopped after attempt 1: the error was the same each time and outside the port's reach.

### SmolLM3 (run 20)
- Our bug, not the port's: the weights download skipped `chat_template.jinja`, so the HF reference tried to fetch
  it into the read-only cache and crashed ("HF side failed: Read-only file system"). The logit gates never ran.
  Smoke output was fluent every attempt (" Paris. The Eiffel Tower is in Paris.").
- The same gap affects normal `mxc pull`: repos with a separate chat template file got no chat template.
- The agent correctly saw nothing to fix: attempts 2 and 3 cost $0.65 together and changed little.
- An infrastructure failure on the HF side should never count as the port's failure (same as BLOOM's 429).

### Granite MoE (run 21)
- A genuine numeric bug fixed without seeing intermediate values: logits_prefill rel_diff 0.10, then 0.13 (worse),
  then 0.036 (pass). The agent got there on the last attempt by reading code, not data. Lucky as much as good.

### Granite MoE again, with try-port (run 22)
- Same model as run 21, fresh workspace, after fix 1 (try-port). Passed on the first harness attempt:
  agent 10.1 min, 63 turns, $2.30 (run 21: 3 attempts, $3.19). Final rel_diff 0.0055 (run 21: 0.036).
- The agent debugged with data: try 1 showed rel_diff 0.131; it kept bf16 matmuls but moved the residual stream
  and expert combine to fp32; try 2 showed 0.057 ("precision was the issue"); try 3 passed at 0.0055.
- No "cache not writable" warnings any more (MODULAR_MAX_CACHE_DIR).
- Side observation: logits_prefill uses a *relative* threshold (0.05) on a logprob near zero (-0.096), so an
  absolute gap of 0.013 nats failed. Strict on confident tokens; here it pushed the port to a genuinely tighter match.

## Harness and skill fixes found
- [x] let the agent run the skill's checks in the form it actually uses (OLMoE; c2b329d)
- [x] a writable, per-job MAX compile cache in the sandbox (OLMoE; c2b329d)
- [x] overlap scheduler: the parity server forces it off (`--force`) instead of each port opting out, so
      ported models keep it for normal serving. `--force` also skips an architecture's required arguments, so
      the parity server turns prefix caching and chunked prefill off itself. Trade-off: parity never exercises
      the overlap scheduler (logprobs need it off); normal serving uses it.
- [x] MAX engine compile cache: MODULAR_MAX_CACHE_DIR (d970700; no warnings in run 22)
- [ ] allow `ruff format`/`ruff check` on the port dir through the skill env (GPT-2)
- [x] let the agent compile, serve and run compare_layers itself: try-port (d970700, 339e00f). Granite went from
      3 blind attempts to 1 attempt with 3 informed tries (run 22)
- [x] an infrastructure failure (Hub 429, 5xx, network, our read-only cache) retries the check instead of using
      up an attempt, and stops saying it isn't the port if it persists (0228132) (BLOOM)
- [x] stop early on the same failure twice in a row (0228132). The gate's wall check didn't flag ALiBi for BLOOM
      (walls PASS), so "known wall" detection would need the skill's own wall list; the repeat rule covers it (BLOOM)
- [x] keep the best attempt: the furthest attempt's package is restored and named in the message (0228132) (StableLM)
- [ ] the logits_multi failure at prefix length 1 (StableLM): the HF-side tokenization is ruled out (2026-10-13:
      "The" decodes and re-encodes to the same single id, no BOS, for StableLM, GPT-2 and Granite). Left: MAX's
      server adding a BOS, or a near-tie on the flat distribution after one word. Re-run StableLM with try-port
      to see; report upstream only once confirmed (StableLM)
- [x] download chat_template.jinja / chat_template.json with the weights (SmolLM3; also fixes plain pulls)
- [x] remote-code configs (auto_map for a model type transformers doesn't know) are parked before the agent runs
      (0228132) (EXAONE). Allowing `--trust-remote-code` stays the person's call.
- [x] the same serve error twice in a row stops (0228132) (EXAONE)
- [ ] feed compare_layers' per-layer output into the retry prompt, not only the gate summary (BLOOM)
- note: the OLMoE port still has `supports_overlap_scheduler=False` from run 11; it can be dropped now.

## Verdict (2026-10-13, 9 clean runs + OLMoE)

**Score.** 5 of 9 clean runs ported and serve (GPT-2, Pythia, StarCoder2, Phi-2, Granite MoE), plus OLMoE. Of the 4
failures only one was the port's fault (BLOOM, a known wall). StableLM 2 and SmolLM3 were very likely correct and
lost to a gate false negative and our own download bug; EXAONE needs `--trust-remote-code`, a policy call. With the
harness fixes below, expect 7 of 9. **The skills are good enough; the harness was the weak part.**

**Cost.** $36.43 of agent time for the 9 runs (about $4 per model; $2-4 for a first-try pass), 13-40 min wall clock
each, most of it weight download and MAX compile.

**What the work is.** A first-try port is 40-110 turns, about 60% reading (Read/Grep/Glob over the skill, the
donor architecture and MAX's own source) and a handful of writes. When the architecture is a close variant of a
MAX donor (GPT-2, Pythia, Phi-2) it is close to mechanical. The hard parts all needed real reasoning over MAX's
source: a numeric mismatch (BLOOM, Granite), a kernel limit surfacing as a compile error (StarCoder2), loader
order (EXAONE), scheduler behaviour (OLMoE).

**LoRA.** Not yet. Five successful transcripts is far too little training data, and the hard cases are exactly
the ones a small model would fail. Next measurement instead: once the agent is swappable (F-034), run an open
coding model served by mxconsole itself over these same 9 architectures, unchanged. If it passes the
close-variant ones, local-first porting works for the common case and Claude stays the fallback for the rest;
fine-tune only if that baseline falls short and the catalog (F-035) has collected enough transcripts.

**Next, in order.** (1) let the agent compile, serve and run compare_layers itself; (2) infrastructure failures
retry instead of using an attempt; (3) keep the best attempt; (4) stop early on a known wall, remote code, or the
same error twice; (5) confirm and report the short-prefix logits_multi false negative upstream.
