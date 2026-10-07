#!/usr/bin/env bash
# Step 1 of the single-CI release (docs/RELEASING.md): put the version bump on the branch BEFORE the PR's CI runs, so CI
# tests the exact commit that gets merged. Needs git only (no gh, no token): a cloud session can run it.
#
#   tools/prepare_release.sh <version> --notes <file> [--title <text>] [--no-push] [--session-url <url>]
#                            [--card-notes <file> [--card-dir <dir>]]
#
# --card-notes: the card is released too. Run it with the card repo (default ../ha-powerengine-card) checked out on the
#   card's change branch; the same checks apply to it, and it gets CARD_VERSION, its CHANGELOG section and a commit and
#   push of its own. Open both PRs afterwards and let both CIs pass before starting the Release workflow.
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

VERSION="" NOTES="" TITLE="" PUSH=1 SESSION="${CLAUDE_SESSION_URL:-}" CARD_NOTES="" CARD_DIR=""
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) sed -n '2,19p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    --notes) NOTES="${2:?--notes needs a file}"; shift 2 ;;
    --title) TITLE="${2:?--title needs text}"; shift 2 ;;
    --no-push) PUSH=0; shift ;;
    --card-notes) CARD_NOTES="${2:?--card-notes needs a file}"; shift 2 ;;
    --card-dir) CARD_DIR="${2:?--card-dir needs a directory}"; shift 2 ;;
    --session-url) SESSION="${2:?--session-url needs a url}"; shift 2 ;;
    -*) die "unknown option: $1" ;;
    *) [ -z "$VERSION" ] || die "more than one version given"; VERSION="$1"; shift ;;
  esac
done
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "give the version, e.g. 0.9.116"
[ -s "$NOTES" ] || die "--notes <file> is required and must not be empty"
grep -q '^### Behaviour changes' "$NOTES" || die "notes must start with '### Behaviour changes' (write 'None.' if none)"

[ -z "$CARD_NOTES" ] || [ -s "$CARD_NOTES" ] || die "--card-notes file is missing or empty: $CARD_NOTES"
[ -n "$CARD_NOTES" ] || [ -z "$CARD_DIR" ] || die "--card-dir given without --card-notes"
[ -z "$CARD_NOTES" ] || CARD_NOTES="$(cd "$(dirname "$CARD_NOTES")" && pwd)/$(basename "$CARD_NOTES")"
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

CARD=""
if [ -n "$CARD_NOTES" ]; then
  CARD="${CARD_DIR:-$ROOT/../ha-powerengine-card}"
  [ -f "$CARD/ha-powerengine-card.js" ] || die "card repo not found at $CARD (clone it beside this repo, or give --card-dir)"
  CARD="$(cd "$CARD" && pwd)"
  CARD_BRANCH="$(git -C "$CARD" rev-parse --abbrev-ref HEAD)"
  [ "$CARD_BRANCH" != "main" ] && [ "$CARD_BRANCH" != "HEAD" ] || die "the card repo is on '$CARD_BRANCH': check out the card's change branch"
  clean_tree "$CARD" || die "the card working tree has uncommitted changes: commit them first"
  git -C "$CARD" fetch -q origin main
  cbehind="$(git -C "$CARD" rev-list --count HEAD..origin/main)"
  [ "$cbehind" = 0 ] || die "the card branch is $cbehind commit(s) behind main: merge origin/main in the card repo first"
  card_main="$(git -C "$CARD" show origin/main:ha-powerengine-card.js | sed -n 's/^const CARD_VERSION = "\([^"]*\)".*/\1/p' | head -n 1)"
  newer_than "$VERSION" "$card_main" || die "version $VERSION is not newer than the card's $card_main on main"
  [ "$(current_version card "$CARD/ha-powerengine-card.js")" = "$card_main" ] || die "the card branch already changed CARD_VERSION: nothing to prepare, or a half-done run"
fi

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

if [ -n "$CARD" ]; then
  WORK="$CARD"
  change_file card_version "$CARD/ha-powerengine-card.js" "$VERSION"
  change_file card_changelog "$CARD/CHANGELOG.md" "$VERSION" "$CARD_NOTES"
  cmsg="$(commit_message "$VERSION (card)${TITLE:+: $TITLE}")"
  git -C "$CARD" add -- ha-powerengine-card.js CHANGELOG.md
  git -C "$CARD" commit -q -F "$cmsg"
  note "card: committed $(git -C "$CARD" rev-parse --short HEAD) on $CARD_BRANCH"
  if [ "$PUSH" = 1 ]; then git -C "$CARD" push -q -u origin "$CARD_BRANCH" && note "card: pushed"; else note "card: not pushed (--no-push)"; fi
fi
echo "Next: open the PR$([ -n "$CARD" ] && echo "s (app and card)"), and when CI is green on this commit$([ -n "$CARD" ] && echo " and the card's") start the Release workflow with prepared=true (version $VERSION, app_branch $BRANCH$([ -n "$CARD" ] && echo ", card_branch $CARD_BRANCH, card_notes $CARD_NOTES"))."
