#!/usr/bin/env bash
# Benchmark the local MAX endpoint.

set -euo pipefail
cd "$(dirname "$0")/.."

export PATH="$HOME/.pixi/bin:$PATH"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"

MODEL="${MODEL:-Qwen/Qwen2.5-3B-Instruct}"
NUM_PROMPTS="${NUM_PROMPTS:-50}"
MAX_CONCURRENCY="${MAX_CONCURRENCY:-8}"
RESULT_FILE="${RESULT_FILE:-results/benchmark-$(date +%Y%m%d-%H%M%S).json}"

mkdir -p results

echo "Benchmarking $MODEL ($NUM_PROMPTS prompts, concurrency $MAX_CONCURRENCY)"
exec pixi run max benchmark \
  --model "$MODEL" \
  --backend modular \
  --endpoint /v1/chat/completions \
  --dataset-name sonnet \
  --num-prompts "$NUM_PROMPTS" \
  --sonnet-input-len 256 \
  --output-lengths 128 \
  --max-concurrency "$MAX_CONCURRENCY" \
  --result-filename "$RESULT_FILE"
