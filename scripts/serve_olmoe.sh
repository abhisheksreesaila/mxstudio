#!/usr/bin/env bash
# Serve OLMoE via custom MAX architecture port.

set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.pixi/bin:$PATH"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"

source ~/.bashrc 2>/dev/null || true

PORT_DIR="$(cd "$(dirname "$0")/../../ports/olmoe_1b_7b_0125_instruct" && pwd)"
MODEL="${MODEL:-allenai/OLMoE-1B-7B-0125-Instruct}"

exec pixi run max serve \
  --model-path "$MODEL" \
  --custom-architectures "$PORT_DIR" \
  --port "${PORT:-8002}" \
  --no-device-graph-capture
