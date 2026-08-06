#!/usr/bin/env bash
# Serve OLMoE via custom MAX architecture port.

set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"
export PATH="$HOME/.pixi/bin:$PATH"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"

source ~/.bashrc 2>/dev/null || true

PORT_DIR="$PROJECT_ROOT/ports/olmoe_1b_7b_0125_instruct"
MODEL="${MODEL:-allenai/OLMoE-1B-7B-0125-Instruct}"

if [[ ! -d "$PORT_DIR" ]]; then
  echo "Custom architecture not found: $PORT_DIR" >&2
  exit 1
fi

exec pixi run max serve \
  --model "$MODEL" \
  --custom-architectures "$PORT_DIR" \
  --port "${PORT:-8002}" \
  --max-batch-size "${MAX_BATCH_SIZE:-1}" \
  --max-batch-input-tokens "${MAX_BATCH_INPUT_TOKENS:-256}" \
  --no-device-graph-capture
