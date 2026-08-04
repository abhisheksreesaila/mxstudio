#!/usr/bin/env bash
set -euo pipefail
export PATH="/home/ablearn/.pixi/bin:$PATH"
pkill -f 'mxstudio.cli ui' 2>/dev/null || true
pkill -f 'uvicorn.*mxstudio' 2>/dev/null || true
sleep 2
cd /home/ablearn/unifiedram/mxstudio
nohup pixi run studio > /home/ablearn/studio_boot.log 2>&1 &
sleep 6
echo -n "HTTP "
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/ || echo fail
echo -n "ENV "
curl -s http://127.0.0.1:8080/api/env | head -c 400 || echo fail
echo
pgrep -af 'mxstudio.cli ui' || echo 'no ui process'
