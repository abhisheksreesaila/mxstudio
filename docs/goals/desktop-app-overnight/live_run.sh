#!/bin/sh
# M3's live proof: run mxconsole/setup/install.sh as a brand-new user would, from nothing: a scratch HOME with
# no pixi, the plain system PATH, and a tarball of this repo's HEAD as the download. Real pixi, real MAX, real tray.
# Ports 18461 (daemon) and 18462 (setup page). Start at login is left alone (the owner's real systemd).
set -u
REPO=$(cd "$(dirname "$0")/../../.." && pwd)
SCRATCH="${SCRATCH:-$HOME/mxc-setup-scratch}"
rm -rf "$SCRATCH" && mkdir -p "$SCRATCH/home"
git -C "$REPO" archive --prefix=mxstudio/ -o "$SCRATCH/mxstudio.tar.gz" HEAD
start=$(date +%s)
env -i HOME="$SCRATCH/home" USER="$USER" LANG="${LANG:-C.UTF-8}" PATH=/usr/local/bin:/usr/bin:/bin \
  XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-}" DISPLAY="${DISPLAY:-}" \
  DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-}" \
  MXC_SOURCE="$SCRATCH/mxstudio.tar.gz" MXC_PORT=18461 MXC_SETUP_PORT=18462 \
  MXC_SETUP_NO_BROWSER=1 MXC_SETUP_NO_LOGIN=1 \
  sh "$REPO/mxconsole/setup/install.sh"
code=$?
echo "install.sh exit $code after $(( $(date +%s) - start ))s"
