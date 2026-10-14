# `mxc` command reference

Every command, grouped by what you're doing. Run them as `pixi run mxc …` inside the repo.

**Conventions**
- **Model names:** a short name (`qwen2.5-0.5b`), a Hugging Face repo id (`HuggingFaceTB/SmolLM3-3B`), or, once
  registered, the name `mxc ls` shows.
- **JSON output:** `--json` prints JSON only on stdout, for scripts and agents.
- **Errors:** one `error:` line on stderr.
- **Exit codes:** 0 ok, 1 refused, 2 bad usage. `pull` and `port --auto` exit 130 when you cancel with Ctrl-C.
- **The daemon:** `serve`, `pull`, `port --auto`, `bench`, `console` and `app` start it if it isn't running. Every other
  command works without it; the ones marked *no daemon needed* say so in their `--help`.

Contents:
- [Serve and call a model](#serve-and-call-a-model)
- [Find and add models](#find-and-add-models)
- [Port a model MAX can't run yet](#port-a-model-max-cant-run-yet)
- [Learn how a model works](#learn-how-a-model-works)
- [Watch and measure](#watch-and-measure)
- [Share on your network](#share-on-your-network)
- [API keys](#api-keys)
- [Console and daemon](#console-and-daemon)
- [Environment variables](#environment-variables)

---

## Serve and call a model

### `mxc serve`

Pulls the model if needed, starts the daemon, loads the model and prints the URL and `curl` and Python snippets. The
first time, it also creates a key and prints it once; after that, use your app's key.

```bash
mxc serve qwen2.5-0.5b
mxc serve smollm2-1.7b --pin            # never unloads
mxc serve qwen2.5-1.5b --keep-alive 10m
```

| Option | |
|---|---|
| `--pin` | Keep it loaded; never unload when idle. |
| `--keep-alive 90s\|10m\|1h` | Unload after this much idle time (default 5m). |
| `--no-wait` | Register and start the daemon, but load on the first request. |

### `mxc stop`

```bash
mxc stop qwen2.5-0.5b      # unload one now
mxc stop --all             # unload everything and stop the daemon
```

| Option | |
|---|---|
| `--all` | Unload every model and stop the daemon. |

### `mxc set`

Changes the same settings the console's model panel shows.

```bash
mxc set qwen2.5-0.5b --pin
mxc set qwen2.5-0.5b --keep-alive 10m
```

| Option | |
|---|---|
| `--pin` / `--unpin` | Keep it loaded, or let it unload when idle again. |
| `--keep-alive 90s\|10m\|1h` | Unload after this much idle time. |
| `--json` | JSON output. |

### `mxc ls`

Registered models and their live state (cold, starting, ready, crashed). Ported models show source `port`.

| Option | |
|---|---|
| `--json` | JSON output. |

### `mxc rm`

Stops a model, unregisters it and deletes its compiled-graph cache. The weights stay unless you pass `--purge`.

| Option | |
|---|---|
| `--purge` | Also delete its weights from the Hugging Face cache. |
| `--json` | JSON output. |

---

## Find and add models

### `mxc search`

Searches the short names, MAX's catalog and Hugging Face, and says in the FIT column whether each result fits
this machine: `ok`, `tight`, `no` (too big, with smaller suggestions), or `port` (MAX doesn't support the
architecture yet; the console calls this "needs porting").

```bash
mxc search qwen
mxc search HuggingFaceTB/SmolLM3-3B
```

| Option | |
|---|---|
| `--json` | JSON output. |

### `mxc pull`

Resolves a model, downloads it at a pinned revision with live progress, and registers it. It runs in the daemon,
so the console shows it too. Ctrl-C cancels; running it again resumes the download.

It reads the model's `config.json` before downloading anything. If MAX can't run the architecture, it stops there
and tells you to run `mxc port`, so no weights are wasted.

```bash
mxc pull qwen2.5-3b
mxc pull meta-llama/Llama-3.2-1B --dry-run    # resolve only
mxc pull acme/model --port ~/.mxc/ports/model/port/pkg   # register a port you finished yourself
```

| Option | |
|---|---|
| `--dry-run` | Only resolve; download nothing. |
| `--name N` | Register under this name. |
| `--port DIR` | Register a finished port (from `mxc port`) instead. |
| `--json` | One JSON object per progress event. |

---

## Port a model MAX can't run yet

### `mxc port`

Adds an architecture MAX doesn't support. With `--auto`, Claude Code writes a small MAX package for it in a
sandbox. mxconsole checks that the package matches the original Hugging Face model, then registers it and serves
it. Expect $2–5 of Claude usage and 7–40 minutes per model. It needs Claude Code, and Linux with bubblewrap. Elsewhere it only runs with `--unsandboxed`.

```bash
mxc port openai-community/gpt2 --auto     # unattended: ends with "Ported … and serving it"
mxc port openai-community/gpt2            # guided: a workspace and the Claude command to run yourself
```

| Option | |
|---|---|
| `--auto` | Run the port unattended in the daemon. |
| `--max-attempts N` | With `--auto`: attempts before giving up (default 3). Each failure is fed back to the next attempt. |
| `--unsandboxed` | With `--auto`: run without bubblewrap. Not recommended. |
| `--json` | With `--auto`: one JSON object per change. |

What it needs, what can come back (ported, parked or failed) and the limits: [PORTING.md](PORTING.md).

---

## Learn how a model works

### `mxc learn`

Builds a notebook from Hugging Face metadata: the model card, a config table, a parameter breakdown, sample code
wired to your endpoint, and where MAX's implementation of the architecture lives. Then it opens the notebook in
Jupyter Lab. Works for a repo you haven't pulled, too.

```bash
mxc learn qwen2.5-0.5b
mxc learn HuggingFaceTB/SmolLM3-3B --no-open
```

| Option | |
|---|---|
| `--no-open` | Write the notebook, but don't launch Jupyter Lab. |
| `--json` | Print the notebook (path, cells, architecture) as JSON; never launches Jupyter. |

The "Try it" cells need `MXC_API_KEY` set before Jupyter starts.

---

## Watch and measure

### `mxc status`

*No daemon needed.* One screen: daemon, endpoints, models, keys, LAN access and MAX version.

| Option | |
|---|---|
| `--json` | JSON output. |

### `mxc ps`

*No daemon needed.* Every process mxconsole runs (daemon, Caddy, LiteLLM, each `max serve`) with pid, port, memory and
uptime.

| Option | |
|---|---|
| `--json` | JSON output. |

### `mxc logs`

*No daemon needed.* Tails a model's server log.

```bash
mxc logs qwen2.5-0.5b -f
```

| Option | |
|---|---|
| `--lines N`, `-n N` | How many trailing lines (default 50). |
| `--follow`, `-f` | Keep printing new lines until Ctrl-C. |

### `mxc metrics`

*No daemon needed.* The console's numbers: requests, errors and latency per model, and GPU use.

| Option | |
|---|---|
| `--range 5m\|1h\|24h\|7d` | Time window (default 5m). |
| `--json` | JSON output. |

### `mxc requests`

*No daemon needed.* The activity log, newest first.

| Option | |
|---|---|
| `--limit N` | How many recent requests (default 20). |
| `--json` | JSON output. |

### `mxc bench`

Runs the same streamed workload on each model through the daemon and lists them fastest first: decode speed,
prefill speed and time to first token.

```bash
mxc bench --all
mxc bench qwen2.5-0.5b smollm3-3b --runs 10
```

| Option | |
|---|---|
| `--all` | Benchmark every registered model. |
| `--runs N` | Timed runs per model after one warm-up, 1–50 (default 5). |
| `--json` | JSON output. |

---

## Share on your network

### `mxc expose`

Serves your models on the LAN over HTTPS. It starts Caddy (TLS with its own certificate authority, port 8443) and
LiteLLM as your user, so no password is needed. It prints the `https://<host>.local:8443/v1` address and the
certificate to trust on other devices. Each app needs its own key ([`mxc keys`](#api-keys)).

```bash
mxc expose
mxc expose --host box.local
mxc expose --stop
```

| Option | |
|---|---|
| `--host NAME` | The hostname clients use (default `<this machine>.local`). |
| `--litellm` / `--no-litellm` | Route through LiteLLM (default) or straight to the daemon. |
| `--dry-run` | Write the configs, but start nothing. |
| `--stop` | Stop Caddy and LiteLLM. |

---

## API keys

One `mst-…` key per app. Keys are stored only as hashes, so each one is printed once: save it then.

### `mxc keys new`

```bash
mxc keys new my-app
```

| Option | |
|---|---|
| `--rotate` | Revoke the app's current key and issue a new one. |

### `mxc keys rotate`

Issues a new key for an app and revokes the old one.

| Option | |
|---|---|
| `--json` | JSON output. |

### `mxc keys ls`

Lists keys, never the secret.

| Option | |
|---|---|
| `--json` | JSON output. |

### `mxc keys revoke`

Disables an app's key right away.

```bash
mxc keys revoke my-app
```

---

## Console and daemon

### `mxc console`

Prints the console link for a page and opens it in your browser. The console only works on this machine.

```bash
mxc console                       # My models
mxc console add                   # Add a model
mxc console notebook smollm3-3b   # a model's panel, on the Notebook tab
mxc console api                   # the HTTP API reference (/docs)
```

Pages: `home`, `add`, `keys`, `gateway`, `settings`, `api`; with a model: `model`, `security`, `bench`, `logs`,
`notebook`. Older page names still work.

| Option | |
|---|---|
| `--no-open` | Only print the link. |

### `mxc app`

Opens the console in its own window. Closing the window stops nothing.

| Option | |
|---|---|
| `--install` | Linux: add an mxconsole launcher to the app menu. |
| `--uninstall` | Linux: remove that launcher. |

### `mxc daemon`

Runs the gateway in the foreground with logs on stdout. Ctrl-C stops it and unloads every model. You rarely need
this: `serve`, `pull` and `console` start the daemon for you.

---

## Environment variables

The common ones:

| Variable | Used for |
|---|---|
| `MXC_HOME` | Where mxconsole keeps its state (default `~/.mxc`). |
| `MXC_PORT` | The daemon's port (default 11435). |
| `MXC_LITELLM_PORT` | LiteLLM's port when exposed (default 4000). |
| `MXC_EXPOSE_PORT` | The HTTPS port when exposed (default 8443). |
| `MXC_API_KEY` | Your key, for the notebook's "Try it" cells and the snippets `mxc serve` prints. |
| `MXC_BASE_URL` | The endpoint the notebook calls, if not the local one. |
| `HF_TOKEN` | Gated Hugging Face models (Llama, for example) and higher download rate limits. |
| `MXC_CLAUDE` | Path to Claude Code for `mxc port --auto`, if `claude` isn't on your PATH. |

The old `HOTFIRE_*` and `MST_*` names still work, and so do the `hotfire` and `mst` commands.
