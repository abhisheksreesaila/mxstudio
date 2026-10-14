# Verify it on real hardware

[README](../README.md) · [User guide](USER_GUIDE.md)

Everything below has been built and reviewed, and the tests pass against a **fake** `max`. Steps 3 to 6
are the first runs on real MAX and real hardware. Tick them off in order; each says what you
should see.

### 0. Free memory
Stop the other agents. Three small models need about 8 GB of GPU memory in total (you have
2× RTX 3090), plus some system RAM while they load.

### 1. Retire the old Nucleus services (needs your password)
The legacy `mxs` stack still runs as two systemd services on ports 443 and 8080:

```bash
sudo systemctl disable --now mxstudio-nucleus-caddy.service mxstudio-nucleus.service
sudo rm /etc/systemd/system/mxstudio-nucleus.service /etc/systemd/system/mxstudio-nucleus-caddy.service
sudo systemctl daemon-reload
sudo rm -r /etc/mxstudio        # old deployment env files, Caddyfile, install manifests
```
✅ `systemctl status mxstudio-nucleus` says *could not be found*, and `ss -ltn | grep ':443 '` is empty.

### 2. Tests (no GPU)
```bash
pixi run pytest tests/mxconsole -q
```
✅ Every test passes (`N passed`, no failures).

### 3. One model, local endpoint
```bash
pixi run mxc serve qwen2.5-0.5b
```
✅ You see `pulled …`, `started the mxconsole daemon`, `loading …`, `qwen2.5-0.5b is ready (Ns)`, then the
endpoint `http://127.0.0.1:11435/v1`, a key `mst-…` (**save it**: `export MXC_API_KEY=mst-…`), and
a curl and a Python snippet. Paste the curl and expect a JSON reply.
✅ Open http://127.0.0.1:11435/docs and you should land on the console's API reference.
✅ Open http://127.0.0.1:11435/console: the console lists your models. Click one to open its
panel: Overview, Security, Benchmark, Logs and Notebook ([what each screen shows](site/console.html)).
❌ If loading fails: the error includes the tail of `~/.mxc/logs/qwen2.5-0.5b.log`.

### 4. Several models, on demand
```bash
pixi run mxc serve smollm2-1.7b --pin
pixi run mxc serve qwen2.5-1.5b --keep-alive 2m
pixi run mxc ls
```
✅ `ls` shows all three as `ready`. Wait 2–3 minutes, run `mxc ls` again, and `qwen2.5-1.5b`
is `cold`, the others aren't. Call it again (curl with `"model": "qwen2.5-1.5b"`) and it comes
back after a cold start.

### 5. Secure LAN endpoint
```bash
pixi run mxc expose
```
✅ It prints `https://<your-host>.local:8443/v1` and the path to Caddy's `root.crt`. Then:
```bash
curl --cacert ~/.local/share/caddy/pki/authorities/local/root.crt \
  https://$(hostname).local:8443/v1/models -H "Authorization: Bearer $MXC_API_KEY"
```
✅ This lists your models. With a wrong key you get `401`, and
`https://…:8443/model/info` gives `404`, because LiteLLM's admin API is never exposed.
✅ LiteLLM's own UI is at http://127.0.0.1:4000/ui (this machine only).

### 6. The whole acceptance run in one script
```bash
pixi run python scripts/mxc_acceptance.py
```
✅ It ends with `N/N checks passed`. That covers chat and streaming on all three models over HTTPS, all three at
once, a bad key refused, idle unload and reload, and a pinned model staying up. It prints a curl line for
a **second device**: copy `root.crt` there, run the line, and you get a reply. That proves the LAN path.

### 7. Learn and port
```bash
pixi run mxc learn qwen2.5-0.5b          # opens Jupyter Lab; run the "Try it" cells with MXC_API_KEY set
pixi run mxc port openai-community/gpt2 --auto   # ports, checks against Hugging Face, registers and serves (~10 min)
```
✅ The notebook shows the model card, a config table, a parameter breakdown (estimate ≈ actual)
and MAX's source folder for the architecture. `port --auto` ends with "Ported … and serving it"; see
[docs/PORTING.md](docs/PORTING.md) for what it needs, what can come back, and the limits.

### When you're done
```bash
pixi run mxc expose --stop && pixi run mxc stop --all
```

