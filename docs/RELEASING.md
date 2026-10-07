# Releasing

Moved out of CLAUDE.md. The release procedure in full.

### Single-CI release (`prepared`): the default (first real use: 0.9.116, 7 Oct 2026, about 13 minutes with `wait_main_ci`)

CI runs once, on the exact commit that is merged, and nothing is re-tested after the owner's Approve. Releases take about as
long as one CI run (about 10 minutes) instead of about 27. A card release works the same way (below).

1. Branch from `main`, change with tests, `tools/check.sh --full`, re-record the replay only if the change is meant to.
2. Write `release-notes/<version>.md` (starts `### Behaviour changes`) and commit it. Merge `origin/main` into the branch.
3. Pick the next version after `main`'s `__version__`; tell the owner the version, title and notes, and wait for his yes in chat.
4. `tools/prepare_release.sh <version> --notes release-notes/<version>.md --title "..."` (git only, no token): it refuses a
   branch behind main or a version that is not newer, bumps `__version__`, `INSTALL.md` and the CHANGELOG, commits and pushes.
5. Open the PR (or let the push do it) and let CI pass **on that commit**. Don't push to the branch after this.
6. Start `release.yml` (ref `main`) with the inputs below (`prepared` is on by default) and, when you want main's own CI to
   pass before the release exists, `wait_main_ci: true` (off by default; it costs about 5 to 11 minutes; a failure leaves main
   merged and no release, fix forward in a new PR). Use `dry_run: true` the first time anything about the workflow changes.
7. The owner presses **Approve and deploy**. The workflow then verifies the bump and that the branch is not behind main,
   checks the PR head is the commit it checked out, waits for CI on it (done already, so no wait), squash-merges pinned to
   that commit, optionally waits for main's CI, and creates the release.

**With a card release** (the card repo, `../ha-powerengine-card`, also changes): do step 4 with
`tools/prepare_release.sh <version> --notes release-notes/<version>.md --card-notes release-notes/<version>-card.md`, run with the
card repo checked out on the card's change branch (it needs `MIN_CARD_VERSION` / `MIN_APP_VERSION` already raised where one side needs
the other). It applies the same checks to the card (not behind its main, version newer) before it edits anything, then bumps
`CARD_VERSION` and the card's CHANGELOG, commits and pushes both repos. Open both PRs and let both CIs pass, then start the workflow with
`prepared: true`, `card_branch` (the card's change branch) and `card_notes` (the card notes file in the app branch). Before anything is
merged the workflow verifies both branches, finds the card's PR and waits for its CI, so a card that cannot be released never follows a
released app; then it merges and releases the app, then the card. With `card_notes` and no `card_branch` it uses the full flow below.
First card use of this path: treat it like the first app one (dry run, then you watching).

Safety: the Approve click is still the only gate; a push after CI makes the PR head differ and the run stops; no CI run on
the merge commit (docs-only paths) is not waited for; the full flow stays available (untick `prepared`) and is used automatically for a card release.
Re-run after a failure: if the merge happened but the release did not, create the release by re-running with the same
inputs only if the script says so; otherwise fix forward.

### The full release (also the only one for a card release)

`.github/workflows/release.yml` runs `tools/release.sh` on GitHub with `RELEASE_TOKEN` (a secret of the `release`
environment, so **every run waits for the owner to press Approve and deploy**). First used for 0.9.87 (3 Oct 2026). A
cloud session does everything except the approval:

1. Branch from `main`, make the change with tests, run the checks (below), re-record the replay if the change is meant to.
2. Commit the notes as `release-notes/<version>.md` on the branch (starts `### Behaviour changes`, plain words; card notes
   in a second file if the card is released). Push the branch. No PR is needed: the script opens one, or reuses an open one.
3. Fetch `main` and pick the next version. Tell the owner the version, title, notes and whether the card is included, and
   **wait for his yes in chat.**
4. Start it with `actions_run_trigger` (`run_workflow`, `release.yml`, ref `main`) and inputs `version`, `title`,
   `app_branch`, `app_notes` (path in the branch), optional `card_branch` and `card_notes`, `dry_run` (`"true"` the first
   time anything about the workflow or the card path is new; nothing is pushed), `skip_replay`, `model_name` (the
   Co-Authored-By name).
5. Ask him to open the run in the Actions tab, click **Review deployments**, tick `release` and press **Approve and
   deploy**. Nothing runs until he does.
6. Watch with `actions_list` (`list_workflow_jobs`) and `subscribe_pr_activity` on the PR it opens (about 10 minutes). The
   script merges and creates the release itself: **don't merge the PR or create the release by hand.** Then check
   `main`'s `__version__`, the CHANGELOG section and `get_latest_release`.

The script always comes from `main`; the code and notes come from the branch. A cloud session can't read the run's log (the
proxy blocks the log host); the run's summary page shows the command and the script's summary, so ask the owner to paste
it if you need it. The notes file stays in `release-notes/` as a record.

### On the owner's machine (fallback)

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
- **Run it detached** (CI takes about 5 minutes, and a device_bash call is killed after 180 s, taking plain `&` or
  `nohup` children with it): `setsid nohup tools/release.sh ... > ../_to_delete/release-x.y.z.log 2>&1 < /dev/null &
  disown`, then `tail` the log in later calls. Always pass `--title "short summary"` (the script prefixes the version itself; including it
  doubles it), because the default title cuts the first notes line mid-word. Pass `--app-dir` and `--card-dir` unless
  the repos live under `$HOME/mnt/powerengine/`. Notes files go in `../_to_delete/` (beside the repos).
- If a PR is already open on the branch (e.g. made by a cloud session), the script reuses it: it pushes the version commit, updates
  the PR's title and body, and carries on from there (it used to fail opening a second PR).
- Auth: the script uses the `gh` CLI (logged in as the owner) and git's credential helper; it never reads or prints a token.
- **When detached runs don't survive** (seen 30 Sep 2026, Cowork session: every background process, including
  setsid, nohup and tmux, was killed when its device_bash call ended, so the script died in the test step): check
  with `(setsid sh -c 'sleep 300' &)` and `ps` in the next call. Then run the release one call at a time with the
  script's own functions: `head -n -2 tools/release.sh > $HOME/work/rel_lib.sh`, then in each call
  `source rel_lib.sh; TMP=$(mktemp -d); DRY=0; WORK=$PWD` and run `change_file app_version|install|app_changelog`,
  `commit_message`, `pr_body`, `gitn push`, `api POST .../pulls`; poll `.../check-runs` in calls of up to 170 s
  (CI took about 6 minutes); `api PUT .../pulls/N/merge` (squash), `api POST .../releases`, then `git checkout main`,
  pull and delete the branch. Run the test suite first in cloud `Bash` (background works there, about 210 s), not
  on the device. Same steps, same commit and release format as the script.
- **A cloud session can't run this script:** its network proxy answers every `api.github.com` call with 403 "No linked
  GitHub account", so `gh` is refused there. Use the Release workflow above, or run it on the device, where
  `gh auth status` is logged in.
- **Check `main` before choosing the version:** the owner may have released since the session started (0.9.72 went
  out while #121 was in progress, so it became 0.9.73). `git fetch`, rebase the branch, rerun the tests, then pick
  the next version. Don't reuse a branch name that already exists on GitHub after a rebase (a stale remote branch
  blocks a plain push and the token can't delete refs); use a new name.
- `release.sh` hard-codes the co-author trailer "Claude Opus 5.5"; set `CLAUDE_MODEL_NAME` to use the model's name,
  and `CLAUDE_SESSION_URL` for the session link (the default points at an old session). Pass `--app-branch` when
  the clone's main checkout is the one on the branch (the script otherwise wants a worktree).
- Never `git clone` with the token in the URL (it lands in `.git/config`); use the credential helper.

Tests-only or docs-only changes can merge without a release.

The owner updates with the **Update** button on the dashboard's Configuration page: it refreshes HACS, installs
both and restarts AppDaemon. PowerEngine also checks GitHub for new versions every 5 minutes.

