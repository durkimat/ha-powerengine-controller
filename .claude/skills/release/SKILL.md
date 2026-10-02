---
name: release
description: Release PowerEngine (and the card, if it changed) with tools/release.sh — branching and version/minimum-version rules. Use when cutting a release or a version bump for ha-powerengine-controller or ha-powerengine-card.
---

## Releases (every user-visible change)

Branch from `main`, make the change with tests, commit, then one command does the rest:

```
tools/release.sh <version> --app-notes <file> [--card-notes <file>] [--app-branch <b>] [--card-branch <b>] \
                 [--app-dir <d>] [--card-dir <d>] [--skip-checks] [--skip-replay] [--dry-run]
```

- Run it from the branch's worktree (or pass `--app-branch`). `--dry-run` shows every step and file change and pushes
  nothing; do that first. The owner does the first real run of anything new.
- It runs the checks (ruff, pytest, the replay), bumps `__version__` and both places in `docs/INSTALL.md`, adds the
  `## x.y.z (beta)` CHANGELOG section from the notes file, commits with the trailers, pushes (credential helper), opens
  the PR, polls CI every 20 s (up to 12 min; it never merges a red PR), squash-merges, creates the GitHub release
  `vx.y.z` (`prerelease: false`, `make_latest: "true"`, body = the notes) and cleans up (`git checkout main && git pull`,
  worktree and local branch removed). It prints PR numbers, release URLs and merge SHAs.
- App notes start with **### Behaviour changes** ("None." if none), then other headings. The update card shows them to
  the owner as "what's new", so write them in plain words. `--card-notes` is the same for the card's CHANGELOG.
- **The card is released only when it changes** (give `--card-notes`; its changelog takes `## x.y.z`, `CARD_VERSION`
  moves to that version). No `--card-notes`: the card repo is not touched. Versions stay in one sequence: a card
  release takes the app version it ships with, so the card may go from 0.9.70 to 0.9.74.
- **Minimum versions, not lockstep.** The card has `MIN_APP_VERSION` (oldest app it works with; 0.9.69, which added the
  `demo_days` attribute). The app publishes `min_card_version` on `sensor.pe_diag_version` (`pe_core/version.py`
  `MIN_CARD_VERSION`, 0.9.70). Each side warns only when the other is older than its minimum, not when they differ
  (card: `versionWarnings`). Raise a minimum in the PR that makes one side need something the other only newer
  versions have, and release both.
- **Run it in the foreground** (CI takes about 5 minutes; use a long Bash timeout, or `run_in_background` and wait for
  the notification). Always pass `--title "x.y.z: short summary"`, because the default title cuts the first notes line
  mid-word. Notes files go in `~/Projects/powerengine/_to_delete/`.
- Tokens: the script gets the token from `gh auth token` (run `gh auth login` once), only as a curl header or through
  git's credential helper. Never print it.
- **Check `main` before choosing the version:** the owner may have released since the session started (0.9.72 went
  out while #121 was in progress, so it became 0.9.73). `git fetch`, rebase the branch, rerun the tests, then pick
  the next version. Don't reuse a branch name that already exists on GitHub after a rebase (a stale remote branch
  blocks a plain push and the token can't delete refs); use a new name.
- `release.sh` hard-codes the co-author trailer "Claude Opus 5.5"; set `CLAUDE_MODEL_NAME` to use the model's name,
  and `CLAUDE_SESSION_URL` for the session link (the default points at an old session). Pass `--app-branch` when
  the clone's main checkout is the one on the branch (the script otherwise wants a worktree).
- Never `git clone` with the token in the URL (it lands in `.git/config`); use the credential helper.

Tests-only or docs-only changes can merge without a release.
