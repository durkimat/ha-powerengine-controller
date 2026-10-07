#!/usr/bin/env bash
# One command for a PowerEngine release: checks, version bump, changelog, PR, CI, squash-merge, GitHub release,
# clean-up. The app is always released; the card only when you give --card-notes (it is released only when it changes).
#
#   tools/release.sh <version> --app-notes <file> [--card-notes <file>]
#                    [--app-branch <branch>] [--card-branch <branch>]
#                    [--app-dir <dir>] [--card-dir <dir>] [--title <text>]
#                    [--skip-checks] [--skip-replay] [--dry-run]
#
# The notes files are the release notes as Markdown. App notes start with "### Behaviour changes" (the update card shows
# them to the owner). They become the CHANGELOG section, the PR body and the GitHub release body.
#
# GitHub access goes through the gh CLI (gh auth login) and git's own credentials; no token is read or handled here.
# CLAUDE_SESSION_URL overrides the session line in commits and PR bodies.

set -euo pipefail

APP_REPO_SLUG="${PE_APP_REPO:-durkimat/ha-powerengine-controller}"
CARD_REPO_SLUG="${PE_CARD_REPO:-durkimat/ha-powerengine-card}"
SESSION_URL="${CLAUDE_SESSION_URL:-https://claude.ai/code/session_01LkznDwr2XBgEZmAma9wpfW}"
POLL_SECONDS="${PE_POLL_SECONDS:-20}"
POLL_MAX_SECONDS="${PE_POLL_MAX_SECONDS:-720}"

die() { printf 'release.sh: error: %s\n' "$*" >&2; exit 1; }
step() { printf '\n==> %s\n' "$*"; }
note() { printf '    %s\n' "$*"; }
dry() { printf '    [dry-run] would: %s\n' "$*"; }
usage() { sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

# git with network access (push, fetch, pull): credentials come from git's own helper (gh auth setup-git).
gitn() { git "$@"; }

TMP=""
SUMMARY=()

# --- GitHub REST API ---------------------------------------------------------------------------------------------
# api METHOD PATH [JSON_FILE]: prints the response body; dies with GitHub's message on an HTTP error.
api() {
  local method="$1" path="$2" data="${3:-}" out
  local args=(api -X "$method" -H "Accept: application/vnd.github+json" "$path")
  [ -n "$data" ] && args+=(--input "$data")
  out="$(gh "${args[@]}" 2>&1)" || die "GitHub refused $method $path: $(printf '%s' "$out" | head -c 300)"
  printf '%s\n' "$out"
}

# --- arguments ---------------------------------------------------------------------------------------------------
VERSION="" APP_NOTES="" CARD_NOTES="" APP_BRANCH="" CARD_BRANCH="" APP_DIR="" CARD_DIR="" TITLE=""
SKIP_CHECKS=0 SKIP_REPLAY=0 DRY=0

parse_args() {
  [ $# -gt 0 ] || { usage; exit 2; }
  while [ $# -gt 0 ]; do
    case "$1" in
      -h|--help) usage; exit 0 ;;
      --app-notes) APP_NOTES="${2:?--app-notes needs a file}"; shift 2 ;;
      --card-notes) CARD_NOTES="${2:?--card-notes needs a file}"; shift 2 ;;
      --app-branch) APP_BRANCH="${2:?--app-branch needs a branch}"; shift 2 ;;
      --card-branch) CARD_BRANCH="${2:?--card-branch needs a branch}"; shift 2 ;;
      --app-dir) APP_DIR="${2:?--app-dir needs a directory}"; shift 2 ;;
      --card-dir) CARD_DIR="${2:?--card-dir needs a directory}"; shift 2 ;;
      --title) TITLE="${2:?--title needs text}"; shift 2 ;;
      --skip-checks) SKIP_CHECKS=1; shift ;;
      --skip-replay) SKIP_REPLAY=1; shift ;;
      --dry-run) DRY=1; shift ;;
      -*) die "unknown option: $1 (see --help)" ;;
      *) [ -z "$VERSION" ] || die "more than one version given: $VERSION and $1"; VERSION="$1"; shift ;;
    esac
  done
  [ -n "$VERSION" ] || die "give the version, e.g. tools/release.sh 0.9.71 --app-notes notes.md"
  [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "version must look like 0.9.71, got: $VERSION"
  [ -n "$APP_NOTES" ] || die "--app-notes <file> is required"
  [ -s "$APP_NOTES" ] || die "app notes file is missing or empty: $APP_NOTES"
  grep -q '^### Behaviour changes' "$APP_NOTES" || die "app notes must start with '### Behaviour changes' (write 'None.' if none): $APP_NOTES"
  if [ -n "$CARD_NOTES" ]; then [ -s "$CARD_NOTES" ] || die "card notes file is missing or empty: $CARD_NOTES"; fi
  if [ -z "$CARD_NOTES" ] && { [ -n "$CARD_BRANCH" ] || [ -n "$CARD_DIR" ]; }; then
    die "--card-branch/--card-dir given without --card-notes: the card is only released when you give card notes"
  fi
  APP_NOTES="$(cd "$(dirname "$APP_NOTES")" && pwd)/$(basename "$APP_NOTES")"
  if [ -n "$CARD_NOTES" ]; then CARD_NOTES="$(cd "$(dirname "$CARD_NOTES")" && pwd)/$(basename "$CARD_NOTES")"; fi
}

# --- git helpers -------------------------------------------------------------------------------------------------
# The main (non-worktree) checkout of the repo that contains $1.
main_checkout() {
  local d
  d="$(cd "$1" && cd "$(git rev-parse --git-common-dir)" && pwd)" || return 1
  dirname "$d"
}
is_worktree() { [ -f "$1/.git" ]; }                        # a worktree's .git is a file; a main checkout's is a directory
current_branch() { git -C "$1" rev-parse --abbrev-ref HEAD; }
clean_tree() { [ -z "$(git -C "$1" status --porcelain --untracked-files=no)" ]; }
# The worktree directory that has $2 checked out, among the worktrees of the repo at $1 (empty if none).
worktree_of_branch() {
  git -C "$1" worktree list --porcelain | awk -v b="refs/heads/$2" '/^worktree /{d=substr($0,10)} $1=="branch" && $2==b {print d; exit}'
}
first_line() {           # the first line of the notes that is not a heading or "None.", as a short title
  awk 'NF && !/^[[:space:]]*#/ && !/^[[:space:]]*[-*]?[[:space:]]*[Nn]one\.?[[:space:]]*$/ { print; exit }' "$1" | sed -e 's/^[[:space:]]*[-*][[:space:]]*//' -e 's/\*\*//g' | cut -c1-70
}

# --- file edits (pure: write the new content to a temp file; apply or, in a dry run, show the diff) -------------------
edit() {                # edit KIND FILE OUT [VERSION] [NOTES]
  python3 - "$@" <<'PYEOF'
import re, sys
kind, path, out = sys.argv[1:4]
version = sys.argv[4] if len(sys.argv) > 4 else ""
notes = open(sys.argv[5], encoding="utf-8").read().strip() if len(sys.argv) > 5 else ""
text = open(path, encoding="utf-8").read()

def sub(pattern, repl, flags=0, count=1, what=""):
    new, n = re.subn(pattern, repl, text, count=count, flags=flags)
    if n < 1:
        sys.exit(f"could not find {what} in {path}")
    return new

if kind == "app_version":
    text = sub(r'^(__version__\s*=\s*")[^"]+(")', lambda m: m.group(1) + version + m.group(2), re.M, what="__version__")
elif kind == "install":
    text = sub(r'(\*\*Version this guide matches:\*\*\s*)\d+(?:\.\d+)*', lambda m: m.group(1) + version, what='"Version this guide matches"')
    text = sub(r'(PowerEngine\s+)\d+(?:\.\d+)*(\s+starting)', lambda m: m.group(1) + version + m.group(2), what='"PowerEngine x.y.z starting"')
elif kind in ("app_changelog", "card_changelog"):
    head = f"## {version} (beta)" if kind == "app_changelog" else f"## {version}"
    if re.search(rf"^## {re.escape(version)}\b", text, re.M):
        sys.exit(f"{path} already has a section for {version}")
    m = re.search(r"^## ", text, re.M)
    if not m:
        sys.exit(f"no '## ' section in {path} to insert above")
    text = text[:m.start()] + head + "\n\n" + notes + "\n\n" + text[m.start():]
elif kind == "card_version":
    text = sub(r'^(const CARD_VERSION = ")[^"]+(";)', lambda m: m.group(1) + version + m.group(2), re.M, what="CARD_VERSION")
else:
    sys.exit("unknown edit " + kind)
open(out, "w", encoding="utf-8").write(text)
PYEOF
}
# change_file KIND FILE VERSION [NOTES]: real run rewrites FILE; a dry run shows what would change.
change_file() {
  local kind="$1" file="$2" new
  new="$(mktemp "$TMP/edit.XXXXXX")"
  edit "$kind" "$file" "$new" "${@:3}" || die "editing $file failed"
  if [ "$DRY" = 1 ]; then
    dry "change ${file#"$WORK"/}"
    diff -u --label "a/${file#"$WORK"/}" --label "b/${file#"$WORK"/}" "$file" "$new" | sed 's/^/        /' | head -n 24 || true
  else
    cp "$new" "$file"
    note "changed ${file#"$WORK"/}"
  fi
}

current_version() {      # current_version KIND FILE
  case "$1" in
    app) sed -n 's/^__version__ *= *"\([^"]*\)".*/\1/p' "$2" | head -n 1 ;;
    card) sed -n 's/^const CARD_VERSION = "\([^"]*\)".*/\1/p' "$2" | head -n 1 ;;
  esac
}
newer_than() { [ "$1" != "$2" ] && [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -n 1)" = "$1" ]; }

# --- checks ------------------------------------------------------------------------------------------------------
run_app_checks() {
  local dir="$1"
  step "App checks in $dir"
  if [ "$SKIP_CHECKS" = 1 ]; then note "skipped (--skip-checks)"; return 0; fi
  (
    cd "$dir"
    export PATH="$HOME/.local/bin:$PATH"
    note "ruff check ."; ruff check . || die "ruff found problems; nothing was changed"
    local sel=(); [ "$SKIP_REPLAY" = 1 ] && sel=(--ignore=tests/test_replay.py)
    note "pytest, in parallel${sel:+ (replay skipped: --skip-replay)}"
    python3 -m pytest -q -n auto --dist loadscope "${sel[@]}" || die "tests failed; nothing was changed"
  )
}
run_card_checks() {
  local dir="$1"
  step "Card checks in $dir"
  if [ "$SKIP_CHECKS" = 1 ]; then note "skipped (--skip-checks)"; return 0; fi
  (
    cd "$dir"
    note "node --check ha-powerengine-card.js"; node --check ha-powerengine-card.js || die "node --check failed; nothing was changed"
    note "node --test tests/*.test.cjs"; node --test tests/*.test.cjs >/dev/null || { node --test tests/*.test.cjs 2>&1 | tail -n 30; die "card tests failed; nothing was changed"; }
  )
}

# --- commit, PR, CI, merge, release ------------------------------------------------------------------------------
commit_message() {       # commit_message TITLE  -> file
  local f; f="$(mktemp "$TMP/msg.XXXXXX")"
  printf '%s\n\nCo-Authored-By: %s <noreply@anthropic.com>\nClaude-Session: %s\n' "$1" "${CLAUDE_MODEL_NAME:-Claude Opus 5.5}" "$SESSION_URL" > "$f"
  echo "$f"
}
pr_body() {              # pr_body NOTES_FILE -> file
  local f; f="$(mktemp "$TMP/body.XXXXXX")"
  { printf '%s' "$(cat "$1")"; printf '\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)\n\n%s\n' "$SESSION_URL"; } > "$f"
  echo "$f"
}

wait_for_ci() {          # wait_for_ci SLUG SHA PR_URL
  local slug="$1" sha="$2" pr_url="$3" waited=0 stable=0 last="" body total bad pending
  step "Waiting for CI on $sha (every ${POLL_SECONDS}s, up to $((POLL_MAX_SECONDS / 60)) minutes)"
  while :; do
    sleep "$POLL_SECONDS"; waited=$((waited + POLL_SECONDS))
    body="$(api GET "/repos/$slug/commits/$sha/check-runs?per_page=100")"
    total="$(jq '.check_runs | length' <<<"$body")"
    bad="$(jq -r '[.check_runs[] | select(.status == "completed" and (.conclusion | IN("success","skipped","neutral") | not)) | "\(.name) (\(.conclusion))"] | join(", ")' <<<"$body")"
    pending="$(jq '[.check_runs[] | select(.status != "completed")] | length' <<<"$body")"
    if [ -n "$bad" ]; then die "CI failed: $bad. Not merging. PR: $pr_url"; fi
    note "$(printf '%3ss: %s checks, %s still running' "$waited" "$total" "$pending")"
    # all done, and the same number of runs twice in a row (a run that hasn't registered yet would be missed otherwise)
    if [ "$total" -gt 0 ] && [ "$pending" -eq 0 ]; then
      if [ "$last" = "$total" ]; then stable=1; fi
      last="$total"
      [ "$stable" = 1 ] && { note "all $total checks passed"; return 0; }
    else
      last=""
    fi
    [ "$waited" -lt "$POLL_MAX_SECONDS" ] || die "CI did not finish within $((POLL_MAX_SECONDS / 60)) minutes. Not merging. PR: $pr_url"
  done
}

# open_pr_for SLUG BRANCH: the number of an open PR whose head is BRANCH (empty if none). Read-only.
open_pr_for() {
  api GET "/repos/$1/pulls?state=open&head=${1%%/*}:$2" | jq -r '.[0].number // empty'
}

# release_repo LABEL SLUG WORK MAIN BRANCH VERSION NOTES TITLE FILES... (files are relative to WORK, already changed)
# Commits, pushes, opens the PR, waits for CI, squash-merges and creates the release. Sets REL_PR, REL_URL, REL_SHA.
release_repo() {
  local label="$1" slug="$2" work="$3" branch="$4" version="$5" notes="$6" title="$7"; shift 7
  local msg body req pr pr_url pr_num head merged merge_sha rel existing
  msg="$(commit_message "$title")"; body="$(pr_body "$notes")"
  step "$label: commit, push, PR"
  if [ "$DRY" = 1 ]; then
    dry "git add $*  (in $work)"
    dry "git commit, with message:"; sed 's/^/        | /' "$msg"
    dry "git push -u origin $branch  (credentials from git)"
    existing="$(open_pr_for "$slug" "$branch" 2>/dev/null || true)"
    if [ -n "$existing" ]; then dry "PATCH /repos/$slug/pulls/$existing (PR #$existing is already open on $branch: reused, not a new PR)  title: \"$title\""
    else dry "POST /repos/$slug/pulls  title: \"$title\"  head: $branch  base: main"; fi
    dry "  body:"; sed 's/^/        | /' "$body" | head -n 14
    dry "poll GET /repos/$slug/commits/<sha>/check-runs every ${POLL_SECONDS}s for up to $((POLL_MAX_SECONDS / 60)) minutes; stop on any failure"
    dry "PUT /repos/$slug/pulls/<n>/merge  merge_method: squash"
    dry "POST /repos/$slug/releases  tag v$version  prerelease false  make_latest \"true\"  body: the notes"
    REL_PR="(dry run)"; REL_URL="(dry run) https://github.com/$slug/releases/tag/v$version"; REL_SHA="(dry run)"
    return 0
  fi
  git -C "$work" add -- "$@"
  git -C "$work" commit -q -F "$msg"
  head="$(git -C "$work" rev-parse HEAD)"
  gitn -C "$work" push -u origin "$branch" 2>&1 | sed 's/^/    /'
  req="$(mktemp "$TMP/req.XXXXXX")"
  jq -n --arg t "$title" --arg h "$branch" --rawfile b "$body" '{title: $t, head: $h, base: "main", body: $b}' > "$req"
  existing="$(open_pr_for "$slug" "$branch")"
  if [ -n "$existing" ]; then                  # a PR is already open on this branch (e.g. from a cloud session): reuse it
    jq -n --arg t "$title" --rawfile b "$body" '{title: $t, body: $b}' > "$req"
    pr="$(api PATCH "/repos/$slug/pulls/$existing" "$req")"
  else
    pr="$(api POST "/repos/$slug/pulls" "$req")"
  fi
  pr_num="$(jq -r .number <<<"$pr")"; pr_url="$(jq -r .html_url <<<"$pr")"
  note "PR #$pr_num: $pr_url"

  wait_for_ci "$slug" "$head" "$pr_url"

  step "$label: squash-merge PR #$pr_num"
  jq -n --arg t "$title (#$pr_num)" --arg s "$head" '{merge_method: "squash", commit_title: $t, sha: $s}' > "$req"
  merged="$(api PUT "/repos/$slug/pulls/$pr_num/merge" "$req")" || die "merge failed; PR: $pr_url"
  merge_sha="$(jq -r .sha <<<"$merged")"
  note "merged as $merge_sha"

  step "$label: release v$version"
  jq -n --arg tag "v$version" --arg sha "$merge_sha" --rawfile b "$notes" \
    '{tag_name: $tag, target_commitish: $sha, name: $tag, body: $b, draft: false, prerelease: false, make_latest: "true"}' > "$req"
  rel="$(api POST "/repos/$slug/releases" "$req")" || die "the PR is merged ($pr_url) but creating release v$version failed"
  REL_PR="#$pr_num"; REL_URL="$(jq -r .html_url <<<"$rel")"; REL_SHA="$merge_sha"
  note "$REL_URL"
}

# cleanup LABEL WORK MAIN BRANCH IS_WT: main checkout to main and pulled, worktree removed, local branch deleted.
cleanup() {
  local label="$1" work="$2" main="$3" branch="$4" is_wt="$5"
  step "$label: clean up"
  if [ "$DRY" = 1 ]; then
    if [ "$is_wt" = 1 ]; then dry "git -C $main worktree remove $work"; fi
    dry "git -C $main checkout main && git pull --ff-only"
    dry "git -C $main branch -D $branch"
    return 0
  fi
  cd "$main"
  if [ "$is_wt" = 1 ]; then git worktree remove "$work" && note "removed worktree $work"; fi
  git checkout -q main
  gitn pull -q --ff-only 2>&1 | sed 's/^/    /'
  git branch -D "$branch" >/dev/null && note "deleted local branch $branch"
  note "$main is on main at $(git rev-parse --short HEAD)"
}

# --- which directory and branch each repo releases ---------------------------------------------------------------------
# resolve_app: sets APP_MAIN, APP_WORK, APP_BR, APP_WT (1 if APP_WORK is a worktree to remove afterwards)
resolve_app() {
  local here script_dir; script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  APP_DIR="${APP_DIR:-$(git -C "$script_dir" rev-parse --show-toplevel)}"
  [ -d "$APP_DIR" ] || die "app directory not found: $APP_DIR"
  APP_DIR="$(cd "$APP_DIR" && pwd)"
  [ -f "$APP_DIR/apps/powerengine/pe_core/__init__.py" ] || die "$APP_DIR is not the PowerEngine app repo"
  APP_MAIN="$(main_checkout "$APP_DIR")"; APP_WT=0
  here="$(current_branch "$APP_DIR")"
  if [ -n "$APP_BRANCH" ]; then
    git -C "$APP_MAIN" rev-parse --verify -q "refs/heads/$APP_BRANCH" >/dev/null || die "app branch not found locally: $APP_BRANCH"
    APP_BR="$APP_BRANCH"
    APP_WORK="$(worktree_of_branch "$APP_MAIN" "$APP_BR")"
    if [ -z "$APP_WORK" ]; then APP_WORK="$APP_MAIN"; APP_NEEDS_CHECKOUT=1; fi
  elif is_worktree "$APP_DIR" && [ "$here" != "main" ] && [ "$here" != "HEAD" ] \
       && [ "$(git -C "$APP_DIR" rev-list --count "main..HEAD")" -gt 0 ]; then
    APP_BR="$here"; APP_WORK="$APP_DIR"
  else
    die "nothing to release: run from a worktree on a branch that has commits, or give --app-branch <branch> (this directory is on '$here')"
  fi
  [ "$APP_BR" != "main" ] || die "won't release main itself; use a branch"
  [ "$APP_WORK" != "$APP_MAIN" ] && APP_WT=1
  return 0
}
# resolve_card: sets CARD_MAIN, CARD_WORK, CARD_BR, CARD_WT, CARD_CREATE (1 to create release/<version> from main)
resolve_card() {
  CARD_DIR="${CARD_DIR:-$HOME/mnt/powerengine/ha-powerengine-card}"
  [ -d "$CARD_DIR" ] || die "card directory not found: $CARD_DIR (use --card-dir)"
  CARD_DIR="$(cd "$CARD_DIR" && pwd)"
  [ -f "$CARD_DIR/ha-powerengine-card.js" ] || die "$CARD_DIR is not the PowerEngine card repo"
  CARD_MAIN="$(main_checkout "$CARD_DIR")"; CARD_WT=0; CARD_CREATE=0
  if [ -n "$CARD_BRANCH" ]; then
    git -C "$CARD_MAIN" rev-parse --verify -q "refs/heads/$CARD_BRANCH" >/dev/null || die "card branch not found locally: $CARD_BRANCH"
    CARD_BR="$CARD_BRANCH"
    CARD_WORK="$(worktree_of_branch "$CARD_MAIN" "$CARD_BR")"
    if [ -z "$CARD_WORK" ]; then CARD_WORK="$CARD_MAIN"; CARD_NEEDS_CHECKOUT=1; fi
    [ "$CARD_WORK" != "$CARD_MAIN" ] && CARD_WT=1
  else
    CARD_BR="release/$VERSION"; CARD_WORK="$CARD_MAIN"; CARD_CREATE=1
    if git -C "$CARD_MAIN" rev-parse --verify -q "refs/heads/$CARD_BR" >/dev/null; then die "card branch $CARD_BR already exists; pass --card-branch $CARD_BR"; fi
  fi
  [ "$CARD_BR" != "main" ] || die "won't release main itself; use a branch"
  return 0
}

preflight_tree() {       # preflight_tree LABEL DIR: clean working tree, else stop before touching anything
  if ! clean_tree "$2"; then
    if [ "$DRY" = 1 ]; then note "warning: $1 working tree at $2 has uncommitted changes (a real run would stop)"
    else die "$1 working tree at $2 has uncommitted changes; commit or stash them first"; fi
  fi
}

# --- main --------------------------------------------------------------------------------------------------------
main() {
  parse_args "$@"
  TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
  APP_NEEDS_CHECKOUT=0; CARD_NEEDS_CHECKOUT=0
  [ "$DRY" = 1 ] && printf 'DRY RUN: nothing is pushed, written to GitHub, or changed on disk.\n'
  for c in git gh jq python3; do command -v "$c" >/dev/null || die "$c is required"; done

  step "Plan"
  resolve_app
  note "app:  $APP_BR in $APP_WORK ($APP_REPO_SLUG)$([ "$APP_WT" = 1 ] && echo ', a worktree: removed at the end')"
  note "app notes: $APP_NOTES"
  if [ -n "$CARD_NOTES" ]; then
    resolve_card
    note "card: $CARD_BR in $CARD_WORK ($CARD_REPO_SLUG)$([ "$CARD_CREATE" = 1 ] && echo ', new branch from main')$([ "$CARD_WT" = 1 ] && echo ', a worktree: removed at the end')"
    note "card notes: $CARD_NOTES"
  else
    note "card: not released (no --card-notes; it is only released when it changes)"
  fi

  if [ "$DRY" = 0 ]; then
    gh auth status >/dev/null 2>&1 || die "gh is not logged in (gh auth login)"
    if (api GET "/repos/$APP_REPO_SLUG/releases/tags/v$VERSION") >/dev/null 2>&1; then
      die "release v$VERSION already exists in $APP_REPO_SLUG"
    fi
  else
    dry "check gh is logged in, and that release v$VERSION does not exist yet"
  fi
  preflight_tree "app" "$APP_WORK"
  [ "$APP_WORK" = "$APP_MAIN" ] || preflight_tree "app main checkout" "$APP_MAIN"
  if [ -n "$CARD_NOTES" ]; then preflight_tree "card" "$CARD_WORK"; fi

  # versions: each must move forward
  WORK="$APP_WORK"
  local app_now card_now card_ref
  if [ "$APP_NEEDS_CHECKOUT" = 1 ]; then
    app_now="$(git -C "$APP_WORK" show "$APP_BR:apps/powerengine/pe_core/__init__.py" | sed -n 's/^__version__ *= *"\([^"]*\)".*/\1/p')"
  else app_now="$(current_version app "$APP_WORK/apps/powerengine/pe_core/__init__.py")"; fi
  [ -n "$app_now" ] || die "could not read the app's current __version__"
  newer_than "$VERSION" "$app_now" || die "version $VERSION is not newer than the app's current $app_now"
  note "app version: $app_now -> $VERSION"
  if [ -n "$CARD_NOTES" ]; then
    card_ref="$CARD_BR"; [ "$CARD_CREATE" = 1 ] && card_ref="main"
    card_now="$(git -C "$CARD_MAIN" show "$card_ref:ha-powerengine-card.js" 2>/dev/null | sed -n 's/^const CARD_VERSION = "\([^"]*\)".*/\1/p' | head -n 1)"
    [ -n "$card_now" ] || die "could not read the card's current CARD_VERSION"
    newer_than "$VERSION" "$card_now" || die "version $VERSION is not newer than the card's current $card_now"
    note "card version: $card_now -> $VERSION (card versions may skip numbers)"
  fi

  # 1. check everything before anything is pushed (a failing card must not leave a released app behind)
  if [ "$APP_NEEDS_CHECKOUT" = 1 ]; then
    if [ "$DRY" = 1 ]; then dry "git -C $APP_MAIN checkout $APP_BR (it is not checked out anywhere)"
    else git -C "$APP_MAIN" checkout -q "$APP_BR"; fi
  fi
  run_app_checks "$APP_WORK"
  if [ -n "$CARD_NOTES" ]; then
    if [ "$CARD_CREATE" = 1 ]; then
      if [ "$DRY" = 1 ]; then dry "git -C $CARD_MAIN fetch origin main; git checkout -b $CARD_BR origin/main"
      else gitn -C "$CARD_MAIN" fetch -q origin main; git -C "$CARD_MAIN" checkout -q -b "$CARD_BR" origin/main; fi
    elif [ "$CARD_NEEDS_CHECKOUT" = 1 ]; then
      if [ "$DRY" = 1 ]; then dry "git -C $CARD_MAIN checkout $CARD_BR"; else git -C "$CARD_MAIN" checkout -q "$CARD_BR"; fi
    fi
    run_card_checks "$CARD_WORK"
  fi

  # 2. bump versions and changelogs (files only; the commit comes with the PR)
  WORK="$APP_WORK"
  step "App: version and changelog"
  change_file app_version "$APP_WORK/apps/powerengine/pe_core/__init__.py" "$VERSION"
  change_file install "$APP_WORK/docs/INSTALL.md" "$VERSION"
  change_file app_changelog "$APP_WORK/CHANGELOG.md" "$VERSION" "$APP_NOTES"
  if [ -n "$CARD_NOTES" ]; then
    WORK="$CARD_WORK"
    step "Card: version and changelog"
    change_file card_version "$CARD_WORK/ha-powerengine-card.js" "$VERSION"
    change_file card_changelog "$CARD_WORK/CHANGELOG.md" "$VERSION" "$CARD_NOTES"
  fi

  # 3. app: PR, CI, merge, release, then the card
  local title app_title card_title
  title="${TITLE:-$(first_line "$APP_NOTES")}"
  app_title="$VERSION${title:+: $title}"
  release_repo "App" "$APP_REPO_SLUG" "$APP_WORK" "$APP_BR" "$VERSION" "$APP_NOTES" "$app_title" \
    apps/powerengine/pe_core/__init__.py docs/INSTALL.md CHANGELOG.md
  SUMMARY+=("app:  PR $REL_PR  release $REL_URL  merge $REL_SHA")
  if [ -n "$CARD_NOTES" ]; then
    card_title="$VERSION (card)${TITLE:+: $TITLE}"
    [ -n "$TITLE" ] || { t="$(first_line "$CARD_NOTES")"; card_title="$VERSION (card)${t:+: $t}"; }
    release_repo "Card" "$CARD_REPO_SLUG" "$CARD_WORK" "$CARD_BR" "$VERSION" "$CARD_NOTES" "$card_title" \
      ha-powerengine-card.js CHANGELOG.md
    SUMMARY+=("card: PR $REL_PR  release $REL_URL  merge $REL_SHA")
  else
    SUMMARY+=("card: not released (no --card-notes)")
  fi

  # 4. clean up
  cleanup "App" "$APP_WORK" "$APP_MAIN" "$APP_BR" "$APP_WT"
  if [ -n "$CARD_NOTES" ]; then cleanup "Card" "$CARD_WORK" "$CARD_MAIN" "$CARD_BR" "$CARD_WT"; fi

  step "Summary$([ "$DRY" = 1 ] && echo ' (dry run)')"
  printf '    version %s\n' "$VERSION"
  printf '    %s\n' "${SUMMARY[@]}"
}

main "$@"
exit
