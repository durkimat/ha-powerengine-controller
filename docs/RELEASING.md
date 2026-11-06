# Releasing

The release procedure in full. PowerEngine is one thing to release: the integration, the engine it starts, the dashboard
card (`card/`, bundled into `custom_components/powerengine/frontend/`) and the dashboard all go out in one version.

### Single-CI release (`prepared`): the default

CI runs once, on the exact commit that is merged, and nothing is re-tested after the owner's Approve. A release takes about
as long as one CI run (about 10 minutes).

1. Branch from `main`, change with tests, `tools/check.sh --full --card`, re-record the replay only if the change is meant to.
2. Write `release-notes/<version>.md` (starts `### Behaviour changes`, plain words; a new file for every version: never
   overwrite an old one) and commit it. Merge `origin/main` into the branch.
3. Pick the next version after `main`'s `__version__`; tell the owner the version, title and notes, and wait for his yes in chat.
4. `tools/prepare_release.sh <version> --notes release-notes/<version>.md --title "..."` (git only, no token): it refuses a
   branch behind main or a version that is not newer, bumps `__version__`, `INSTALL.md` and the CHANGELOG, brings
   `custom_components/powerengine` up to date (`tools/build_integration.py --sync`: the engine copy, the card with its
   `CARD_VERSION`, the manifest version), commits and pushes.
5. Open the PR and let CI pass **on that commit** (three checks: Lint and tests, HACS validation, Integration against Home
   Assistant). Don't push to the branch after this.
6. Start `release.yml` (ref `main`) with the inputs below (`prepared` is on by default) and, when you want main's own CI to
   pass before the release exists, `wait_main_ci: true` (off by default; it costs about 5 to 11 minutes; a failure leaves
   main merged and no release, fix forward in a new PR). Use `dry_run: true` the first time anything about the workflow changes.
7. The owner presses **Approve and deploy**, or has allowed the session to approve a release he asked for in chat, with
   `gh api -X POST repos/<repo>/actions/runs/<id>/pending_deployments` once CI is green on that exact commit. The workflow
   then verifies the bump and that the branch is not behind main, checks the PR head is the commit it checked out, waits for
   CI on it (already done), squash-merges pinned to that commit, optionally waits for main's CI, and creates the release.

Inputs: `version`, `title`, `app_branch`, `app_notes` (path in the branch), `dry_run`, `prepared`, `wait_main_ci`,
`skip_replay`, `model_name` (the Co-Authored-By name).

Safety: the Approve click is still the only gate; a push after CI makes the PR head differ and the run stops; no CI run on
the merge commit (docs-only paths) is not waited for. The full flow stays available (untick `prepared`). If the merge
happened but the release did not, create the release by re-running with the same inputs only if the script says so;
otherwise fix forward.

### Installing a release on a Home Assistant

HACS shows the release as an update; `powerengine.update` (the card's **Update** button) asks HACS, installs and waits for
the engine. The engine restarts by itself within a minute. A release that changes the integration's own Python (not only
the engine, the card or the dashboard) needs a Home Assistant restart: the release notes say so.

### The full release

`.github/workflows/release.yml` runs `tools/release.sh` on GitHub with `RELEASE_TOKEN` (a secret of the `release`
environment, so **every run waits for the owner to press Approve and deploy**). Untick `prepared` and the script itself
runs the checks (ruff, pytest, the card's node tests, the replay), bumps the version, commits, pushes, opens the PR (or
reuses an open one), polls CI, squash-merges and creates the release. **Don't merge the PR or create the release by
hand.** The script always comes from `main`; the code and notes come from the branch. A cloud session can't read the run's
log (the proxy blocks the log host); the run's summary page shows the command and the script's summary.

### On the owner's machine (fallback)

Branch from `main`, make the change with tests, commit, then:

```
tools/release.sh <version> --app-notes <file> [--app-branch <b>] [--app-dir <d>] [--title <text>] \
                 [--skip-checks] [--skip-replay] [--dry-run] [--prepared [--wait-main-ci]]
```

- Run it from the branch's worktree (or pass `--app-branch`). `--dry-run` shows every step and file change and pushes
  nothing; do that first.
- It runs the checks, bumps `__version__` and both places in `docs/INSTALL.md`, adds the `## x.y.z (beta)` CHANGELOG
  section from the notes file, brings the integration up to date, commits with the trailers, pushes (credential helper),
  opens the PR, polls CI every 20 s (up to 12 min; it never merges a red PR), squash-merges, creates the GitHub release
  `vx.y.z` (`prerelease: false`, `make_latest: "true"`, body = the notes) and cleans up. It prints the PR number, release
  URL and merge SHA.
- Notes start with **### Behaviour changes** ("None." if none), then other headings. The update card shows them to the
  owner as "what's new", so write them in plain words.
- Always pass `--title "short summary"` (the script prefixes the version itself), because the default title cuts the
  first notes line mid-word.
- `release.sh` uses the co-author trailer "Claude Opus 5.5" unless `CLAUDE_MODEL_NAME` is set, and `CLAUDE_SESSION_URL` for
  the session link. Pass `--app-branch` when the clone's main checkout is the one on the branch.
- Auth: the script uses the `gh` CLI and git's credential helper; it never reads or prints a token. Never `git clone` with a
  token in the URL (it lands in `.git/config`).
- **Check `main` before choosing the version:** the owner may have released since the session started. `git fetch`, rebase,
  rerun the tests, then pick the next version. Don't reuse a branch name that already exists on GitHub after a rebase.
- **A cloud session can't run this script** (its proxy refuses `api.github.com`): use the Release workflow, or run it on the
  device, where `gh auth status` is logged in.

### Card and engine versions

The card is bundled with the engine and always has its version, so there is no card release, no version to keep in step and
no minimum-version check between them. A browser tab left open across an update still runs the old card: reload it.

Tests-only or docs-only changes can merge without a release.
