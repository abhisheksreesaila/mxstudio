#!/bin/sh
# F-049: does MAX serve our small models on this Mac? Decides whether the Mac app (F-050, F-051) goes ahead.
#
# Run on an Apple-silicon Mac, from a clone of the repo:
#   git clone https://github.com/abhisheksreesaila/mxstudio.git && cd mxstudio && sh scripts/mac_check.sh
#
# It installs pixi if missing (into ~/.pixi, nothing system-wide), sets up the project, runs the test suite,
# then serves qwen2.5-0.5b two ways: plain `max serve` (does MAX work here?) and `mxc serve` (does omxconsole?).
# It stops only the processes it started. Everything goes to mac-check.log; send that file back.
# Takes 10-30 minutes, most of it the first download and the first model compile.

set -u
LOG="$(pwd)/mac-check.log"
PORT=18451
MODEL=Qwen/Qwen2.5-0.5B-Instruct
: > "$LOG"
say() { printf '\n== %s\n' "$*" | tee -a "$LOG"; }
run() { "$@" >> "$LOG" 2>&1; }

[ "$(uname -s)" = Darwin ] || { echo "Run this on a Mac."; exit 1; }
[ -f pixi.toml ] || { echo "Run this from the mxstudio folder."; exit 1; }

say "machine"
{ sysctl -n machdep.cpu.brand_string; sysctl -n hw.memsize | awk '{printf "%.0f GB memory\n", $1/1073741824}'
  sw_vers -productVersion; uname -m; } | tee -a "$LOG"

say "pixi"
if ! command -v pixi >/dev/null 2>&1 && [ ! -x "$HOME/.pixi/bin/pixi" ]; then
  curl -fsSL https://pixi.sh/install.sh | sh >> "$LOG" 2>&1
fi
PATH="$HOME/.pixi/bin:$PATH"; export PATH
pixi --version | tee -a "$LOG"

say "pixi install (MAX and the rest; the first time takes a while)"
if ! run pixi install; then
  say "RESULT: pixi install failed on this Mac (see above). Stop here."
  exit 1
fi
pixi run max --version 2>&1 | tee -a "$LOG"

say "test suite (no GPU needed)"
pixi run pytest tests/mxconsole -q -p no:cacheprovider 2>&1 | tail -15 | tee -a "$LOG"

# Wait for an OpenAI endpoint, then time one chat request. $1: base URL, $2: model name, $3: pid to watch.
chat() {
  base=$1 name=$2 pid=$3 waited=0
  while ! curl -sf "$base/models" >/dev/null 2>&1; do
    if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then echo "server exited before it was ready" | tee -a "$LOG"; return 1; fi
    sleep 5; waited=$((waited + 5))
    [ $waited -ge 1500 ] && { echo "not ready after 25 minutes" | tee -a "$LOG"; return 1; }
  done
  echo "ready after ${waited}s" | tee -a "$LOG"
  body='{"model":"'"$name"'","messages":[{"role":"user","content":"Write three sentences about the sea."}],"max_tokens":128}'
  start=$(python3 -c 'import time; print(time.time())')
  out=$(curl -s "$base/chat/completions" -H "Content-Type: application/json" ${MXC_API_KEY:+-H "Authorization: Bearer $MXC_API_KEY"} -d "$body")
  end=$(python3 -c 'import time; print(time.time())')
  echo "$out" >> "$LOG"
  printf '%s' "$out" | python3 -c '
import json, sys
start, end = float(sys.argv[1]), float(sys.argv[2])
try:
    r = json.load(sys.stdin)
    n = r["usage"]["completion_tokens"]
    print("answer:", r["choices"][0]["message"]["content"][:160].replace("\n", " "))
    print(f"{n} tokens in {end - start:.1f}s = {n / (end - start):.1f} tok/s (includes the first-token wait)")
except Exception as e:
    print("no answer:", e); sys.exit(1)' "$start" "$end" | tee -a "$LOG"
}

say "1/2 plain max serve $MODEL (does MAX run here, and on which device?)"
pixi run max serve --model-path "$MODEL" --port $PORT >> "$LOG" 2>&1 &
MAXPID=$!
chat "http://127.0.0.1:$PORT/v1" "$MODEL" "$MAXPID"; MAX_OK=$?
grep -i -E "device|gpu|cpu|metal|accelerator" "$LOG" | grep -v "^==" | tail -8
kill "$MAXPID" 2>/dev/null; sleep 3; kill -9 "$MAXPID" 2>/dev/null

say "2/2 mxc serve qwen2.5-0.5b (omxconsole's own path, with a scratch home)"
MXC_HOME="$(mktemp -d)/mxc"; export MXC_HOME
MXC_PORT=18452; export MXC_PORT
SERVED=$(pixi run mxc serve qwen2.5-0.5b 2>&1)
KEY=$(printf '%s' "$SERVED" | grep -o 'mst-[A-Za-z0-9_-]*' | tail -1)
printf '%s\n' "$SERVED" | sed 's/mst-[A-Za-z0-9_-]*/mst-(hidden)/g' | tee -a "$LOG"
MXC_API_KEY=$KEY chat "http://127.0.0.1:$MXC_PORT/v1" qwen2.5-0.5b ""; MXC_OK=$?
pixi run mxc status 2>&1 | tee -a "$LOG"
pixi run mxc stop 2>&1 | tee -a "$LOG"

say "RESULT"
{ [ $MAX_OK -eq 0 ] && echo "max serve: works" || echo "max serve: FAILED"
  [ $MXC_OK -eq 0 ] && echo "mxc serve: works" || echo "mxc serve: FAILED"
  echo "Full log: $LOG"; } | tee -a "$LOG"
