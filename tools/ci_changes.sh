#!/usr/bin/env bash
# Does a change touch anything the tests care about? Prints "code=true" or "code=false" (for $GITHUB_OUTPUT).
#   tools/ci_changes.sh <base-sha> [head-ref]
# Notes, plans and history never change behaviour, so a change that only touches them skips the slow steps. The CI job
# still runs and reports success, because main requires the "Lint and tests" check: a workflow-level paths-ignore would
# leave that check missing and block the merge. Any doubt (no base, unknown commit, diff fails) means code=true.
base="${1:-}"; head="${2:-HEAD}"
ignored='^(CLAUDE\.md|release-notes/.*|docs/plans/.*|docs/history/.*|docs/RELEASING\.md)$'
if [ -z "$base" ] || [[ "$base" =~ ^0+$ ]] || ! git cat-file -e "$base^{commit}" 2>/dev/null; then echo "code=true"; exit 0; fi
files="$(git diff --name-only "$base" "$head" 2>/dev/null)" || { echo "code=true"; exit 0; }
[ -n "$files" ] || { echo "code=true"; exit 0; }
if printf '%s\n' "$files" | grep -Evq "$ignored"; then echo "code=true"; else echo "code=false"; fi
