#!/usr/bin/env python3
"""F-011 live acceptance: three small models behind one keyed HTTPS endpoint.

Run on the real machine with memory free:  pixi run python scripts/mst_acceptance.py
It pulls (if needed) and serves the models, exposes them through Caddy + LiteLLM,
then checks chat, streaming, concurrent use, idle unload and reload over HTTPS.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
import urllib.request

import httpx
from openai import AsyncOpenAI

from mst import expose
from mst.settings import EXPOSE_PORT, daemon_url

MODELS = ["qwen2.5-0.5b", "smollm2-1.7b", "qwen2.5-1.5b"]
IDLE_MODEL = "qwen2.5-0.5b"  # gets a short keep-alive so we can watch it unload and come back


def mst(*args: str) -> str:
    print(f"$ mst {' '.join(args)}", flush=True)
    result = subprocess.run(["mst", *args], capture_output=True, text=True)
    print(result.stdout.rstrip())
    if result.returncode != 0:
        print(result.stderr.rstrip(), file=sys.stderr)
        raise SystemExit(f"FAIL: mst {' '.join(args)} exited {result.returncode}")
    return result.stdout


def model_state(name: str) -> str:
    req = urllib.request.Request(f"{daemon_url()}/admin/status", headers={"X-Mst-Admin": "1"})
    with urllib.request.urlopen(req) as r:
        return {m["name"]: m["state"] for m in json.load(r)}.get(name, "missing")


async def chat(client: AsyncOpenAI, model: str) -> tuple[str, float]:
    start = time.monotonic()
    r = await client.chat.completions.create(
        model=model, max_tokens=24, messages=[{"role": "user", "content": "Say hello in five words."}])
    return r.choices[0].message.content or "", time.monotonic() - start


async def stream(client: AsyncOpenAI, model: str) -> int:
    chunks = 0
    s = await client.chat.completions.create(
        model=model, max_tokens=24, stream=True, messages=[{"role": "user", "content": "Count to five."}])
    async for chunk in s:
        if chunk.choices and chunk.choices[0].delta.content:
            chunks += 1
    return chunks


async def run(host: str, idle_wait: int) -> None:
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, detail))
        print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}", flush=True)

    for m in MODELS:
        mst("pull", m)
    mst("serve", MODELS[1], "--pin", "--no-wait")
    mst("serve", MODELS[2], "--no-wait")
    mst("serve", IDLE_MODEL, "--keep-alive", "60s", "--no-wait")
    out = mst("keys", "new", "acceptance", "--rotate")
    key = next(tok for tok in out.split() if tok.startswith("mst-"))
    mst("expose", "--host", host)
    await asyncio.sleep(15)  # LiteLLM boot

    base = f"https://{host}:{EXPOSE_PORT}/v1"
    http = httpx.AsyncClient(verify=str(expose.ca_root_path()), timeout=None)
    client = AsyncOpenAI(base_url=base, api_key=key, http_client=http)

    bad = await httpx.AsyncClient(verify=str(expose.ca_root_path())).get(
        f"{base}/models", headers={"Authorization": "Bearer mst-wrong"})
    check("bad key is refused over HTTPS", bad.status_code == 401, f"status {bad.status_code}")

    for m in MODELS:
        try:
            text, secs = await chat(client, m)
            check(f"chat {m}", bool(text.strip()), f"{secs:.1f}s · {text.strip()[:40]!r}")
            check(f"stream {m}", await stream(client, m) > 1)
        except Exception as e:  # report and continue so one model doesn't hide the others
            check(f"chat {m}", False, repr(e)[:200])

    replies = await asyncio.gather(*(chat(client, m) for m in MODELS), return_exceptions=True)
    check("all three answer at once", all(not isinstance(r, Exception) and r[0] for r in replies))

    print(f"waiting {idle_wait}s for {IDLE_MODEL} to unload…", flush=True)
    await asyncio.sleep(idle_wait)
    check(f"{IDLE_MODEL} unloads when idle", model_state(IDLE_MODEL) == "cold", model_state(IDLE_MODEL))
    check(f"{MODELS[1]} stays loaded (pinned)", model_state(MODELS[1]) == "ready", model_state(MODELS[1]))
    text, secs = await chat(client, IDLE_MODEL)
    check(f"{IDLE_MODEL} comes back on the next request", bool(text.strip()), f"cold start {secs:.1f}s")

    print("\nFrom a second device on the LAN (copy root.crt there first):")
    print(f"  curl --cacert root.crt {base}/chat/completions -H 'Authorization: Bearer {key[:12]}…' \\")
    print(f"    -H 'Content-Type: application/json' -d '{{\"model\":\"{MODELS[1]}\",\"messages\":[{{\"role\":\"user\",\"content\":\"hi\"}}]}}'")
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    raise SystemExit(1 if failed else 0)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default=expose.default_host())
    p.add_argument("--idle-wait", type=int, default=100, help="seconds to wait for the idle unload")
    args = p.parse_args()
    asyncio.run(run(args.host, args.idle_wait))


if __name__ == "__main__":
    main()
