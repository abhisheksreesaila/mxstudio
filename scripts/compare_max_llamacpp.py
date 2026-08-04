#!/usr/bin/env python3
"""Side-by-side throughput: MAX (OpenAI API) vs llama.cpp (llama-bench or llama-cli).

Example:
  # MAX must already be serving the same model family on :8100
  pixi run python scripts/compare_max_llamacpp.py \\
      --gguf ~/models/gguf/qwen2.5-0.5b-instruct-q8_0.gguf
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path

from openai import OpenAI

DEFAULT_PROMPT = (
    "Explain the significance of the transformer architecture in machine "
    "learning. Cover attention, scaling laws, and two real-world applications."
)


def bench_max(base_url: str, model: str, max_tokens: int, runs: int) -> dict:
    client = OpenAI(base_url=base_url, api_key="EMPTY", timeout=300)
    # warmup
    _stream_once(client, model, max_tokens=16)
    results = []
    for _ in range(runs):
        results.append(_stream_once(client, model, max_tokens=max_tokens))
    return {
        "engine": "max",
        "model": model,
        "runs": results,
        "mean_ttft_s": round(sum(r["ttft_s"] for r in results) / len(results), 3),
        "mean_tok_per_s": round(
            sum(r["tok_per_s"] for r in results) / len(results), 1
        ),
    }


def _stream_once(client: OpenAI, model: str, max_tokens: int) -> dict:
    t0 = time.perf_counter()
    first = None
    tokens = 0
    stream = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": DEFAULT_PROMPT}],
        max_tokens=max_tokens,
        temperature=0.0,
        stream=True,
    )
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
            if first is None:
                first = time.perf_counter()
            tokens += 1
    end = time.perf_counter()
    if first is None:
        raise RuntimeError("MAX returned no tokens")
    gen = end - first
    return {
        "ttft_s": round(first - t0, 3),
        "tokens": tokens,
        "tok_per_s": round(tokens / gen, 1) if gen > 0 else None,
    }


def bench_llamacpp(llama_bench: Path, gguf: Path, ngl: int, prompt_tokens: int, gen_tokens: int) -> dict:
    cmd = [
        str(llama_bench),
        "-m",
        str(gguf),
        "-ngl",
        str(ngl),
        "-p",
        str(prompt_tokens),
        "-n",
        str(gen_tokens),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    text = proc.stdout + "\n" + proc.stderr
    # llama-bench prints a table; pull pp/tg tok/s lines
    # Example columns vary; look for "tg128" or "eval time"
    tg = None
    pp = None
    for line in text.splitlines():
        # Table cells may be spaced: "tg128" ... "450.23"
        m_tg = re.search(r"tg\d+\s*\|\s*([\d.]+)", line) or re.search(
            r"tg\d+\s+([\d.]+)", line
        )
        m_pp = re.search(r"pp\d+\s*\|\s*([\d.]+)", line) or re.search(
            r"pp\d+\s+([\d.]+)", line
        )
        if m_tg:
            tg = float(m_tg.group(1))
        if m_pp:
            pp = float(m_pp.group(1))
        m = re.search(r"([\d.]+)\s*tokens per second", line)
        if m and "prompt" not in line.lower():
            tg = float(m.group(1))

    return {
        "engine": "llama.cpp",
        "gguf": str(gguf),
        "raw_tail": "\n".join(text.splitlines()[-40:]),
        "prompt_tok_per_s": pp,
        "gen_tok_per_s": tg,
        "returncode": proc.returncode,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default="http://127.0.0.1:8100/v1")
    ap.add_argument("--model", default="Qwen/Qwen2.5-0.5B-Instruct")
    ap.add_argument("--gguf", type=Path, required=True)
    ap.add_argument(
        "--llama-bench",
        type=Path,
        default=Path.home() / "llama.cpp" / "build" / "bin" / "llama-bench",
    )
    ap.add_argument("--max-tokens", type=int, default=128)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--ngl", type=int, default=99)
    ap.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "logs" / "max_vs_llamacpp.json",
    )
    args = ap.parse_args()

    if not args.gguf.is_file():
        raise SystemExit(f"GGUF not found: {args.gguf}")
    if not args.llama_bench.is_file():
        raise SystemExit(f"llama-bench not found: {args.llama_bench}")

    print("=== MAX ===")
    max_res = bench_max(args.base_url, args.model, args.max_tokens, args.runs)
    print(
        f"mean TTFT {max_res['mean_ttft_s']}s · mean {max_res['mean_tok_per_s']} tok/s"
    )

    print("\n=== llama.cpp (llama-bench) ===")
    llama_res = bench_llamacpp(
        args.llama_bench, args.gguf, args.ngl, prompt_tokens=128, gen_tokens=args.max_tokens
    )
    print(llama_res.get("raw_tail", ""))
    print(
        f"parsed: prompt={llama_res.get('prompt_tok_per_s')} tok/s · "
        f"gen={llama_res.get('gen_tok_per_s')} tok/s"
    )

    out = {
        "prompt": DEFAULT_PROMPT,
        "max": max_res,
        "llamacpp": llama_res,
        "notes": [
            "MAX typically serves BF16/FP16 weights; GGUF Q8_0 is quantized — not bit-identical.",
            "Compare directionally (same model family, similar decode length).",
            "For closer parity, use an F16 GGUF.",
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2))
    print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
