#!/usr/bin/env bash
# Chat with MAX on port 8000.

set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.pixi/bin:$PATH"

exec pixi run python scripts/chat.py "$@"
