"""Throughput benchmark via Modular's ``max benchmark`` CLI.

Runs against the already-serving MAX endpoint (default port 8100). Falls back
to a short OpenAI-stream probe only if ``max benchmark`` cannot be launched.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from statistics import mean

from notebooks.mxstudio import hwinfo
from openai import OpenAI

from notebooks.mxstudio import state
from notebooks.mxstudio.servers import MODEL_PORT, PROJECT_ROOT

DEFAULT_PROMPT = (
    "Explain the significance of the transformer architecture in machine "
    "learning. Cover attention, scaling laws, and two real-world applications."
)

RESULTS_DIR = Path.home() / ".mxstudio" / "bench_results"


def _max_benchmark_cmd(
    model_id: str,
    *,
    num_prompts: int,
    max_concurrency: int,
    output_len: int,
    result_file: Path,
    env: dict[str, str] | None = None,
) -> list[str]:
    search_path = (env or os.environ).get("PATH")
    pixi = shutil.which("pixi", path=search_path)
    max_cli = shutil.which("max", path=search_path)
    if not pixi and not max_cli:
        raise FileNotFoundError("Modular max benchmark CLI was not found")
    base = [pixi, "run", "max", "benchmark"] if pixi else [max_cli, "benchmark"]
    return [
        *base,
        "--model",
        model_id,
        "--backend",
        "modular",
        "--endpoint",
        "/v1/chat/completions",
        "--host",
        "127.0.0.1",
        "--port",
        str(MODEL_PORT),
        "--dataset-name",
        "sonnet",
        "--num-prompts",
        str(num_prompts),
        "--sonnet-input-len",
        "256",
        "--max-output-len",
        str(output_len),
        "--max-concurrency",
        str(max_concurrency),
        "--result-filename",
        str(result_file),
        "--no-collect-gpu-stats",
    ]


def _benchmark_env() -> dict[str, str]:
    env = os.environ.copy()
    pixi_bin = str(PROJECT_ROOT / ".pixi" / "envs" / "default" / "bin")
    wsl_nvidia = "/usr/lib/wsl/lib"
    paths = [pixi_bin]
    if Path(wsl_nvidia).is_dir():
        paths.append(wsl_nvidia)
    paths.append(env.get("PATH", ""))
    env["PATH"] = os.pathsep.join(p for p in paths if p)
    return env


def _has_max_benchmark(env: dict[str, str]) -> bool:
    path = env.get("PATH")
    return bool(shutil.which("pixi", path=path) or shutil.which("max", path=path))


def _summarize_max_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    # max benchmark JSON shape varies slightly across versions — be tolerant.
    def pick(*keys, default=None):
        for k in keys:
            if k in data and data[k] is not None:
                return data[k]
            # nested common wrappers
            for nest in ("results", "metrics", "summary"):
                block = data.get(nest)
                if isinstance(block, dict) and block.get(k) is not None:
                    return block[k]
        return default

    tok_s = pick(
        "output_throughput",
        "output_token_throughput",
        "mean_output_throughput",
        "tokens_per_second",
    )
    ttft = pick(
        "mean_ttft_ms",
        "ttft_ms",
        "mean_time_to_first_token_ms",
    )
    if ttft is not None and ttft > 20:  # likely milliseconds
        ttft_s = round(float(ttft) / 1000.0, 3)
    elif ttft is not None:
        ttft_s = round(float(ttft), 3)
    else:
        ttft_s = None

    return {
        "mean_tok_per_s": round(float(tok_s), 1) if tok_s is not None else None,
        "mean_ttft_s": ttft_s,
        "raw_keys": sorted(data.keys())[:40],
        "result_file": str(path),
        "backend": "max_benchmark",
    }


def _fallback_stream_runs(model_id: str, runs: int, max_tokens: int) -> dict:
    """Lightweight OpenAI-stream fallback if max benchmark is unavailable."""
    client = OpenAI(
        base_url=f"http://127.0.0.1:{MODEL_PORT}/v1",
        api_key="EMPTY",
        timeout=300,
    )

    def one() -> dict:
        t0 = time.perf_counter()
        first: float | None = None
        tokens = 0
        stream = client.chat.completions.create(
            model=model_id,
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
            raise RuntimeError("no tokens received from server")
        gen = end - first
        return {
            "ttft_s": round(first - t0, 3),
            "tokens": tokens,
            "gen_s": round(gen, 2),
            "tok_per_s": round(tokens / gen, 1) if gen > 0 else None,
            "total_s": round(end - t0, 2),
        }

    one()  # warmup
    run_list = [one() for _ in range(runs)]
    return {
        "runs": run_list,
        "summary": {
            "mean_ttft_s": round(mean(r["ttft_s"] for r in run_list), 3),
            "mean_tok_per_s": round(mean(r["tok_per_s"] for r in run_list), 1),
            "max_tokens": max_tokens,
            "backend": "openai_stream_fallback",
        },
    }


class BenchManager:
    def __init__(self) -> None:
        self.job: dict = {"state": "idle"}
        self._lock = threading.Lock()

    def start(
        self,
        model_id: str,
        runs: int = 3,
        max_tokens: int = 128,
        num_prompts: int | None = None,
        max_concurrency: int = 4,
        use_max_benchmark: bool = True,
    ) -> dict:
        with self._lock:
            if self.job.get("state") == "running":
                raise RuntimeError("a benchmark is already running")
            prompts = num_prompts if num_prompts is not None else max(runs * 4, 8)
            self.job = {
                "state": "running",
                "model_id": model_id,
                "runs_total": runs,
                "runs": [],
                "error": None,
                "backend": "max_benchmark" if use_max_benchmark else "openai_stream_fallback",
                "num_prompts": prompts,
                "max_concurrency": max_concurrency,
                "max_tokens": max_tokens,
                "log_tail": "",
            }

        def _worker() -> None:
            try:
                env = _benchmark_env()
                if use_max_benchmark and _has_max_benchmark(env):
                    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
                    stamp = time.strftime("%Y%m%d-%H%M%S")
                    result_file = RESULTS_DIR / f"max-bench-{stamp}.json"
                    cmd = _max_benchmark_cmd(
                        model_id,
                        num_prompts=self.job["num_prompts"],
                        max_concurrency=max_concurrency,
                        output_len=max_tokens,
                        result_file=result_file,
                        env=env,
                    )
                    self.job["command"] = " ".join(cmd)
                    proc = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        timeout=3600,
                        check=False,
                        cwd=str(PROJECT_ROOT),
                        env=env,
                    )
                    self.job["log_tail"] = (proc.stdout or "")[-2000:] + (
                        "\n" + (proc.stderr or "")[-1000:]
                    )
                    if proc.returncode != 0 or not result_file.is_file():
                        # Fall back so the UI still returns a number.
                        fb = _fallback_stream_runs(model_id, runs, max_tokens)
                        self.job["runs"] = fb["runs"]
                        self.job["summary"] = {
                            **fb["summary"],
                            "note": "max benchmark failed; used stream fallback",
                            "max_benchmark_exit": proc.returncode,
                            "degraded": True,
                        }
                        self.job["backend"] = "openai_stream_fallback"
                    else:
                        summary = _summarize_max_json(result_file)
                        summary["max_tokens"] = max_tokens
                        summary["num_prompts"] = self.job["num_prompts"]
                        summary["max_concurrency"] = max_concurrency
                        self.job["summary"] = summary
                        # Synthesize a single "run" row for the table UI.
                        self.job["runs"] = [
                            {
                                "ttft_s": summary.get("mean_ttft_s") or 0,
                                "tokens": max_tokens,
                                "tok_per_s": summary.get("mean_tok_per_s"),
                            }
                        ]
                        self.job["backend"] = "max_benchmark"
                else:
                    fb = _fallback_stream_runs(model_id, runs, max_tokens)
                    self.job["runs"] = fb["runs"]
                    self.job["summary"] = {
                        **fb["summary"],
                        "note": (
                            "max benchmark CLI unavailable; used stream fallback"
                            if use_max_benchmark
                            else "stream timing requested"
                        ),
                        "degraded": bool(use_max_benchmark),
                    }
                    self.job["backend"] = "openai_stream_fallback"

                self.job["state"] = "done"
                gpus = hwinfo.gpus()
                history = state.load().get("bench_history", [])
                history.append(
                    {
                        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "model_id": model_id,
                        "summary": self.job["summary"],
                        "gpu": gpus[0]["name"] if gpus else "unknown",
                        "backend": self.job.get("backend"),
                    }
                )
                state.update(bench_history=history[-50:])
            except Exception as exc:
                self.job["state"] = "error"
                self.job["error"] = str(exc)[:500]

        threading.Thread(target=_worker, daemon=True, name="bench").start()
        return self.job

    def status(self) -> dict:
        return self.job

    @staticmethod
    def history() -> list[dict]:
        return state.load().get("bench_history", [])


MANAGER = BenchManager()
