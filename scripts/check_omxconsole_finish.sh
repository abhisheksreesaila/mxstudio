#!/usr/bin/env bash
# The checker for docs/goals/omxconsole-finish.md: PASS or FAIL per milestone. Run from the repo root.
cd "$(dirname "$0")/.." || exit 2
pass() { echo "PASS $1"; }; fail() { echo "FAIL $1: $2"; ok=1; }
ok=0

# M1: the leftovers have tests (written first) and they pass
if pixi run pytest -q tests/mxconsole/test_console_finish.py >/dev/null 2>&1; then pass "M1 leftovers"; else fail "M1 leftovers" "tests/mxconsole/test_console_finish.py"; fi

# M2: the live states were seen and saved
shots=docs/goals/omxconsole-finish
if [ -f "$shots/beacon-pull.png" ] && [ -f "$shots/add-stages.png" ] && [ -f "$shots/add-ready.png" ]; then pass "M2 live states"; else fail "M2 live states" "missing screenshots in $shots"; fi

# M3: new screenshots and a tour that no longer describes the old top bar
stale=$(grep -ci "hardware chip\|dark top bar\|daemon dot\|dot on the far right" docs/site/console.html)
newer=$(find docs/assets/screens -name '*.png' -newer docs/goals/omxconsole-finish.md | wc -l)
if [ "$stale" -eq 0 ] && [ "$newer" -ge 10 ]; then pass "M3 docs tour"; else fail "M3 docs tour" "$stale stale phrases, $newer of 13 screenshots retaken"; fi

# M4: F-045 done in the plan and the whole suite green
if grep -q "^## F-045 .*\[done\]" docs/plan.md && pixi run pytest -q tests/mxconsole >/dev/null 2>&1; then pass "M4 reviewed"; else fail "M4 reviewed" "F-045 not done or suite red"; fi
exit $ok
