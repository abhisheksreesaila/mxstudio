# Porting a model mxconsole can't run yet

mxconsole runs any model whose architecture MAX already supports. For one it doesn't support, it can
**port** the architecture: an agent writes a small MAX package for it, mxconsole checks that it matches the
original Hugging Face model, and the model then works like any other.

## Three kinds of model

| `mxc search` says (console label) | What it means | What you do |
|---|---|---|
| `ok` / `tight` / `no` (fits / tight / too big) | MAX supports the architecture | `mxc serve <model>` |
| `port` (needs porting) | MAX doesn't support it yet | `mxc port <model> --auto` |
| parked (from `mxc port`) | needs remote code; nothing is spent | nothing, or ask us |

## Why it's a separate step from pulling

A pull downloads files: it's free and predictable, and it either works or tells you why. A port is a different
kind of job. It spends your Claude usage (about $2–5), takes 7–40 minutes, needs Claude Code and Linux with bubblewrap,
and can fail after spending it. So mxconsole never starts one on its own. `mxc pull` notices the problem early
(it reads `config.json` before downloading the weights) and tells you to run `mxc port`. In the console, **Add a
model** shows the model as "needs porting" with a **Port it** button.

## Porting

```bash
pixi run mxc port <hf-repo> --auto      # or "Port it" in the console's Add a model
```

What happens, in order:

1. **Workspace.** mxconsole fetches the model's config and picks the closest architecture MAX already supports as
   the starting point. It downloads the weights once.
2. **Generate.** Claude Code writes the port in a sandbox, following Modular's own porting skill.
3. **Try.** The agent compiles its package, serves it, sends a prompt and compares the output with the Hugging
   Face model (`try-port`). It fixes what it sees. That's up to 4 tries per attempt.
4. **Final check.** mxconsole runs the comparison again on its own. The port must match Hugging Face within
   tolerance.
5. **Register and serve.** The model is added at a pinned revision and started.

If an attempt fails, mxconsole feeds the error back and tries again. It makes 3 attempts by default
(`--max-attempts`). It stops early when the same failure happens twice in a row.

### What you need

- **Claude Code**, installed and signed in (`claude` on the PATH, or `MXC_CLAUDE=/path/to/claude`). A port uses
  your Claude account: about $2–5 per model in our tests.
- **Linux with bubblewrap** (`bwrap`). The agent reads untrusted files (the config and the model card), so it
  runs sandboxed. Without bubblewrap you have to pass `--unsandboxed`, which isn't recommended, and the agent
  can't run `try-port`.
- **Time:** 7–40 minutes per model, mostly downloading the weights and compiling.

### What you get back

- **Ported and serving.** The model is in `mxc ls` with source `port`. It uses the same endpoint, keys and idle
  unload as any other model.
- **Parked.** The model's config loads Python code from Hugging Face (remote code), which mxconsole doesn't run.
  It says so before spending anything.
- **Failed.** A model that hits a MAX limitation (BLOOM's ALiBi positional bias, for example) usually stops
  after 2 attempts with "the same failure twice in a row". In every failure, the message says which attempt got
  furthest ("built but did not serve", "served; the comparison
  failed"). That attempt's package is kept in the workspace, and the message points to the full log. If Hugging
  Face itself was unreachable or rate-limiting, the message says so and doesn't blame the port.

### Guided mode, and registering a port yourself

```bash
pixi run mxc port <hf-repo>                         # workspace + PORT.md + the Claude command to run yourself
pixi run mxc pull <hf-repo> --port <package-dir>    # register a finished port
```

## Where things are

| Path | What |
|---|---|
| `~/.mxc/ports/<model>/` | the workspace: `PORT.md`, `config.json`, and the package under `port/` |
| `~/.mxc/ports/<job>/port.log` | everything the job did, readable |
| `~/.mxc/ports/<job>/agent-<n>.jsonl` | the agent's raw transcript for attempt *n* (it can contain anything the agent printed; don't share it unreviewed) |
| `~/.mxc/skills/modular/` | Modular's porting skills, kept up to date, with two known bugs patched locally ([modular/skills#15](https://github.com/modular/skills/issues/15)) |

## What's proven

Tested on Linux with 2× RTX 3090 and MAX 26.7, text LLMs only (details in [port-study.md](port-study.md)):

| Ported and serving | Parked |
|---|---|
| GPT-2, Pythia (GPT-NeoX), Phi-2, StarCoder2, StableLM 2, SmolLM3, Granite MoE, OLMoE | BLOOM (ALiBi: fails; parked by us), EXAONE (remote code: parked automatically) |

## Limits

- **Text LLMs only so far.** Vision and embedding architectures haven't been tried through porting.
- **Safetensors weights only.** Repos with only `.bin` or GGUF files are refused.
- **It must fit one GPU** at the precision the weights are stored in.
- **A port is tied to a MAX version.** It's built against the MAX version mxconsole serves with, and the porting
  skill's environment is pinned to the same version. After a MAX upgrade, a port may need redoing.
- **Ported code runs inside mxconsole's serving.** The agent writes Python that MAX loads. It's sandboxed while
  it's written and checked, but at serving time it runs like any other model code. Review it if that matters to
  you.
- **The checks compare with the original model.** They compare the first-token score and the top token at
  several prefix lengths against Hugging Face. They don't evaluate quality on your workload.
