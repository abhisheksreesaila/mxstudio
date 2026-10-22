#!/usr/bin/env bash
# The checker for docs/goals/desktop-app-overnight.md: PASS or FAIL per milestone. Run from the repo root.
cd "$(dirname "$0")/.." || exit 2
pass() { echo "PASS $1"; }; fail() { echo "FAIL $1: $2"; ok=1; }
ok=0
t() { pixi run pytest -q -p no:cacheprovider "$@" >/dev/null 2>&1; }

# M1: unified memory on a Mac, tested with recorded Mac output
if [ -f tests/mxconsole/test_mac_memory.py ] && t tests/mxconsole/test_mac_memory.py; then pass "M1 Mac memory"; else fail "M1 Mac memory" "tests/mxconsole/test_mac_memory.py"; fi

# M2: porting refused off Linux, before anything runs
if [ -f tests/mxconsole/test_port_platform.py ] && t tests/mxconsole/test_port_platform.py; then pass "M2 port is Linux-only"; else fail "M2 port is Linux-only" "tests/mxconsole/test_port_platform.py"; fi

# M3: first run from nothing, tested and seen live
shots=docs/goals/desktop-app-overnight
if [ -f tests/mxconsole/test_setup.py ] && t tests/mxconsole/test_setup.py && [ -f "$shots/setup-progress.png" ] && [ -f "$shots/setup-done.png" ] && [ -f "$shots/live-run.txt" ] && grep -q "^console up" "$shots/live-run.txt"; then
  pass "M3 first run"; else fail "M3 first run" "tests/mxconsole/test_setup.py, screenshots or live-run.txt"; fi

# M4: the Mac bundle builds and has the right shape
if [ -f tests/mxconsole/test_mac_bundle.py ] && t tests/mxconsole/test_mac_bundle.py && pixi run python scripts/build_mac_app.py >/dev/null 2>&1 && [ -f dist/omxconsole-mac.zip ]; then
  pass "M4 Mac bundle"; else fail "M4 Mac bundle" "tests/mxconsole/test_mac_bundle.py or dist/omxconsole-mac.zip"; fi

# M5: reviewed, suite green, plan and report written
if [ -f docs/goals/desktop-app-overnight-report.md ] && grep -q "^## F-050 .*\[\(review\|done\)\]" docs/plan.md && grep -q "^## F-051 .*\[\(review\|done\)\]" docs/plan.md && t tests/mxconsole; then
  pass "M5 reviewed"; else fail "M5 reviewed" "report, plan status or suite"; fi
exit $ok
