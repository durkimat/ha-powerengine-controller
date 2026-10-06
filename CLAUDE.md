# PowerEngine: notes for Claude

PowerEngine is a Home Assistant energy manager, run as an AppDaemon app. It plans and controls a home battery
(Solis S5-EH1P6K-L hybrid inverter, 18 kWh battery) against EDF tariffs (smart slots ~6.99p, overnight ~7p, peak
~30p, export 15p), a myenergi Zappi car charger and Solcast forecasts. It publishes its entities over MQTT, and a
companion Lovelace card (`../ha-powerengine-card`) provides the configuration, test, health, update and
diagnostics UI. The owner is Matthew. This is live on his house: mistakes cost money or inverter wear.

## Two repos

Changes often touch both repos: this one (the app, `durkimat/ha-powerengine-controller`) and the card
(`durkimat/ha-powerengine-card`, one JS file). A session that needs both must have the card cloned **beside this
repo as `../ha-powerengine-card`**, which is where `tools/release.sh` and the card's `CLAUDE.md` look. If it is not
there, say so and stop; don't guess its contents. Pairs that must move together:

- A new setting in `pe_core/config.py` (`SAFETY`, `SETTING_SECTIONS`) needs adding to the card's section lists in
  `ha-powerengine-card.js` (search for a neighbouring key, e.g. `ram_max_power_w`), or it never shows on the config page.
- A new adapter (tariff, charger, forecast, events) or inverter definition needs a detect entry for the Your system card (once the setup wizard)
  (`pe_core/adapters/detect.py`, or the definition's `detect:`); see Phase 1 below.
- `MIN_APP_VERSION` (card) and `MIN_CARD_VERSION` (app, `pe_core/version.py`): raise one in the PR that makes the other
  side need something new, and release both (see Releases).
- A changed state text or attribute on a published sensor (`pe_core/status.py`) can break the card or the dashboard
  (`dashboard.lovelace`, and its frozen render in `tests/golden/`): search both repos for the old text.

A cloud session can write code, tests and PRs and start a release through the **Release workflow** (see Releases): the
owner approves each run on GitHub. It can't run `tools/release.sh` itself (it needs the owner's `gh` login).

## Layout

- `apps/powerengine/powerengine.py`: the AppDaemon app (large; wiring, control, scheduling).
- `apps/powerengine/pe_core/`: pure logic, testable without Home Assistant. Planner, optimiser, decisions,
  readings, costs, learning, RAM control, damping, journal, releases, diagnostics and so on.
- `apps/powerengine/dashboard/dashboard.lovelace`: the dashboard. The app writes it to HA on start, so edit it
  here, never in HA.
- `docs/INVERTERS.md`: how to add an inverter as a definition file (`pe_core/adapters/devices/<name>.yml`).
- `docs/logic/`: the plan and decision logic as flowcharts and tables (inputs, rules planner, optimiser, priorities, control, settings,
  decision log, review findings). Start at `docs/logic/README.md`. A PR that changes planning or `decide` updates the matching page.
- `docs/INSTALL.md`: the install guide. Keep it current: any release that changes setup updates it in the same PR.
- `docs/ha/powerengine_handover.yaml`: the HA package everyone needs (update script, AppDaemon restarts, watchdog); it names no
  other controller. `docs/ha/powerengine_predbat_handover.yaml`: the optional Predbat handover (input_select, two scripts, restart
  Predbat). **PowerEngine installs and updates them itself** (0.9.109): the shipped copies are `apps/powerengine/ha_packages/*.yml`
  (`.yml`, not `.yaml`: AppDaemon loads every `.yaml` under apps/), `docs/ha/*.yaml` must stay identical (a test), each starts with the
  `hapackage.MARKER` line, and `pe_core/hapackage.py` decides. Edit the docs file, then copy it over the shipped one. See "HA config" below.
- `tools/release.sh`: the release (below). `tools/diag_summary.py`: summarises a diagnostics export (below).
  `tools/build_demo_pack.py`: rebuilds the demo pack.
- `tests/`: pytest. `tests/test_replay.py` with `tests/replay_harness.py` replays a recorded night through the
  whole app (see "Replay safety net").
- The card repo has one JS file, `ha-powerengine-card.js`, and `tests/helpers.test.cjs` (node --test).

## Checks before any PR

```
export PATH=$HOME/.local/bin:$PATH
ruff check .                    # controller repo
python3 -m pytest -q            # about 70 s, including the replay
node --check ha-powerengine-card.js && node --test tests/*.test.cjs   # card repo, when the card changes
```

## Replay safety net

`tests/test_replay.py` replays 27-28 Sep (scrubbed fixture `tests/replay/day_2026_09_27.json`) through
`PowerEngine.initialize()` and `_cycle()`, with Home Assistant and the clock faked. It runs on RAM control and on
timed windows, and compares every plan, decision, service call and warning with `tests/replay/expected_*.json`.

- **Refactoring must pass it unchanged.** If it fails, the refactor changed behaviour: fix the refactor, don't
  re-record.
- **An intended behaviour change** re-records with `PE_REPLAY_UPDATE=1 python3 -m pytest tests/test_replay.py`,
  and the PR explains the diff of `tests/replay/expected_*.json`.
- More fixtures can be built from a diagnostics export with `tests/replay/build_fixture.py`, which scrubs
  account numbers, meter IDs and serials. Never commit an unscrubbed export.

## Releases (every user-visible change)

### From a cloud session: the Release workflow (preferred)

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

## Working routines

- **Routine check of a diagnostics export** (Health tab's export): run `tools/diag_summary.py <export.json>` (add
  `--since <ISO>` for a window, `--json` for machine-readable output). It prints one screen: versions, mode, log
  levels, deduplicated warnings, decisions per hour and flip-flops, RAM commands, smart-slot requests and slots, Axle
  events, restarts, attributes above 12 KB, the next 12 plan slots and the write budget. Open the full JSON only for
  what the summary flags.
- Prefer fresh sub-agents per task over resuming long-lived ones.

## Guardrails (don't break these)

- **Never write to Backup or Off-Grid modes, or to any entity with "bump" or "boost" in its name.** The app
  enforces this too.
- **Don't hammer EDF's API**, or any external API.
- **No admin HA token for PowerEngine.** Things that need admin rights (restarting add-ons, HACS actions) go
  through the handover package's scripts or the card (an admin's browser).
- **Leave Active / Passive / Pause to the owner.** Don't change them on his behalf.
- **Inverter writes are precious** (EEPROM wear). The default for this install is RAM remote control (43135 mode,
  43136 charge W, 43129 discharge W, capped at 5000 W; failsafe about 5 min). Any change to control must keep the
  write budget, dampening, read-back checks and the RAM refresh behaviour intact.
- **Attributes of published sensors must stay under 16 KB** (HA's recorder skips larger ones). `_publish_state`
  measures them and warns above 15,000 bytes. The role catalogue is about 14.5 KB.
- **HA config:** a git copy lives at `~/HA/config` on the owner's machine (not in either repo), synced by the owner with `./ha-sync.sh pull` /
  `push --apply`. Commit there only right after he says he has pulled. He pushes and applies it.
- **Never hard-code a supplier or device name in user text** (EDF, Zappi, Solcast, Solis, Axle): use the names map
  (`pe_core/names.py`, `N(term)` or a `<<term>>` placeholder). Stored names (entity ids, topics, keys) never change.
- **A forced charge must never buy grid energy at a dear rate that the plan didn't count.** The plan drops a
  grid-charge that needs no grid energy at a non-cheap price (`_solar_only_charges`, planner.py): with RAM control a
  "charge" is a fixed-power Force charge, and less sun than forecast meant importing at 30.28p (29 Sep 2026). Sentences
  must name the price the decision used (`_car_slot_p`: "tariff still 30.28p" for a planned slot not yet priced).
- **Decisions at a charge target latch for the half-hour** (`_held_at_target`, decide.py). The inverter's SoC reads a
  point lower while charging than while holding, so without it Force charge and Hold alternated every 30 s.
  `Decision.details["reached"]` carries the latch; nothing else may use `details` for other purposes without care.
- **Display wording vs mode keys:** `ModeDecision.label` ("waiting for inputs") is for logs and the summary only;
  `effective == "unconfigured"` stays the key the entity, the card and the code use.
- **Deleting files:** only when he asks. Put scratch files in `../_to_delete` (beside the repos).

## Backlog

- **Manual rates provider (own item; designed 5 Oct 2026, not built).** For homes with no tariff integration: `site.tariff: manual`, a new tariff adapter (`pe_core/adapters/manual.py`,
  registered like `kraken.py`, so `site_choices()` and the Your system picker list it) that builds the `Window` list for today and tomorrow from the config instead of from entities.
  The owner's decisions: rates entered as **bands** (from, to, p/kWh) that expand to 48 half-hour slots, **weekday and weekend patterns from the start**, plus a flat export rate and a
  standing charge; **one provider at a time** (no partial price override of an integration: the plan, cost book and simulator would disagree about what a half-hour cost). Things to get right:
  (1) with `manual` the rate roles (`import_rate_now`, `import_rates_today`, `import_rates_tomorrow`, `export_rate`, `standing_charge`) must drop out of `required_roles`, as `none` does for
  a car charger (`SKIPPED_PART_GROUPS`, `left_out_roles`), or the app sits at "inputs missing"; (2) `Readings.import_rate` is derived from the schedule at `now`; (3) no smart slots, free
  power or supplier events (grid events, Axle, stay a separate part); (4) **cost-book trap**: `tariff.rates_at` classes any cheap half-hour outside the overnight window as a smart slot and
  prices its "standard" rate at the day's peak, so a manual tariff with a second cheap band (an afternoon boost) is misclassified unless the window covers it or the classifier is off when
  the provider has no dispatches; (5) a new adapter needs a detect entry (`adapters/detect.py`: always available) and a card UI for the bands (card repo, so `MIN_CARD_VERSION`/`MIN_APP_VERSION`
  and a joint release). Open: effective-from dates for a rate change, and whether the fixed overnight window also needs a weekday/weekend variant and a second range (it has one range today).
  Plan doc to write first: `docs/plans/tariff-sources.md`. This is the "fixed-rate tariff adapter" in the setup wizard item below.
- **Engine cost comparison on the Costs page (owner's idea, 6 Oct 2026; not built).** Totals only, a rolling week: one row per day with what engine v1, engine
  v2 and the perfect-foresight bound would have cost, the cheapest engine marked, to show which engine is the better one day by day. **Predbat is out of scope**
  (the owner's decision: it can't be replayed fairly without embedding its planner). How: each night (like the tariff simulator) replay yesterday from the cost
  history (actual house, sun, prices, smart slots, grid events: the outside world, which no engine changes) through each engine closed loop, as
  `tools/engine_compare.py` does on the demo days, with the battery and grid simulated; the bound is `engine_v2.value.solve` on the actual day. Things to get right:
  (1) **forecasts as they were**: the cost history holds actuals only, so a replay would give both engines near-perfect foresight; first save daily forecast
  snapshots (sun with bands, the house profile in use, smart slots with when they were announced) and compare only days that have one (a week after that ships);
  (2) **calibration**: show the engine that really ran its simulated cost against its metered cost, so the owner can see how far to trust the other columns;
  (3) **simulator fidelity**: the demo world has no taper, BMS or cold limits and lets surplus sun into the battery on Hold (the real inverter exports it), so
  absolute figures are rough (perhaps 5 to 10%) while the ranking is sturdier; (4) **cost on the HA host**: whole-app replays took about 100 s (v1) and 300 s (v2)
  per day here, slower on a Pi: run once a night, and prefer stepping the pure engines over the whole app; (5) card: a small table on the Costs page (card
  repo, joint release).
- **Zappi Eco+ and solar (monitor):** Eco+ is required for EDF/Octopus smart charging. The owner has only seen the car charge from the grid, not from solar, so some
  threshold (probably on the charger) decides. If the Zappi ever starts and stops with solar surplus, `car_charging` (an urgent rule, no damping) and the plan signature will flip with it:
  watch `ev_state` changes per day in the diagnostics export, and add a short debounce on stop only if it happens. Do nothing until it is seen.
- Release workflow, still to do (the workflow itself is done and used for 0.9.87 to 0.9.104): the card path ran as a **dry run** on 3 Oct, and as a **real** card
  release in 0.9.104 (5 Oct: card branch checked out, card PR pushed, merged, card release created with `card_notes`; it worked). Still untested: a slow CI (the script
  waits up to 12 minutes). The owner chose not to test a red-CI run. `RELEASE_TOKEN` is a fine-grained token with an expiry: when it
  lapses the checkout steps fail with an auth error, and the owner renews it and replaces the secret. Created about 26 Sep 2026
  with a 90-day expiry (the owner can't see the exact date), so it lapses around 25 Dec 2026: ask him to renew it in early December.
  A cloud session's token can't delete branches (403), so ask the owner to delete stale ones.

- Setup wizard, still to do (see docs/WIZARD.md, "Not done yet"): (1) try it on the live HA and check the guessed details: the Solis
  `detect:` manufacturer pattern, the integration domains in `adapters/detect.py` (`solax_modbus`, `octopus_energy`/`edf_energy`,
  `myenergi`, `solcast_solar`, Axle's unknown) and that `hass.entities[].platform` / `hass.devices` are what the card expects; (2) install
  links for myenergi and Axle (left out because the addresses weren't known); (3) a second inverter's `suggest` regexes reaching the
  card (the catalogue holds only the default inverter's, and `map_catalogue` is near its 15 KB limit, so ship them per definition
  some other way); (4) Octopus tariff suggestions and a fixed-rate tariff adapter (Phase 2), since the tariff suggestions are the
  EDF ones; (5) the GitHub issue form that the candidate export attaches to (the wizard links to a plain new issue).
- **Multiple devices: designed (3 Oct), not built** (docs/plans/multiple-devices.md). Owner's decisions: flexible enough for any mix of
  hybrid inverters, solar-only inverters and battery-only units; a setting for which are controlled (a new device starts read only);
  each battery gets its own plan that mirrors the main one with the device's own size, power rates and drive. The design: a device
  is a capability set (`solar`, `battery`, `drive`) in its definition; roles per device as `<id>.<role>` (`main` keeps today's names);
  a `BatteryProfile` per battery; **residual planning in priority order** (not a joint optimiser) with shared grid limits and a shared
  grid-event export; per-device controller, write budget, Active guard and supervised tests; the demo world gains a second device so
  M3 can be tested without hardware. Stages: **M1 device model and read-only devices: done in 0.9.93** (app `config.Device`, `Readings.devices`, sensors
  `sensor.pe_state_dev_<id>_*`, `devices` attribute; card "Other devices" in "Your system", shown for app 0.9.93+; docs/SITE.md), M2 a plan per battery
  shown not executed, M3 control of more than one device (long Passive trial first). Not in M1: the wizard offering a device, a Monitoring tile,
  and a definition for a battery-only unit (`definition.validate` still requires RAM or timed slots). The owner's second inverter is solar-only and stays a read-only
  `solar_plants` entry. Open for M2: default priority, splitting a grid event, planning around a non-autonomous read-only battery.
- Phase 1, still open: low-write mode for EEPROM-only inverters (#189). **Designed; L1 shadow study built** (`docs/plans/low-write-mode.md`;
  evidence from `tools/low_write_study.py` on the demo days: the overnight cycle alone keeps 83-94% of the full plan's benefit for
  about 5 window changes a day; daytime smart-slot top-ups add nothing; value per write falls steeply). Recommended: a write credit
  (token bucket, 10 counted writes/day, 3 days' cap) that sets the optimiser's price per window change, paid events may overdraw,
  overnight-only arbitrage with a band derived from battery size and spread. Stages L1 shadow accounting (any install, no behaviour
  change), L2 credit and price, L3 overnight tier and derived band, L4 card/wizard/docs and a supervised EEPROM trial. Decided
  (owner, 3 Oct): 10 writes/day (4 for unknown brands), 3-day credit, paid events may overdraw, a `daytime_policy` setting
  (`plan` default / `no holds`; hold by day was considered and rejected), shadow on his install first (L1, built in 0.9.91:
  `pe_core/lowwrite.py`, nightly at 02:40, `sensor.pe_diag_lowwrite`, Health tab; `Params.plan_tier`), no EEPROM tester yet. Also decided
  (3 Oct): the balance starts at one day's budget (10 writes) and shows on the Health tab and a Monitoring tile. RAM-control installs are never capped. (Licence, CONTRIBUTING and the step 7
  leftovers are done: see Phase 1 below.)

## Recent fixes

- **PowerEngine keeps its HA package files in step (0.9.109, `pe_core/hapackage.py`, `_package_sync` in `powerengine.py`).** At start (after the config loads) and after a
  save that changes `other_controller`, the app compares `<ha config>/packages/` (the parent of the folder holding config.yaml; never created, never in a demo, skipped with no
  config) with the shipped files: `write` / `update` / `remove` / `keep` / `skip_unmanaged`. Wanted: the main file always, the Predbat file only when `other_controller()` is `predbat`.
  Recognised as ours: the marker line, an older shipped header, or the old combined file (header "PowerEngine handover" plus `battery_handover_to_powerengine`); anything else is
  never touched. Before an update or removal the old file is copied to `<name>.bak-YYYYMMDD` (HA loads only `*.yaml`, so it ignores it). One INFO line per change, one notification,
  failures a warning. `sensor.pe_diag_package` (state `ok` / `reload_needed` / `no_packages_dir` / `unmanaged` / `error`; attributes `files`, `reload_needed`, `message`) is rechecked
  every 30 s cycle from entity presence (`script.powerengine_update`, `input_select.battery_controller`). **Rule:** a change to either package file is shipped by editing both copies, and
  the owner's git copy of HA config must be pulled before he pushes, or the push undoes PowerEngine's file.
- **Other battery controller is optional (0.9.108, `config.other_controller()`, `modes.guard_status`).** System setting `other_controller` (`none` / `predbat` / `other`;
  default `""` = not chosen, derived at read time from the mapped guards: `guard_read_only` containing "predbat" gives `predbat`, any guard `other`, none `unset`; nothing is saved).
  `none`: guards not required, not checked, not evaluated (`_evaluate` skips the handover roles), Active allowed as far as guards go. `unset` with no guard: Active refused with
  `modes.CHOOSE_CONTROLLER`. Published as `other_controller` on `sensor.pe_diag_version`. The owner's personal automations are gone from code, roles and docs; user text names no
  Predbat except where `predbat` is chosen. HA packages split (see Layout). The replay config maps guards, so it derives `predbat` and is unchanged.

- **Overnight window: learned or fixed (built for 0.9.105, not released; `docs/logic/01-inputs.md` 1.10).** New system setting `overnight_window` (`learned` default / `fixed`) and safety settings
  `overnight_start_h` / `overnight_end_h` (hours since midnight in half-hour steps, 23.5 = 23:30; a start after the end runs over midnight). `PowerEngine._overnight()` is the one place the plan
  gets the window (`CostBook.window()` for the cost book, which `_reload` re-values when the window in use changes); `tariff.fixed_window` / `chosen_window` / `describe_window` are the pure
  helpers; `sensor.pe_diag_overnight` (state = window in use; attributes `source`, `learned`, `learned_days`, `fixed`, `fixed_not_valid`) is the readout the card shows under the choice. The plan
  signature now includes the system settings. Default learned = byte-identical (replay unchanged). Card: choice and times sit under "Tariff and planning", readout from the sensor; neither side
  needs a `MIN_*_VERSION` bump (a card without the sensor shows no readout; an app without the settings lists none).
- **The 0.2 kWh "car drew something" figure is gone (0.9.104, F14, `slots.py`, `certainty.py`).** A smart slot now counts as used when the charger reported "charging" without a break for
  at least `car_min_charge_min` (2 minutes; `SlotTracker` keeps `longest_min` per slot, a break resets `run_min`), because blips (the car waking, 15 to 70 s, 0.01 kWh) come in bursts and the
  total would count them. One figure for certainty and the Health tab (`DEFAULT_MIN_CHARGE_MIN`). Learned with `learn_car_min` (on): `slots.learn_min_charge`, from supplier-confirmed slots,
  moved n / (n + 20) of the way after 8, within half to double the setting; the app uses `_min_charge_min()`. Config page: setting and tick box in the car section, now titled "Car and smart
  charging" in the card. The replay did not change. **Observe** `smart_slots.min_charge_min` and `used` / `done_no_car` on the export, and whether certainty moved. Old records have no `longest_min`
  (they fall back to the total, which can only overstate).
- **The plan assumes no car, except the running half-hour while it charges (0.9.103, `forecast.build_slots`, `Readings.ev_state()`).** The planner used to expect the
  car in every future smart slot (battery held, no selling) and guessed otherwise from history (`car_idle`, `ev_complete`, a running dispatch with no draw); a 1-minute 0.01 kWh blip
  made it hold flat at 90% all evening (4 Oct 2026, 0.9.101 patched the threshold, 0.9.103 removed the guess). Now only the state counts: `car_expected` is true only for the running
  half-hour while `ev_state() == "charging"` (`car_kw` = the live draw); the plan assumes the charge ends by the end of that half-hour. `_plan_signature` holds the car's state and, while it
  charges, the half-hour, so start, stop and a charge into the next half-hour each remake the plan. The decide rule "car charging" still holds the battery at once. `SlotTracker.car_idle` stays only
  for the `smart_skip_full_car` setting (off: slots are asked for even with the car full). Replay re-recorded: plan strings, and a sale begins 2 minutes later in the RAM night (15:27, was 15:25)
  and 5 minutes later in the timed-window runs (15:30, was 15:25).

- **The arbitrage band is soft (0.9.102, `docs/plans/soft-band.md`)**: a charge may run `SOFT_BAND_MARGIN` (5) points past `arbitrage_max_soc` and a sale may end 5 under
  `arbitrage_min_soc` outside the overnight window (`optimiser.slot_target`, `sell_floor`), priced by the band penalty, so a cycle uses whole half-hours instead of a
  charge-then-hold sliver. Planner only; the controller follows each slot's target. Replay re-recorded. **Observe** the first days: idle (Hold) half-hours in the plan, levels outside 75-90.
- **A dashboard template must not read a key a window may lack**: HA raises on a missing attribute and the card shows nothing (the Actions list, 0.9.102: use `w.get('manual')`).

- **#121, RAM control and BMS limits. Done in 0.9.73** (`pe_core/bms.py`, `ramcontrol.py`). Optional roles
  `battery_bms_charge_limit` / `battery_bms_discharge_limit` (Solis suggestions) cap commands at limit A x 52 V
  (`BATTERY_VOLTS`; no battery-voltage role, because it pushed `map_catalogue` over 15,000 bytes: it is 14.9 KB now, so
  any new role needs a size check). Limit 0 = Hold on a charge, Off on a discharge. Cold-caution fallback when the charge
  limit is unmapped. The follow check uses the expected power. A command not followed (battery under 20% of it, after the
  3-minute alarm) steps down 1000 W to a floor of 3000 W, for up to 60 minutes. The diagnostics export has a `bms` section
  (in-memory ring, lost on restart). **Not done:** Modbus read-back of 43136/43129 (no entity for it). To watch in the
  first cold spell: the export's `bms` rows against the actual battery power; the conversion assumes battery-side watts.

## Engine pages and same-day comparison (built for 0.9.110)

Plan `docs/plans/engine-pages-and-comparison.md` (section 6: as built). The Plan tab is titled **Engine v1** (path `plan` kept), Plan history
is at its bottom, and the Engine v2 page ends with its own history; both carry `powerengine-engine-badge-card` (Active / Paused / Passive) and
the card's icons `pe:engine-v1` / `pe:engine-v2` (`window.customIcons`).
- **Forecast snapshots** (`pe_core/fcsnap.py`, `<costs>/snapshots/YYYY-MM-DD.json`, 14 days): deltas of the raw states of the roles that
  carry future information, the house profile and smart slots' first-seen times; a full entry at the first cycle of each day. Never in a demo.
- **Cost records** carry `engine` and `live`; `costbook.day_engine(_info)`. v1's "As run" (`_record_ran`) is written only on engine v1.
- **Engine v2 history** (`pe_core/v2history.py`, `<costs>/v2history/`, 30 days; `sensor.pe_v2_history`, event `pe_v2_history_day`).
- **Same-day comparison** (`pe_core/compare/`): nightly at 03:20 the app starts `python -m pe_core.compare.run` as a **subprocess**
  (90-minute limit, one at a time, yesterday plus one missing day of the last week) that replays the whole app in the demo world on
  yesterday's records and snapshot, v1 and v2, self-use and the bound, plus calibration against the meter; results in `<costs>/compare/`,
  `sensor.pe_cost_engines`, the Costs page's "Engines compared, same day". Feature `engine_compare` (on). A day with no snapshot house
  profile by 00:30 is refused (else the replay would know the load). The fake AppDaemon moved to `pe_core/compare/harness.py`
  (`tests/replay_harness.py` and `tools/engine_compare.py` use it). About 3 minutes a day here. **Watch** the first results: the
  calibration gap, `took_s` on the owner's host, and that the first full day after release is compared.

## Current work: engine v2 (built for 0.9.106)

Design `docs/plans/engine-v2.md` (owner's decisions in its status box and section 18), build plan `docs/plans/engine-v2-build.md`.
A second, selectable engine: system setting `engine` (`v1` default, `v2`); v2's own settings are the `engine_v2:` block
(`pe_core/engine_v2/settings.py`; missing keys seeded once from v1's equivalents: arbitrage, events, free power, reserve). New shared
safety setting `battery_floor_soc` (12 %, the BMS's own limit; v2's hard floor, v1 does not read it).

- **Soft top and steady legs (0.9.113, `docs/plans/engine-v2.md` 18a, from the first live evening):** `top_up_cost_p` (2p/kWh) prices grid energy charged
  above `comfort_high_soc` (inside the comfort figure; seeded from v1 `arbitrage_max_soc`; the early charge of a stretch only goes up to the top);
  `reversal_cost_p` (3p) prices charge-to-sale turns in the plan and in `_worth_the_change`; a running leg goes on to its plan step's end when both
  pay (`Executor._leg_going`); "learned" revalues only on a 1% fact or 1 day change (`triggers.learned_moved`). Closed-loop check: `tests/evening_world.py`.
- **How it decides:** `pe_core/engine_v2/` (pure). `forecast.py` cuts the look-ahead into segments at the data's own times (a smart slot's
  real minutes) with low/mid/high sun and house; `value.py` is a stochastic dynamic programme for the value of a stored kWh (smart slots are
  two price outcomes known before the choice), giving `Lines` (value against prices turned into lines after losses), a charge target and an
  expected timeline; `rules.py` one rules core for plan and live (grid event > override > free power > car > reserve; events may go below the
  owner's reserve to the hard floor + 1); `execute.py` the mode automaton (exit conditions, price and level bands, minimum time, deadlines that
  only re-check); `observe.py` filtered battery level and events; `triggers.py` revalue only on events (coalesced; 2 h backstop);
  `learning.py` scenario weights, solar bias, load spread, level offset; `engine.py` `EngineV2.step` never raises; `publish.py` the sensors.
- **Charge early within a cheap stretch:** the DP decides how much (ties not bought); `value.run_target` / `Lines.charge_now` start it at
  the beginning of the stretch at one price, not at its end.
- **In the app:** `_engine_tick` (every `sample_s`, a no-op on v1) steps the engine; the 30 s `_cycle` keeps its bookkeeping and still makes
  v1's plan (a comparison) but does not decide with v2; both engines' decisions go through `_hand_over` (activity log, would-writes,
  `_control`), so RAM control, BMS and fuse caps, following check and write budget are shared. **v2 needs RAM remote control:** on timed
  windows Active is refused ("Engine v2 needs RAM remote control"). State in `engine_v2_state.json` beside the config. Diagnostics export
  section `engine_v2`; `tools/diag_summary.py` prints it.
- **Sensors (the card's contract, `engine-v2-build.md`):** `sensor.pe_state_engine`, `pe_v2_mode`, `pe_v2_value`, `pe_v2_timeline`,
  `pe_v2_value_curve`, `pe_v2_triggers`, `pe_diag_v2`, `pe_diag_v2_settings`. Card: `powerengine-engine-card`, `powerengine-v2-plan-card`,
  `powerengine-v2-health-card` on the dashboard's **Engine v2** page (after Plan); the config card has the engine choice (confirm both ways)
  and Your house / Engine v1 / Engine v2 groups, sending `engine`/`engine_v2` only when the app publishes `pe_diag_v2_settings`.
  `MIN_CARD_VERSION` 0.9.106. The existing pages don't switch with the engine yet (a later release).
- **Comparison:** `tools/engine_compare.py` runs the whole app closed loop in the demo world on the pack's days, v1 against v2, with a
  perfect-foresight bound.
- **Preview (0.9.107):** on engine v1 `_engine_tick` still steps v2 with `Situation.active=False` (reason "Preview: engine v1 is in control") and publishes the v2 sensors (`v2_mode.preview`, `state_engine` attribute `v2_preview`), but never reaches `_hand_over`, `_control`, `_note_command`, the activity log or `self._decision`; setting `preview_when_v1` (default on) turns it off. v1's `_cycle` decides as before.
- **Not done / to watch:** not yet run on a live HA; `sample_s` changes need a restart; `band_exit` may revalue often on real data
  (watch the Health card's causes); the design's offset sign: the learned offset is reported minus true.

## Current work: making it generic (Phase 0)

Plan: `docs/plans/making-it-generic.md` (index: `docs/plans/README.md`; each plan opens with a Status box, and the box is updated in the PR that lands a step). Phase 0 restructures the code behind adapters, with no
behaviour change:

0. Replay safety net. **Done** (PR #152).
1. Adapter interfaces and neutral vocabulary, with no code moved. **Done** (`pe_core/adapters`; built by a Sonnet sub-agent, reviewed).
2. Solis inverter adapter (timed windows, RAM control, tests/clock). Done (2a timed windows, 2b RAM control, 2c tests and clock), pending a night's running.
3. EDF tariff adapter (Kraken rates, smart slots/dispatches, Axle as a grid event). **Done** (branch `tariff-adapter-3`):
   - `pe_core/adapters/kraken.py`: `KrakenTariff("edf" | "octopus")` holds the Octopus-Energy-integration parsing that was in
     `readings.read()` (`read_import_rate/export_rate/standing_charge/rates/dispatches/offpeak/free_sessions`, pure, taking a
     `role -> state` accessor), the `TariffAdapter` wrappers, `display_names()`, `supplier_of(entity_id)` (was inline in the
     simulator context) and the smart-request translation (`ready_by_call`, `charge_target_call`, `ready_by_options`).
   - `pe_core/adapters/axle.py`: `AxleEvents.read_event()` (was in `read()`) and `grid_events()` as export `GridEvent`s. New
     registry kind `"event"`; `GridEventAdapter` protocol in `base.py`.
   - `pe_core/parsing.py`: `Window`, `parse_time`, `parse_windows` and friends moved out of `readings` (still importable
     from there) so adapters need not import `readings`.
   - `read(cfg, get_state, now, tariff=None, events=None)` calls the adapters (defaults built inside); the app builds them
     lazily with `_tariff()` / `_events()`. `SmartCharger` policy (when, back-off, settle, attempts file) stays in the core.
   - Step 6 to-do, EDF-named texts left as they were: `powerengine.py:2132` (log "Asking EDF for smart-charge slots"),
     `pe_core/costs.py:335` (waterfall label "EDF tariff", also the docstring at :297), `dashboard/dashboard.lovelace`
     (lines 265, 315, 928, 932, 983, 1032, 1060, 1111, 1119, 1384, 1394, 1397, 1412, 1414), `pe_core/roles.py` (63, 164, 214,
     217 role descriptions/group; the `suggest` regexes name `edf_energy`, which is discovery, not text), `docs/INSTALL.md`
     (25, 278, 279), and comments/docstrings in `smartcharge.py`, `slots.py`, `certainty.py`, `tariff.py`, `forecast.py`,
     `optimiser.py`, `planner.py`.
4. Zappi EV adapter (plug/charge states, car-full detection, check meter). **Done** (branch `ev-adapter-4`):
   - `pe_core/adapters/myenergi.py`: `ZappiCharger` (`read(state)` for plug/status/mode/session kWh/power, pure
     `classify(plug, power_w)` = the old `ev_state()` logic, `complete(plug_state, status)`, plus the `EVAdapter` protocol and
     `display_names()`); registered as `("ev", "zappi")`.
   - `Readings.ev_state()` delegates to the adapter's `classify` (optional non-compared field `ev`, default module-level Zappi
     adapter, so `Readings(**kw)` still works); new `Readings.ev_complete()` replaces the `"complet" in ev_status` test in
     `forecast.py`. `read(..., ev=None)`; the app builds `_ev()` lazily. `_power_w`/`_energy_kwh` moved to `pe_core/parsing.py`.
   - Left: role `suggest` regexes (step 7), `slots.car_idle`, `gridcheck`. Zappi-named texts for step 6: `pe_core/config.py:126`
     (setting help "32 A Zappi"), `pe_core/roles.py:109` (check meter "e.g. Zappi CT"), `docs/INSTALL.md:25, 278`, and comments in
     `gridcheck.py:1,16` and `checks.py:95`.
5. Solcast forecast adapter. **Done** (branch `forecast-adapter-5`):
   - `pe_core/adapters/solcast.py`: `SolcastForecast` (`attribute = "detailedForecast"`): `points(items)` -> `ForecastPoint`s
     (kWh = pv_estimate x 0.5, optional 10/90 bands; the slot builder's old skip rules), `day_kwh(items)` (was
     `readings.forecast_kwh`, which stays as a wrapper), `read(get_attribute, entity_ids)` (was the loop in the app's
     `_solar_forecast`), and `half_hourly(ha, day)`. Registered as `("forecast", "solcast")`.
   - `build_slots(r, solar, ...)` takes `ForecastPoint`s or raw dicts (raw go through the default adapter); the app's
     `_solar_forecast()` now returns points, via `_forecast()`. `read(..., forecast=None)`. Nothing serialises the solar list.
   - Left: the roles' `attribute="detailedForecast"` and `suggest` regexes (step 7). Step 6 texts naming Solcast:
     `docs/INSTALL.md:25`; the stored config value `solcast_site` (`config.py:43` `FORECAST_SOURCES`) is a key, not text.
6. Neutral names; display names come from the adapters. **Done** (branch `neutral-names-6`; identical text for EDF/Zappi/Solcast/Solis/Axle):
   - **Rule: never hard-code a supplier or device name in user-visible text; use the names map.** Stored or addressed
     things keep their names (entity ids, MQTT topics, attribute/config keys and values, cost-history fields, file names).
   - `pe_core/names.py`: one map of terms (`supplier`, `tariff`, `dispatch`, `dispatch_short`, `smart_charge`, `ev_charger`,
     `forecast`, `inverter`, `event`) built from the adapters' `display_names()` (`build_names`). `N(term)` reads the current map
     (defaults are his words, so unit tests and the replay see EDF/Zappi/...; the app calls `set_current()` via `_names()`).
     `fill(text)` replaces `<<term>>` placeholders and raises on an unknown one. Unknown terms fall back to neutral words
     (`adapters/vocabulary.py`: "your supplier", "smart-charge slot", ...).
   - Python texts use `N(...)` (decide, planner, costs, status, notify, `smartcharge.ask_message`) or `<<term>>` in static
     tables (role help in `roles.py`, setting help in `config.py`), filled when the catalogues are built.
   - Dashboard (`dashboard.lovelace`): user text carries `<<term>>`; `sync_dashboard(..., names=)` fills it after the
     energy-flow splice (`dashboard.render`). `tests/golden/dashboard_edf_zappi_solcast_solis_axle.lovelace` is the frozen
     rendered original; a test keeps the render with his names byte-identical to it.
   - The app publishes the map as attribute `names` on `sensor.pe_diag_version` (small; not `map_catalogue`, which is near the
     15.5 KB limit). The card fills its own placeholders from it (`fillNames`), with neutral fallbacks.
   - Left: comments/docstrings (incl. dated history notes), the `state_axle` entity's display name, the roles' `suggest`
     regexes, `FORECAST_SOURCES`, `docs/INSTALL.md` (his hardware's setup guide), the dashboard's and the simulator's
     EDF-versus-Octopus comparison sentences, and the card's "Forecast: Solcast site" option (names that config value).
7. Solis as a definition file (YAML plus a small driver; firmware variants; RAM first). **Done** (branch `phase0/solis-definition-7`; byte-identical for his S5-EH1P6K-L on firmware 420044):
   - `pe_core/adapters/devices/solis.yml` holds all Solis data: display names, `card_model`, capability flags and limits, the
     RAM remote-control entities/options/limits/tests first, then the timed slots (three, `_2`/`_3` suffix, staged parts, test
     roles), the clock roles, the 29 brand `suggest` regexes per role, and `firmware:` variants (his 420044 is the base; a
     commented `re:^FB` example). **It is `.yml`, not `.yaml`, on purpose:** AppDaemon loads every file ending `.yaml` under
     the apps folder as app config; `test_no_stray_yaml_in_app_folder` allows only `powerengine.yaml` and `devices/*.yml`.
   - `adapters/definition.py` loads, validates (clear `DefinitionError`s naming the missing key) and merges firmware variants
     (`match` = exact version or `re:pattern`; first match wins; mappings merge key by key). It imports nothing else from
     pe_core, so `roles.py` can use it. `adapters/defined.py`: `DefinedInverter(definition, ha, role_entity, ...)` implements
     the whole inverter surface. `adapters/solis.py`: `SolisInverter(DefinedInverter)` keeps the old constructor, class
     `DISPLAY_NAMES`, `card_model` and the registry entry. `registry.py` registers every `devices/*.yml` as an inverter on
     first use (a class registered under the same name wins).
   - Stays in Python, chosen by `behaviour:` (`timed_hhmm`, `override_select`, `drift_button`): the window arithmetic
     (`control.py`, `schedule.py`), the RC command per decision, refresh and following check (`ramcontrol.py`), the RC tests'
     verdicts (`rctest.py`), clock-drift maths (`clock.py`). The neutral role names (`timed_charge_start_hour`, `rc_mode`,
     `storage_mode`) and the RC option words the controller works in (`Off`, `Force charge`, `Force discharge`) are the
     app's vocabulary; a definition maps them to its inverter's own words (applied at the service call and the option check).
   - `roles.py`: the brand `suggest` regexes are gone from it; `roles_for(inverter)` merges them from the definition
     (`ROLES = roles_for()`), so the catalogue is unchanged (`tests/golden/roles_catalogue.json`).
   - `tests/test_solis_definition.py` + `tests/golden/solis_parity.json` (recorded from the hand-written class before the
     refactor) pin every protocol method's output; re-record only for a deliberate behaviour change
     (`PE_PARITY_RECORD=1`). The replay goldens are untouched. See `docs/INVERTERS.md` for the file format.
   - Left: the app still builds `SolisInverter` directly (no inverter setting yet), the RC controller compares option
     words by the app's names, the timed behaviour handles exactly three slots, and `writes_needed` names the update button
     role. Those are the first things a second inverter would generalise.

8. Site section (which plant this home has). **Done.** 8a (controller, branch `phase0/site-8`; byte-identical for his plant, replay and dashboard goldens unchanged) and 8b (the card's "Your system" block, card 0.9.66, PR #147):
   - `config.yaml` gets an optional `site:` (`config.Site`, frozen; keys `inverter`, `inverter_firmware`, `ev_charger`, `car`, `tariff`, `forecast`,
     `events`; unknown keys and names are `ConfigError`s). The valid names come from the registry (`config.site_choices()`: registry names
     plus `none`, and `auto` for the tariff), never from lists in `config.py`. See `docs/SITE.md`.
   - The app builds `_inverter()`, `_ev()`, `_forecast()`, `_events()`, `_tariff()` by name from `cfg.site` (`_adapter()` keeps each with the name it
     was built for and rebuilds on change, so a config reload picks up a new site). `"none"` is a null adapter (`adapters/null.py`: `NoCharger`,
     `NoForecast`, `NoEvents`; reads nothing, neutral words in the names map). Tariff `auto` keeps `supplier_of`. `inverter_firmware` goes to the
     definition loader (`firmware=`), so firmware variants apply.
   - Migration (`_add_site`): a real config with no `site` gets today's plant (solis, firmware from the definition's `firmware_entity` if any, else the definition's default ("420044", logged as assumed), zappi,
     car none, tariff auto, solcast, axle), saved by `store.save_config` (backup kept), one INFO line "Site added to the configuration: ...". Not in
     demo mode, not unconfigured, idempotent; a failed save warns and carries on in memory. Parts are never migrated to `none` (the words in texts
     would change). The replay harness config carries the site instead, so the goldens stay as recorded.
   - Definitions gained `status` (verified | community | draft; solis is verified on `verified_firmware: ["420044"]`) and an optional
     `firmware_entity` ({domain, tail}). The SolaX Modbus Solis plugin exposes no firmware entity, so solis names none.
   - `sensor.pe_diag_version` attributes add `site`, `site_options` (`adapters/options.py`, about 1 KB), `firmware_detected` and `retest_required`.
   - Changing `site.inverter`, or `site.inverter_firmware` so that a different firmware variant applies (null and "420044" are the same for solis), (`_on_save` -> `_site_guard`) switches to Passive, logs, notifies and sets
     `retest_required` (`site_state.json` beside the config; cleared when a supervised RC test passes). The app has no stored RC-test results or
     Active gate, so the flag is what the card reads; the card decides how to show it. A save without `site` keeps the saved one.
   - 8b (card, `siteInfo`, `_renderSite`, `siteNeedsWarning` in `ha-powerengine-card.js`): the "Your system" block reads the attributes above and
     saves `site` with `pe_config_save`. It is hidden, and sends no `site`, when the app publishes no `site_options`, so it needs no
     `MIN_APP_VERSION` bump. Shows the inverter-change warning and confirm, and the `retest_required` banner. Not yet tried on a live HA.

Each step is a small PR that passes the replay unchanged.

## Current work: Phase 1 (generic core)

Plan status box: `docs/plans/making-it-generic.md`. Done so far:

- **Active guard for unverified definitions** (`pe_core/verification.py` `active_refusal(inverter, firmware)`, wired into
  `effective_mode(..., unverified=)` by the app's `_unverified()`). Active is refused (effective Passive, reason "Active
  refused: ...") unless the definition's `status` is `verified` and, when it lists `verified_firmware`, the firmware that applies
  (the site's, else the definition's default) is listed. A definition that won't load is refused too. Not applied in a demo.
  Byte-identical for his Solis on 420044 (replay unchanged). The config keeps saying `active`; only the effective mode is Passive.

- **Setup wizard and candidate export. Written on branch `ccr-6832e975-2wkm5i`, not released** (docs/WIZARD.md; needs card and
  app 0.9.88 or the next free version: `MIN_CARD_VERSION` is already 0.9.88, so release both together). The wizard is the card
  `powerengine-wizard-card` (one JS file, section "setup wizard and candidate export": pure `wizard*` helpers, then the class),
  placed on the Config tab (dashboard and its golden both carry it). The app only says what to look for:
  `pe_core/wizard.py` `wizard_info()`, published as attribute `wizard` of `sensor.pe_diag_version` (about 3.5 KB: keep it, the
  version sensor's attributes share 16 KB). Per part (inverter, tariff, ev_charger, forecast, events; the first two required, the
  rest skippable as `none`): its adapters with how to recognise them (`adapters/detect.py` for the non-inverters, the
  definition's `detect:` block for inverters, validated in `definition.py`), and its role keys (`GROUP_PART` / `ROLE_PART`).
  **Rule: a new adapter or definition needs a detect entry, or the wizard can't find it.** `config.required_roles` now leaves out
  the roles of a part the site sets to `none` (`SKIPPED_PART_GROUPS`, `left_out_roles`; no car charger also drops the
  smart-charge group), which is what makes "skip" work. The card hides the wizard when `wizard` is absent (no `MIN_APP_VERSION`
  bump). The candidate export (`buildCandidateExport` in the card, `pe_core/candidates.py` format and checks,
  `tools/candidates_summary.py`) is scrubbed in the card (long digits to `<n>`, emails and postcodes dropped; firmware keeps its
  digits) and checked again by `candidates.unscrubbed`; never commit an unscrubbed one. **Not yet run on a live HA**: the card uses
  `hass.entities[].platform/device_id` and `hass.devices`, and the Solis `detect:` values and other adapters' integration domains
  (`solax_modbus`, `octopus_energy`/`edf_energy`, `myenergi`, `solcast_solar`, Axle's unknown) are best knowledge. Not done: a second
  inverter's `suggest` regexes reaching the card (the catalogue holds only the default's), Octopus tariff suggestions.
  Since 0.9.89 (card and app): on a configured system the wizard starts folded away and reads what PowerEngine already uses (the
  device holding a saved mapped entity, else the site's name), so EDF stays chosen when the Octopus integration is also there; other
  candidates are listed as "also found"; "Your system" lists them too; the setup checklist card hides itself when everything is in
  place; the Config tab order is demo banner, update, setup checklist, wizard, handover, config. Config page wording now says
  **grid events** (run by `<<event>>`, i.e. Axle) in the role, setting, feature, notification and topic texts (`axle` stays the key).

- **Your system (replaces the setup wizard). E1 built on branch `ccr-b18fa4a5-ve20m1`, not released** (plan: `docs/plans/equipment-manager.md`; user guide:
  docs/WIZARD.md). Card only, plus the dashboard line: `custom:powerengine-wizard-card` became `custom:powerengine-system-card` in `dashboard.lovelace` and its
  golden (the card keeps the old name as an alias for a dashboard the app has not yet rewritten). The Config tab shows the configured equipment read only and a
  **Change your system** panel; changes are a draft (browser local storage) until **Apply to System**, which sends `pe_config_save` with the saved config plus
  only the equipment changes. Removing a required part is blocked (replace only); removing an optional part shows what goes with it. The config card no
  longer edits site, plants or devices. **To release:** card and app together; raise `MIN_CARD_VERSION` (`pe_core/version.py`) to the card's new version, because
  the dashboard now names the new card; the card's release notes (`--card-notes`) say the wizard is gone. The app needs nothing else: `wizard`, `site_options`
  and the save path are unchanged. **Not yet run on a live HA** (same open point as the wizard); the card was driven in Chromium against a fake `hass`.
  Open: the setup checklist's button could open the panel; E2 (per-definition roles, battery-only kind) and E3 (plants become devices) are in the plan.
- **Manual override, O1 app and O2 card built (not released, 0.9.97)** (`pe_core/override.py`, `_on_override` in `powerengine.py`, plan `docs/plans/mode-override.md`;
  card `powerengine-override-card`, section "Manual override" in the card). The owner picks Self-use, Hold ("home": the grid runs the house), Charge or Export, for the
  plan window, N half-hours, until a half-hour time (max 12 h) or permanently. Event `pe_override` `{"action":"set","mode":..., window|slots|until|permanent}` /
  `{"action":"clear"}`, answer `pe_override_result {ok,message}`; sensor `sensor.pe_state_override` (state `none` or the mode; attributes `text`, `until`,
  `set_at`); `override.json` beside the config. In `decide` it sits **below a grid event in progress (the event wins), above the plan**; the reserve stops Export
  and Self-use, Charge holds at the target (2-point latch). Active only (`_on_override` refuses otherwise; the app passes `override=` to `decide` only when
  effective mode is active). Pause, fuse limit, BMS limits and the write budget act as for any decision. O3: the planner makes the plan around it (`Slot.manual`, set by `_mark_manual` for
  the slots from now to the end, skipping grid-event slots; `optimiser._actions` and `planner._default` fix the action, the post-passes and arbitrage skip them; windows carry
  `manual: true`, the plan series a `manual` list, the dashboard a pink "Manual override" band); set, cancel and expiry replan at once (the plan signature
  holds the active override). `MIN_CARD_VERSION` is 0.9.97 because the dashboard names the new card.
- **A Hold does not store surplus solar in the plan** (#175, `planner.step`): on the inverter Hold is Force charge at 0 W, so with spare sun the battery sat idle while Self-use charged
  at the full surplus (4 Oct 2026, from the diagnostics history: Hold with ~1.9 kW surplus averaged +0.19 kW battery, Self-use -1.9 kW). The plan used to charge from surplus in Hold, so its SoC
  path ran ahead of the battery and the optimiser liked Hold in sunny half-hours. Now Hold exports the surplus. Replay re-recorded: the RAM night's decisions and service calls
  are unchanged, only the plan strings; the timed-window runs lose one afternoon export and one decision moves by 10 minutes.
- **A charge target reached early replans (#175, `_early_target` in `powerengine.py`, `pe_core/earlytarget.py`)**: when `_with_plan` would hold at the target ("reached", `details["reached"]`) with
  5 minutes or more left in the half-hour (`MIN_LEFT_MIN`) the app calls `_maybe_replan(force=True)` (no mid-slot stick) and decides again; one look per half-hour (`first_look`). Every look is a
  record in `early_target.json` beside the config (last 300): `outcome` `replanned_changed` / `replanned_still_hold` / `late` / `no_plan` / `error`, `left_min`, `soc`, `target`, `price_p`,
  `next_action`, `next_price_p`, `car_charging`, the new action and reason, and `after` (the decisions that followed in the rest of the half-hour, for churn). The diagnostics export has
  `early_target` (summary + last 60), and `tools/diag_summary.py` prints a line. **Review about 11 Oct 2026** (first released 0.9.99): how often it fires, what it chose, minutes of hold avoided,
  extra energy sold or charged in those minutes (cost records), churn in `after`, and the `late` / `still_hold` cases that still end in a hold. Not done: the same for non-charge holds.
- **Short missing readings are bridged** (`_bridge_data_gap`, `DATA_GAP_GRACE_S` 180 s): a `no_data` decision within 3 minutes of the last real one keeps it.

- **Licence and CONTRIBUTING. Done.** Apache-2.0 in both repos (`LICENSE`, `NOTICE`, README sections; copyright 2026 Matthew Durkin;
  they were MIT before, which stays true for copies already taken). `CONTRIBUTING.md` in each repo: what to send, how to add a
  definition, the PR checks and the replay, the rules the code keeps, safety on other people's hardware.
- **Step 7 "Left" items. Done** (byte-identical for his Solis, replay unchanged): the timed-slot count is a definition value
  (`timed_slots.count`, 1 to 8; `schedule.assign/programmed/desired_state(..., slots=)`), the update button role is a definition value
  (`timed_slots.button_role`, default `timed_update_button`; `writes_needed(..., button_role)`, `schedule.writes_for(..., button_role)`),
  and the remote-control mode the select reads is mapped back to the app's words (`DefinedInverter.app_option`, used by the supervised
  test's "still shows" check). The app reads `slot_count` and `button_role` from `self._inverter()`. Not done: a timed behaviour that
  isn't hour/minute numbers plus a button, and a remote control that isn't a mode select plus two powers (each is a new `behaviour:`).

Open: low-write mode for EEPROM-only inverters (#189), designed in `docs/plans/low-write-mode.md` (L0 study and L1 shadow study done, L2 to L4 to build).

## Demo mode plan

Plan: `docs/plans/demo-and-easier-install.md`. A2, A3 and D1 are parked; the options are in #192.

Goal: PowerEngine runs with no MQTT broker and no real inverter (a demo), and installs more easily.

- **B1, publisher adapter. Done** (`pe_core/adapters/publish.py`): `StatePublisher` (`discover`, `publish`, `preset`,
  `retire`, `retire_all`, `available`) with `MqttPublisher` (exactly the old topics, payloads, QoS 1, retained) and
  `DirectPublisher(set_state, remove_state=None)` (HA states through AppDaemon, same entity ids as MQTT discovery's
  `default_entity_id`). System setting `publisher`: `auto` (MQTT if the AppDaemon MQTT plugin is there, else direct) /
  `mqtt` / `direct`. **Rule: all entity output goes through `self._get_publisher()`; never call `mqtt_publish` or build a
  topic in the app.** Direct-mode gaps: no entity registry (no unique ids, gone when HA restarts until the next publish),
  no retire from HA (marked `unavailable`), and no commands from HA (switches and selects are read-only) until B2.
- **B2, commands in direct mode. Done** (`pe_core/commands.py`, `powerengine.py` `_listen_for_commands` / `_on_command`).
  With MQTT the app never receives a command message: HA's switch publishes to the retained command topic, the state
  changes, and the app's `listen_state` handlers (pause, guards, history) react. Direct mode does the same by setting the
  entity's state itself: `_on_command(key, value)` validates, then `publisher.preset`. Direct mode only (no listeners in
  MQTT mode) hears HA's `call_service` event (`switch.turn_on/turn_off/toggle`, `select.select_option/select_next/
  select_previous`, `number.set_value`; `service_data.entity_id` as string, comma string or list; other entities ignored) and
  the card's fallback event **`pe_command`**: `{"entity_id": "switch.pe_ctl_pause", "value": "ON"}` (`value`: "ON" / "OFF" /
  "toggle" for a switch, one of the select's options, a number). Fire it from the card with
  `hass.connection.sendMessagePromise({type: "fire_event", event_type: "pe_command", event_data: {...}})` (admin only)
  when HA refuses a service call because the domain has no platform. Invalid values are ignored.
- **C1, demo data pack. Done** (`apps/powerengine/demo/pack.json`, about 19 KB, inside the app folder HACS installs;
  pure reader `pe_core/demo/pack.py`: `load_pack`, `days`, `day_at`, `row_at`, `smart_slots`, `axle_events`). Four
  recorded, scrubbed days, picked by rule from the owner's cost records: `sunny` (most solar), `dull` (least solar),
  `axle` (largest grid-services export; its title is `<<event>> event day`, fill it with `names.fill`) and `car` (most
  car kWh); a day that wins two rules gives the later rule its next best. It is September data: no "winter" names.
  Per half-hour: house, car, solar, a derived solar forecast (recorded solar smoothed over 2 h, scaled per day within
  10%), rates (act, std, ovn, exp, standing), recorded SoC at the start, smart-slot, axle and free flags, and a small
  `as_recorded` grid/battery section. Times are offsets from local midnight (Europe/London); `day_at` maps wall-clock
  times, so a spring-forward date has 46 rows and an autumn one 50 (the repeated hour plays twice).
  - **Rebuild:** `python3 tools/build_demo_pack.py <config>/powerengine/costs [--glob "2026-09-*.json"]`. It prints
    the chosen days and their totals and writes the pack. Only complete days count (48 consecutive records from local
    midnight, at least 95% coverage, no duplicate starts).
  - **Scrub rule:** the pack holds numbers, times and flags only. The builder refuses (exit 2) any string in a source
    record except `start`, `source: "history"`, `fv` (a rates tag, dropped) and `v.event` (`axle` or `free_power`), so an
    entity id, account number, MPAN, serial or site id can never be copied in. Never commit a pack from an unscrubbed
    source, never put private day files in tests (they use synthetic days), and don't loosen the whitelist without
    looking at what the new string is.
- **C2, the demo world. Done** (`pe_core/demo/world.py`, `gate.py`, `demo/config.template`, the `_demo_*` methods in
  `powerengine.py`). **Start it** with `demo: sunny` (or `dull`, `axle`, `car`) in the app's apps.yaml entry, and restart
  AppDaemon; remove the line to go back. The app then runs on a simulated home: `DemoWorld` (18 kWh battery, 95% each
  way, 5 kW, 12% floor, remote-control failsafe after 5 min) moves one pack day onto today, and the real Solis/Kraken/
  Zappi/Solcast/Axle adapters read its `demo_*` entities unmodified (`world.IDS` lists them; RC discovery finds the
  `battery_control_override` ones). The settings are a fresh copy of `demo/config.template` in `<config>/powerengine/demo/`
  (the folder is wiped at each demo start; nothing else there or in `<config>/powerengine/` is touched; not `.yaml`, or
  AppDaemon would load it as app config). Publishing is direct. Off: smart-charge requests, tariff simulator, cold-battery
  weather, the GitHub release check. `sensor.pe_diag_version` carries `attributes.demo` (`day`, `title` with the names
  filled, `note`) for C3's banner.
  - **The gate rule: in demo mode every call to Home Assistant goes through `DemoGate`.** The app replaces its own
    `get_state`, `get_history`, `call_service`, `set_state` and `fire_event` on the instance: service calls go only to the
    world (what it refuses, such as notifications and logbook entries, is dropped and logged once); `set_state` only for
    `pe_` entities; `fire_event` only the card's answers; `get_state` sees demo entities and PowerEngine's own `pe_`
    entities, nothing else (no `zone.home`). So **always call `self.call_service` / `self.fire_event` in `powerengine.py`;
    never call them on anything else** (an adapter's `ha`, `hass.Hass...`). `test_no_direct_call_to_home_assistant_bypasses_
    the_gate` reads the source and fails on a new call anywhere else.
- **C3, controller side. Done** (`_on_demo`, `_saved_demo_day`, `_wipe` in `powerengine.py`; `tests/test_demo_control.py`).
  The card's contract:
  - `sensor.pe_diag_version` attributes: `setup` is `"unconfigured"` (no real config.yaml) or `"configured"`; `demo` is
    `null`, or `{"day", "title" (names filled), "days": [{"key","title"}, ...all pack days], "note": "Recorded data from a
    real home. Nothing is controlled."}`. An unconfigured app publishes the sensor (direct publishing when there is no MQTT).
  - Event **`pe_demo`** (admin, over the websocket): `{"action":"start","day":"sunny"}`, `{"action":"day","day":"dull"}`,
    `{"action":"exit"}`. Answer event **`pe_demo_result`**: `{"ok": true|false, "message": "<plain words>"}`. Only that
    event name is handled. `start` is refused (`ok:false`) when a real config.yaml exists and no demo is running, so a demo
    never takes over a real system. Bad days, unknown actions and `day` with no demo running are refused. The app arg
    `demo` still wins (then start/day/exit are refused, with a message).
  - `start`/`day` write `<save dir>/demo.json` `{"day": ...}` and re-initialise; `exit` removes it and re-initialises
    (unconfigured, or back to the real config). A saved demo.json is ignored when a real config exists. Demo settings are
    discarded because the config copy is remade on every start.
  - **Re-initialise = `initialize()` again, after `_wipe()`** (no `restart_app`). From the first `initialize()` the app
    records every attribute it sets (`__setattr__` into `_touched`) and every timer/listener handle (instance wrappers on
    `run_in/run_every/run_daily/listen_event/listen_state`). `_wipe()` runs `terminate()`, cancels the handles (skipping
    `run_in` timers already due) and deletes the attributes (this also puts the real `get_state` etc. back).
    **Rule: keep `initialize()` and what it sets in `self`; don't set attributes from AppDaemon threads outside it.**
    `test_no_duplicate_timers_or_listeners_after_start_day_exit_start` compares the live handle set with the fresh one.
  - **Handles can be Tasks.** AppDaemon 4.5 returns an asyncio Task/Future (not the handle string) from `run_every`,
    `run_in`, `listen_state` etc. when called on its event loop; 4.4 returns the string. `_track` records the handle
    inside a finished Task, or records it when a pending one finishes (and cancels it at once if a wipe came first).
  - **Direct publishing and AppDaemon 4.5's `set_state`.** Its REST write cleans the payload: `true` becomes "true",
    and `false`, `null` and every 0 are dropped from attributes (series lose their zeros and shift; config booleans
    arrive as text). So `_lossless_set_state` posts attributes with such values to HA's states endpoint itself
    (`_rest_states_poster`, `pe_` entities only, so the gate rule holds) and `check_existence=False` stops the
    "Entity not found" warnings on creation (only passed where `set_state` names it: 4.4 would make it an attribute).
  - **The demo's clock is the pack's zone** (Europe/London), not AppDaemon's `time_zone` (often UTC on a fresh install,
    which put the recorded day an hour out), and the app's `tz` is set to it while a demo runs.
  - Dashboard: with no real config a demo also writes `<save dir>/dashboard.yaml` (plus C2's `demo/` copy). Every view of
    `dashboard.lovelace` starts with `- type: custom:powerengine-demo-card` (full width, no options); the golden fixture
    has exactly those 24 lines more.
