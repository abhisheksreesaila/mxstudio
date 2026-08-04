#!/usr/bin/env bash
# Start MAX serve with Qwen 2.5 3B on port 8000.
# WSL note: --no-device-graph-capture avoids CUDA graph capture issues on WSL.

set -euo pipefail
cd "$(dirname "$0")/.."

export PATH="$HOME/.pixi/bin:$PATH"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"

source ~/.bashrc 2>/dev/null || true
source ~/.profile 2>/dev/null || true

MODEL="${MODEL:-Qwen/Qwen2.5-3B-Instruct}"
PORT="${PORT:-8000}"

echo "Starting MAX serve: $MODEL on port $PORT (GPU $CUDA_VISIBLE_DEVICES)"
exec pixi run max serve \
  --model "$MODEL" \
  --port "$PORT" \
  --no-device-graph-capture
