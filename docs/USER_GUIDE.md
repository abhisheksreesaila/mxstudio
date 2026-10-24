# omxconsole user guide

[README](../README.md) · [Command reference](CLI.md) · [Porting](PORTING.md)

## What it is

omxconsole (`mxc`) serves small models on Modular MAX behind one OpenAI-compatible endpoint on your machine.
It loads a model on its first request, unloads it when idle, and can put the endpoint on your LAN over HTTPS with one key per app.
It also writes a notebook for each model, so you can see how it's built.

This guide covers the omxconsole setup. The older MX Studio / Nucleus guide (`mxs` commands) is in [LEGACY_NUCLEUS.md](LEGACY_NUCLEUS.md).

## Requirements

- Linux x86-64. That's the only platform it's tested on.
- An NVIDIA GPU with a working driver: `nvidia-smi` should work. omxconsole reads GPU memory from it.
- [Pixi](https://pixi.sh/). MAX, Jupyter Lab and the rest go into the project's own environment; your system Python isn't touched.
- Git, and internet access for the first install and model downloads.

Only for some features:

- **LAN access:** [Caddy](https://caddyserver.com/) on your PATH. It isn't installed by pixi.
- **Gated models** (Llama, for example): a Hugging Face token in `HF_TOKEN`.
- **Porting:** Claude Code, signed in, and bubblewrap (`bwrap`). See [PORTING.md](PORTING.md).

## Install

1. Install pixi, then open a new shell:

   ```bash
   curl -fsSL https://pixi.sh/install.sh | sh
   ```

2. Clone the repo and install the environment:

   ```bash
   git clone https://github.com/abhisheksreesaila/mxstudio.git
   cd mxstudio
   pixi install
   ```

3. If you'll put the endpoint on your LAN, also install the LiteLLM environment:

   ```bash
   pixi install -e gateway
   ```

Run every command below from the repo, as `pixi run mxc …`.

## 🚀 First ten minutes

Model weights aren't bundled. Start with `qwen2.5-0.5b` (Qwen2.5 0.5B Instruct): about 1 GB, ungated, quick to load.

### In the CLI

```bash
pixi run mxc serve qwen2.5-0.5b
```

This downloads the model if needed, starts the omxconsole daemon in the background, loads the model and prints:

- the endpoint, `http://127.0.0.1:11435/v1`;
- the first time only, a key `mst-…` for the app `default`. It's shown once. Save it:

  ```bash
  export MXC_API_KEY=mst-…
  ```

- a curl command and a Python snippet that call the model.

Paste the curl and you get a JSON reply. Then:

```bash
pixi run mxc ls                 # your models and their state: cold, starting, ready, crashed
pixi run mxc status             # daemon, endpoints, models, keys and MAX version on one screen
pixi run mxc stop qwen2.5-0.5b  # unload it now
pixi run mxc stop --all         # unload everything and stop the daemon
```

Short names: `qwen2.5-0.5b`, `qwen2.5-1.5b`, `qwen2.5-3b`, `smollm2-1.7b`, `all-minilm-l6` and `llama3.2-1b` (gated). Any Hugging Face repo id works too.

### In the console

```bash
pixi run mxc console    # opens http://127.0.0.1:11435/console in your browser
pixi run mxc app        # or: the console in its own window
```

Both start the daemon if it isn't running. Closing the window stops nothing.

1. On **My models**, click **Add a model**.
2. Search for `qwen2.5-0.5b` and pick it. You watch it go through Check hardware, Fetch the model, Compile graph, Serve on localhost and Smoke test, ending on a Ready card.
3. Click the model in **My models**. Its panel opens beside the list, with a curl command under **Call it**.

The console works only on this machine. It isn't exposed on the LAN, even after you make the endpoint secure.

## 🧭 A tour of the console

Panels (blades) open side by side from left to right; older ones fold into rails on the left. The breadcrumb island at the top shows the parent and the open blade; click the **omx** logo to go back to My models.

| | |
|---|---|
| ![A model's overview: live throughput, endpoint, a ready-to-paste curl](assets/screens/model.png) | ![Add a model: search MAX and Hugging Face, checked against your GPU memory](assets/screens/add.png) |
| **A model:** live numbers, its endpoint and a `curl` to paste | **Add a model:** search MAX's catalog and Hugging Face; every result is checked against your GPU memory |
| ![The notebook, running in Jupyter inside the console](assets/screens/jupyter.png) | ![A model MAX can't run yet, with Port it](assets/screens/port-offer.png) |
| **Notebook:** how the model is built; runs in Jupyter in the page | **Needs porting:** one click hands it to the porting agent |

### My models

Everything this machine serves, as a list or as cards (the choice is remembered in this browser). A filter box narrows it by name.

- **State:** Running, Starting (with "compiling" and the time so far), Idle or Crashed. A pinned model says "pinned"; a running one shows how long it has been idle out of its keep-alive.
- **Access:** **Local** (localhost only) or **Secure** (HTTPS on your LAN).
- **Tokens/s:** live decode speed, with a sparkline. Also first-token time, requests in the last 5 minutes, GPU memory and architecture.

Above the list: how many models are serving, total throughput, requests in the last 5 minutes, and a GPU memory bar split by model. A crashed model gets a banner with **View logs** and **Restart**.

### The model panel

The left menu has **Overview**, **Security**, **Benchmark**, **Logs** and **Notebook**. The command bar on top changes with the state:

- **Start** when it's idle, **Stop** and **Restart** when it's running, **Restart** when it crashed;
- **Make it secure** (until the endpoint is on your LAN), **Run benchmark**, **Open notebook**, **Logs**, **Copy endpoint**;
- **Remove**, which asks for a second click. It's the same as `mxc rm`: the weights stay.

Hover a button to see the `mxc` command that does the same thing.

**Overview** shows:

- **Essentials:** repository, architecture, source, when it unloads, endpoint, access, GPU memory and state.
- **Tokens · live:** throughput, first-token time and requests, with a 5-minute chart.
- **Call it:** the endpoint URL and a curl command. The key appears as `$MXC_API_KEY`, never the real key.
- **Security** and **Hardware fit:** this model's GPU memory next to the other models and what's free.

The other tabs:

- **Security:** see [Securing it on your LAN](#-securing-it-on-your-lan).
- **Benchmark:** decode tokens/s and time to first token for the models in the latest run, with this model outlined, and past runs. Same as `mxc bench`.
- **Logs:** the model's server log, following new lines, with a filter and copy. Same as `mxc logs <model> -f`.
- **Notebook:** see [Notebooks](#-notebooks).

### Add a model

One search box over the short names, MAX's catalog and Hugging Face. You can paste a repo id (`owner/name`). Each result has a label for this machine:

| Label | Meaning |
|---|---|
| **fits** | MAX supports it and it fits in free GPU memory. |
| **tight fit** | It fits, but uses more than 80% of the free memory. |
| **too big** | It needs more memory than is free. You see how much it needs and smaller models that fit. |
| **needs porting** | MAX doesn't support the architecture yet. You get a **Port it** button. |

The filter above the results shows All, Fits or Needs porting. The same labels in the terminal: `mxc search <query>` says `ok`, `tight`, `no` or `port`.

**Port it** runs `mxc port <repo> --auto`. See [Models MAX can't run yet](#-models-max-cant-run-yet).

### The top islands

- **The Beacon** (top right): what is happening now, in one pill: a pull or port in progress, a crashed model, a model starting, or the running models with live tokens per second. With nothing running it shows the GPU and memory in use. Tap it for the details and a shortcut; cancelling a pull or port from it takes a second tap.
- **API keys:** every app key (prefix only), with **New key**, **Rotate** and **Revoke**. A new key is shown once.
- **Gateway:** LiteLLM's status, the route for each model, the LiteLLM log, safeguard counts for today, recent requests (kept 7 days) and a link to the API reference at `/docs`. **Restart** restarts Caddy and LiteLLM and rotates their internal key.
- **Settings:** the theme, when each model unloads, the daemon's info with **Stop daemon**, and every port and process omxconsole owns.
- **Theme:** the moon (or sun) button switches light and dark. Until you pick one, the console follows your system.

## 🔌 Calling the endpoint from your apps

The endpoint is OpenAI-compatible:

- Base URL: `http://127.0.0.1:11435/v1`
- Auth: `Authorization: Bearer <key>`
- Model: the name `mxc ls` shows, for example `qwen2.5-0.5b`
- Routes: `/v1/models`, `/v1/chat/completions`, `/v1/completions` and `/v1/embeddings`. Anything else gets a 404.

Give each app its own key. It's printed once:

```bash
pixi run mxc keys new my-app
pixi run mxc keys ls              # lists keys, never the secret
pixi run mxc keys rotate my-app   # new key, old one stops working
pixi run mxc keys revoke my-app
```

curl:

```bash
curl http://127.0.0.1:11435/v1/chat/completions \
  -H "Authorization: Bearer $MXC_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "qwen2.5-0.5b", "messages": [{"role": "user", "content": "Hello"}]}'
```

Python, with the `openai` package:

```python
import os
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:11435/v1", api_key=os.environ["MXC_API_KEY"])
reply = client.chat.completions.create(
    model="qwen2.5-0.5b",
    messages=[{"role": "user", "content": "Hello"}],
)
print(reply.choices[0].message.content)
```

A request without a valid key gets a 401. A model that isn't registered gets a 404 that tells you to run `mxc pull`.

### Idle unload and pin

You can register many models without filling GPU memory. A model loads on its first request; callers that arrive while it loads wait for the same start. It unloads after 5 minutes without a request, never in the middle of one. The first call after an unload waits for the model to load again.

```bash
pixi run mxc serve smollm2-1.7b --pin              # never unloads
pixi run mxc serve qwen2.5-1.5b --keep-alive 10m   # unloads after 10 idle minutes
pixi run mxc set qwen2.5-0.5b --pin                # change it later (or --unpin, --keep-alive 1h)
```

The console's Settings panel lists each model's setting; change it with `mxc set`.

## 🔒 Securing it on your LAN

By default only this machine can call the endpoint. To let other devices on your network call it, click **Make it secure** in a model's Security panel, or run:

```bash
pixi run mxc expose
pixi run mxc expose --host box.local   # if other devices know this machine by another name
```

This applies to every model, not just one. It runs as your user, so no password is needed:

- **Caddy** serves HTTPS on port 8443 with its own certificate authority, and checks each request's app key.
- **LiteLLM** routes requests to the daemon by model name. It listens on `127.0.0.1:4000` only. `--no-litellm` skips it and Caddy goes straight to the daemon.

You get an address like `https://<host>.local:8443/v1`. Use it with the same keys and model names. Only the four `/v1` routes above are reachable; LiteLLM's admin API is not. Localhost keeps working over HTTP.

### Trust the certificate on other devices

Each device has to trust Caddy's CA once, or HTTPS calls fail with a certificate error. The file is:

```
~/.local/share/caddy/pki/authorities/local/root.crt
```

The Security panel shows the path and how to install it on Linux, macOS, Windows, iPhone and Android. On Linux, for example:

```bash
sudo cp root.crt /usr/local/share/ca-certificates/mxconsole.crt && sudo update-ca-certificates
```

To test without installing it, pass the file to curl:

```bash
curl --cacert root.crt https://<host>.local:8443/v1/models -H "Authorization: Bearer $MXC_API_KEY"
```

The other device must also resolve `<host>.local` to this machine. If it can't, use `--host` with a name or IP address it can reach.

### Turn it off

Click **Stop exposing** in the Security panel (it asks for a second click), or run:

```bash
pixi run mxc expose --stop
```

The Gateway panel shows the same command. It stops Caddy and LiteLLM; the local endpoint keeps running.

## 📓 Notebooks

Every model gets a Jupyter notebook built from its Hugging Face metadata: the model card, a config table, a breakdown of where the parameters are, sample code wired to your endpoint, and where MAX's Python implementation of the architecture lives. No LLM writes it.

```bash
pixi run mxc learn qwen2.5-0.5b            # writes the notebook and opens it in Jupyter Lab
pixi run mxc learn qwen2.5-0.5b --no-open  # only writes it
```

It works for a repo you haven't pulled, too. Notebooks are saved in `~/.mxc/notebooks/`.

In the console, open a model's **Notebook** tab. It shows the saved notebook: chapters on the left, cells and saved outputs in the middle, the architecture on the right. Nothing runs there.

To run it, click **Run in Jupyter**. Jupyter opens inside the panel in its single-notebook view, on this one notebook. **Saved view** switches back to the read-only view, and a link opens Jupyter in a new tab instead. Jupyter listens on `127.0.0.1` only, so this works only on this machine.

The "Try it" cells call your endpoint. From the console they just work: the daemon gives its Jupyter a key of
its own (`_notebook` in the activity log) and the endpoint's address. If the model is idle, the first cell loads it,
which can take a few seconds.

With `mxc learn` in a terminal, Jupyter starts in your shell, so export `MXC_API_KEY` there first (and
`MXC_BASE_URL` for an endpoint other than the local one).

## 🛠️ Models MAX can't run yet

MAX doesn't support every architecture. Search marks those "needs porting", and `mxc pull` stops before downloading the weights and points you to `mxc port`. **Port it** in Add a model, or `pixi run mxc port <hf-repo> --auto`, has Claude Code write a small MAX package for the model in a sandbox. omxconsole checks it against the Hugging Face model, then registers and serves it. It costs about $2–5 of Claude usage and takes 7–40 minutes per model. What it needs, what can come back and the limits: [PORTING.md](PORTING.md).

## 📁 Where things live

Everything is under `~/.mxc/` (set `MXC_HOME` to move it):

| Path | What |
|---|---|
| `mst.db` | registered models, key hashes, request log, benchmarks |
| `logs/` | one log per model (`<model>.log`) and `daemon.log` |
| `run/` | pid files, the daemon lock and the running Jupyter server's address |
| `notebooks/` | generated notebooks |
| `ports/` | porting workspaces and logs |
| `skills/` | Modular's porting skills |
| `expose/` | the Caddyfile, LiteLLM config and their logs |
| `webview/` | the `mxc app` window's saved preferences |

Model weights stay in the normal Hugging Face cache. Keys are stored only as SHA-256 hashes, which is why each one is shown once.

## 🩺 Troubleshooting

Start with:

```bash
pixi run mxc status   # is the daemon up, which models, LAN on or off
pixi run mxc ps       # every process omxconsole runs: daemon, Caddy, LiteLLM, each max serve
```

**The daemon isn't running.** The console says "omxconsole cannot reach the daemon", and CLI calls say "could not reach the omxconsole daemon". `mxc serve`, `mxc pull`, `mxc console` and `mxc app` start it for you. To run it in the foreground with logs on screen, use `pixi run mxc daemon`. If it won't start, the error shows the end of `~/.mxc/logs/daemon.log`.

**A model is stuck starting, or crashed.** The first load compiles the graph, which can take minutes; the state shows "compiling" and the time so far. omxconsole gives up after 10 minutes. If it crashed or failed, read its log:

```bash
pixi run mxc logs qwen2.5-0.5b -n 100
```

Or open the model's **Logs** tab. Requests to a model that fails to start get a 503 with the end of the log. Fix the cause, then **Restart** or `mxc serve <model>` again.

**Out of GPU memory, or too big.** Search marks models that won't fit as "too big" and suggests smaller ones. Each model gets a share of one GPU, so several fit side by side. If a load fails for memory, unload something (`mxc stop <model>`), unpin models you don't need (`mxc set <model> --unpin`), or pick a smaller model. Other programs using the GPU count too: `nvidia-smi` shows them.

**jupyter not found.** Jupyter Lab is part of the pixi environment, so this means it ran outside it. Add it with:

```bash
pixi add jupyterlab
```

Or open the notebook yourself: `pixi run jupyter lab ~/.mxc/notebooks/<model>.ipynb`.

**A gated model.** The error says the repo "is gated". Accept the license on huggingface.co, then set `HF_TOKEN`. Downloads run in the daemon, so it needs the token too: stop it, export the token and start again.

```bash
pixi run mxc stop --all
export HF_TOKEN=hf_…
pixi run mxc serve llama3.2-1b
```

**Port already in use.** Pick other ports with environment variables, and set them the same way for every `mxc` command:

| Variable | Default | What |
|---|---|---|
| `MXC_PORT` | 11435 | the daemon and its endpoint |
| `MXC_LITELLM_PORT` | 4000 | LiteLLM, when exposed |
| `MXC_EXPOSE_PORT` | 8443 | HTTPS on your LAN |

`mxc expose` names the port when LiteLLM's is taken. If the daemon's port is taken, the daemon doesn't start and `daemon.log` says why.

## Full command reference

Every command and option: [CLI.md](CLI.md).
