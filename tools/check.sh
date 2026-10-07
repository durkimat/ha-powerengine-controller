#!/usr/bin/env bash
# One-line local check. Default: ruff + the fast tests (about 40 s). `--full` adds the slow whole-app tests and the
# replay (about 4 min); run that before a release. `--card` also checks the card repo beside this one.
# Prints one line on success; on failure, the first failing output only.
set -u
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
full=0; card=0
for a in "$@"; do case "$a" in --full) full=1 ;; --card) card=1 ;; *) echo "usage: $0 [--full] [--card]"; exit 2 ;; esac; done
out="$(mktemp)"; trap 'rm -f "$out"' EXIT
fail() { echo "FAILED: $1"; tail -n 40 "$out"; exit 1; }
ruff check . >"$out" 2>&1 || fail "ruff"
sel=(-m "not slow"); [ "$full" = 1 ] && sel=()
python3 -m pytest -q -x -n auto --dist loadscope "${sel[@]}" -p no:cacheprovider >"$out" 2>&1 || fail "pytest"
summary="$(tail -n 1 "$out")"
if [ "$card" = 1 ]; then
  c="../ha-powerengine-card"; [ -d "$c" ] || { echo "card repo not found at $c"; exit 1; }
  (cd "$c" && node --check ha-powerengine-card.js && node --test tests/*.test.cjs) >"$out" 2>&1 || fail "card"
fi
echo "OK: ruff clean; $summary$([ "$card" = 1 ] && echo '; card ok')$([ "$full" = 1 ] || echo ' (fast tier; use --full before a release)')"
