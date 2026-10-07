#!/usr/bin/env bash
# Step 1 of the single-CI release (docs/RELEASING.md): put the version bump on the branch BEFORE the PR's CI runs, so CI
# tests the exact commit that gets merged. Needs git only (no gh, no token): a cloud session can run it.
#
#   tools/prepare_release.sh <version> --notes <file> [--title <text>] [--no-push] [--session-url <url>]
#
# Run it in the checkout of the branch that carries the change. It stops unless the branch is clean, is not main, contains
# the current origin/main (merge main first), and <version> is newer than main's. It then bumps __version__, INSTALL.md
# and CHANGELOG.md (the same edits release.sh makes) and commits and pushes with the usual trailers. Tests are NOT run
# here: run tools/check.sh --full first, and let the PR's CI pass on the commit this makes. Then start the Release
# workflow with prepared=true.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=release.sh
source "$HERE/release.sh"

VERSION="" NOTES="" TITLE="" PUSH=1 SESSION="${CLAUDE_SESSION_URL:-}"
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) sed -n '2,13p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    --notes) NOTES="${2:?--notes needs a file}"; shift 2 ;;
    --title) TITLE="${2:?--title needs text}"; shift 2 ;;
    --no-push) PUSH=0; shift ;;
    --session-url) SESSION="${2:?--session-url needs a url}"; shift 2 ;;
    -*) die "unknown option: $1" ;;
    *) [ -z "$VERSION" ] || die "more than one version given"; VERSION="$1"; shift ;;
  esac
done
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "give the version, e.g. 0.9.116"
[ -s "$NOTES" ] || die "--notes <file> is required and must not be empty"
grep -q '^### Behaviour changes' "$NOTES" || die "notes must start with '### Behaviour changes' (write 'None.' if none)"

ROOT="$(git rev-parse --show-toplevel)"; cd "$ROOT"
[ -f apps/powerengine/pe_core/__init__.py ] || die "run this inside the PowerEngine app repo"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
[ "$BRANCH" != "main" ] && [ "$BRANCH" != "HEAD" ] || die "run it on the change's branch, not on '$BRANCH'"
clean_tree "$ROOT" || die "the working tree has uncommitted changes: commit them first"
git fetch -q origin main
behind="$(git rev-list --count HEAD..origin/main)"
[ "$behind" = 0 ] || die "the branch is $behind commit(s) behind main: merge origin/main first, then run this again"
main_now="$(git show origin/main:apps/powerengine/pe_core/__init__.py | sed -n 's/^__version__ *= *"\([^"]*\)".*/\1/p')"
newer_than "$VERSION" "$main_now" || die "version $VERSION is not newer than main's $main_now"
[ "$(current_version app apps/powerengine/pe_core/__init__.py)" = "$main_now" ] || die "the branch already changed __version__: nothing to prepare, or a half-done run"

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT; DRY=0; WORK="$ROOT"
change_file app_version "$ROOT/apps/powerengine/pe_core/__init__.py" "$VERSION"
change_file install "$ROOT/docs/INSTALL.md" "$VERSION"
change_file app_changelog "$ROOT/CHANGELOG.md" "$VERSION" "$NOTES"

TITLE="${TITLE:-$(first_line "$NOTES")}"
[ -n "$SESSION" ] && SESSION_URL="$SESSION"
msg="$(commit_message "$VERSION${TITLE:+: $TITLE}")"
git add -- apps/powerengine/pe_core/__init__.py docs/INSTALL.md CHANGELOG.md
git commit -q -F "$msg"
note "committed $(git rev-parse --short HEAD) on $BRANCH: $VERSION${TITLE:+: $TITLE}"
if [ "$PUSH" = 1 ]; then git push -q -u origin "$BRANCH" && note "pushed; CI now runs on exactly this commit"; else note "not pushed (--no-push)"; fi
echo "Next: when CI is green on this commit, start the Release workflow with prepared=true (version $VERSION, app_branch $BRANCH)."
