#!/bin/sh
# omxconsole: first run from nothing, and every launch after it. This is what omxconsole.app runs on a Mac
# (Contents/MacOS/omxconsole), and it works the same on Linux.
#
# First run: installs pixi (into ~/.pixi, nothing system-wide), shows a setup page in the browser, downloads
# omxconsole into ~/.mxc/runtime/app, installs MAX, Python and the tray's Qt with pixi, turns on start at login,
# puts `mxc` in ~/.local/bin, starts the tray (which starts the server) and sends the page to the console.
# Later launches: makes sure the tray is running and opens the console window.
#
# Settings (all optional): MXC_RUNTIME (default ~/.mxc/runtime), MXC_SOURCE (a .tar.gz URL or file),
# MXC_PORT (the daemon's port), MXC_SETUP_PORT (the setup page, default 11436), PIXI_HOME,
# MXC_SETUP_NO_BROWSER=1, MXC_SETUP_NO_LOGIN=1 (don't touch start at login or ~/.local/bin).
set -u

HERE=$(cd "$(dirname "$0")" && pwd)
RES="${MXC_SETUP_RESOURCES:-$HERE}"   # where setup_server.py and setup.html are (the .app keeps them in Resources)
RUNTIME="${MXC_RUNTIME:-$HOME/.mxc/runtime}"
APP="$RUNTIME/app"
LOG="$RUNTIME/setup.log"
OUT="$RUNTIME/setup-output.log"
SOURCE="${MXC_SOURCE:-https://github.com/abhisheksreesaila/mxstudio/archive/refs/heads/main.tar.gz}"
SETUP_PORT="${MXC_SETUP_PORT:-11436}"
DAEMON_PORT="${MXC_PORT:-11435}"
PIXI_DIR="${PIXI_HOME:-$HOME/.pixi}"

note() { printf '%s %s %s %s\n' "$(date +%s)" "$1" "$2" "${3:-}" >> "$LOG"; }
tell() {  # $1: title, $2: text. A Mac app started from Finder has no terminal: say it in a dialog.
  [ "$(uname -s)" = Darwin ] && [ -z "${MXC_SETUP_NO_BROWSER:-}" ] || return 0
  text=$(printf '%s' "$2" | sed 's/\\/\\\\/g; s/"/\\"/g')  # AppleScript string: escape \ and "
  osascript -e "display alert \"$1\" message \"$text\"" >/dev/null 2>&1
}
fail() {
  note "$1" failed "$2"
  echo "omxconsole setup: $2" >&2
  tell "omxconsole setup stopped" "$2"
  exit 1
}
open_url() {
  [ -n "${MXC_SETUP_NO_BROWSER:-}" ] && return 0
  if [ "$(uname -s)" = Darwin ]; then open "$1"; else xdg-open "$1" >/dev/null 2>&1 & fi
}
find_pixi() {
  PIXI=$(command -v pixi 2>/dev/null || true)
  if [ -z "$PIXI" ] && [ -x "$PIXI_DIR/bin/pixi" ]; then PIXI="$PIXI_DIR/bin/pixi"; fi
}
answers() {  # $1: URL, $2: text the body must contain, $3: seconds to wait
  i=0
  while [ "$i" -lt "$3" ]; do
    curl -fs -m 2 "$1" 2>/dev/null | grep -q "$2" && return 0
    sleep 1; i=$((i + 1))
  done
  return 1
}

mkdir -p "$RUNTIME"

# --- later launches -----------------------------------------------------------------------------------
later() {
  find_pixi
  [ -n "$PIXI" ] || { echo "omxconsole: pixi is gone; delete $APP/.mxc-ready to set up again" >&2; exit 1; }
  nohup "$PIXI" run --manifest-path "$APP/pixi.toml" mxc tray >> "$RUNTIME/tray.log" 2>&1 &  # exits 3 if one runs
  exec "$PIXI" run --manifest-path "$APP/pixi.toml" mxc app
}
[ -f "$APP/.mxc-ready" ] && later

# An earlier first run that gave up waiting for a slow first start, while omxconsole did come up from $APP:
# adopt it rather than delete it under the running server.
if [ -f "$APP/pixi.toml" ] && answers "http://127.0.0.1:$DAEMON_PORT/health" mst 2; then
  touch "$APP/.mxc-ready"
  later
fi

# --- first run ----------------------------------------------------------------------------------------
# One setup at a time (a double click, or a click and a terminal): the lock is a directory, made atomically.
LOCK="$RUNTIME/.setup-lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  holder=$(cat "$LOCK/pid" 2>/dev/null)
  if [ -n "$holder" ] && kill -0 "$holder" 2>/dev/null; then
    echo "omxconsole setup is already running (pid $holder); its page is http://127.0.0.1:$SETUP_PORT/" >&2
    open_url "http://127.0.0.1:$SETUP_PORT/"
    exit 0
  fi
  rm -rf "$LOCK" && mkdir "$LOCK" || fail pixi "couldn't take the setup lock $LOCK"
fi
echo $$ > "$LOCK/pid"
trap 'rm -rf "$LOCK"' EXIT

# A setup page left over from an earlier run (it lingers a minute) would answer for this one, then vanish.
old=$(cat "$RUNTIME/setup-server.pid" 2>/dev/null)
if [ -n "$old" ] && ps -o args= -p "$old" 2>/dev/null | grep -q setup_server.py; then
  kill "$old" 2>/dev/null
  i=0; while kill -0 "$old" 2>/dev/null && [ $i -lt 20 ]; do sleep 0.2; i=$((i + 1)); done
fi
rm -f "$RUNTIME/setup-server.pid"

: > "$LOG"
: > "$OUT"

note pixi running
find_pixi
if [ -z "$PIXI" ]; then
  curl -fsSL https://pixi.sh/install.sh | PIXI_HOME="$PIXI_DIR" PIXI_NO_PATH_UPDATE=1 sh >> "$OUT" 2>&1
  find_pixi
fi
[ -n "$PIXI" ] || fail pixi "could not install pixi. Is the internet reachable? Details: $OUT"
note pixi done "$("$PIXI" --version 2>/dev/null)"

# The page: a tiny server on Python from pixi (omxconsole itself isn't here yet). Setup goes on without it.
"$PIXI" exec -s python=3.12 python "$RES/setup_server.py" "$LOG" "$SETUP_PORT" >> "$OUT" 2>&1 &
if answers "http://127.0.0.1:$SETUP_PORT/state" steps 90; then
  open_url "http://127.0.0.1:$SETUP_PORT/"
else
  echo "omxconsole setup: the setup page didn't start; carrying on (progress in $LOG)" >&2
  tell "Setting up omxconsole" "The setup page couldn't start, so this runs quietly: a few minutes, then the console opens. Progress: $LOG"
fi

note source running "$SOURCE"
rm -rf "$APP.part" && mkdir -p "$APP.part"
case "$SOURCE" in
  http://*|https://*) TARBALL="$RUNTIME/source.tar.gz"
    curl -fsSL -o "$TARBALL" "$SOURCE" 2>> "$OUT" || fail source "couldn't download omxconsole ($SOURCE). Is the internet reachable?" ;;
  *) TARBALL="$SOURCE" ;;
esac
tar xzf "$TARBALL" -C "$APP.part" --strip-components 1 2>> "$OUT" || fail source "the download was cut off or damaged ($SOURCE); open omxconsole again to retry"
[ "$TARBALL" = "$SOURCE" ] || rm -f "$TARBALL"
[ -f "$APP.part/pixi.toml" ] || fail source "the download didn't contain omxconsole ($SOURCE)"
rm -rf "$APP" && mv "$APP.part" "$APP"
note source done

note env running
"$PIXI" install --manifest-path "$APP/pixi.toml" >> "$OUT" 2>&1 || fail env "installing MAX and Python failed. Details: $OUT"
note env done "$(du -sh "$APP/.pixi/envs/default" 2>/dev/null | cut -f1) on disk"

note app running
"$PIXI" install --manifest-path "$APP/pixi.toml" -e app >> "$OUT" 2>&1 || fail app "installing the window and tray failed. Details: $OUT"
note app done "$(du -sh "$APP/.pixi/envs/app" 2>/dev/null | cut -f1) on disk"

if [ -n "${MXC_SETUP_NO_LOGIN:-}" ]; then
  note login skipped "left alone (MXC_SETUP_NO_LOGIN)"
else
  note login running
  if said=$("$PIXI" run --manifest-path "$APP/pixi.toml" python -m mxconsole.autostart 2>> "$OUT"); then
    note login done "$said"
  else
    note login skipped "couldn't turn it on: $(tail -1 "$OUT")"  # not worth stopping for
  fi
fi

note start running
nohup "$PIXI" run --manifest-path "$APP/pixi.toml" mxc tray >> "$RUNTIME/tray.log" 2>&1 &
answers "http://127.0.0.1:$DAEMON_PORT/health" mst 600 || fail start "omxconsole didn't start. Details: $RUNTIME/tray.log"
touch "$APP/.mxc-ready"
note console - "http://127.0.0.1:$DAEMON_PORT/console"
note start done
