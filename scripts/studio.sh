#!/usr/bin/env bash
# Launch the mxstudio web UI (http://localhost:8080).

set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.pixi/bin:$PATH"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"

exec pixi run python -m mxstudio.cli ui --port "${PORT:-8080}"
