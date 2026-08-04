#!/usr/bin/env bash
# Serve MAX-native Qwen3 MoE FP8 on both GPUs (streaming guinea pig).

set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.pixi/bin:$PATH"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0,1}"

source ~/.bashrc 2>/dev/null || true

MODEL="${MODEL:-Qwen/Qwen3-30B-A3B-Instruct-2507-FP8}"

echo "Serving $MODEL on GPUs $CUDA_VISIBLE_DEVICES port ${PORT:-8100}"
exec pixi run max serve \
  --model-path "$MODEL" \
  --port "${PORT:-8100}" \
  --devices gpu:0,1 \
  --ep-size 2 \
  --no-device-graph-capture
