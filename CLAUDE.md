# PowerEngine: notes for Claude

PowerEngine is a Home Assistant energy manager, run as a Home Assistant integration that starts the engine as its own
process (`custom_components/powerengine/`; the engine's source is `engine/`). It plans and controls a home battery
(Solis S5-EH1P6K-L hybrid inverter, 18 kWh battery) against EDF tariffs (smart slots ~6.99p, overnight ~7p, peak
~30p, export 15p), a myenergi Zappi car charger and Solcast forecasts. It publishes its entities as native Home Assistant entities, and a
Lovelace card (`card/`, bundled in the integration and served by it) provides the configuration, test, health, update and
diagnostics UI. The owner is Matthew. This is live on his house: mistakes cost money or inverter wear.

## The engine and the card

The card (`card/ha-powerengine-card.js`, with its tests in `card/tests/`) lives in this repository since 0.9.215 and is
released with it: `tools/build_integration.py` copies it, with its `CARD_VERSION` set to the engine's, into
`custom_components/powerengine/frontend/`, which the integration serves and registers as a Lovelace resource
(`frontend.py`). The old `durkimat/ha-powerengine-card` repository is archived. Pairs that must move together:

- A new setting in `pe_core/config.py` (`SAFETY`, `SETTING_SECTIONS`) needs adding to the card's section lists in
  `ha-powerengine-card.js` (search for a neighbouring key, e.g. `ram_max_power_w`), or it never shows on the config page.
- A new adapter (tariff, charger, forecast, events) needs a file in `engine/pe_core/adapters/integrations/` (its detect entry and its
  entity suggestions: `docs/INVERTERS.md`, "Other integrations"); an inverter definition needs a detect entry for the Your system card (once the setup wizard)
  (the definition's `detect:`); see Phase 1 below.
- `MIN_APP_VERSION` (card) and `MIN_CARD_VERSION` (app, `pe_core/version.py`): raise one in the PR that makes the other
  side need something new, and release both (see Releases).
- A changed state text or attribute on a published sensor (`pe_core/status.py`) can break the card or the dashboard
  (`dashboard.yaml`, and its frozen render in `tests/golden/`): search both repos for the old text.

A cloud session can write code, tests and PRs and start a release through the **Release workflow** (see Releases): the
owner approves each run on GitHub. It can't run `tools/release.sh` itself (it needs the owner's `gh` login).

## Layout

- `engine/powerengine.py`: the engine (large; wiring, control, scheduling).
- `engine/pe_core/`: pure logic, testable without Home Assistant. Planner, optimiser, decisions,
  readings, costs, learning, RAM control, damping, journal, releases, diagnostics and so on.
- `engine/dashboard/dashboard.yaml`: the dashboard. The app writes it to HA on start, so edit it
  here, never in HA.
- `docs/INVERTERS.md`: how to add an inverter as a definition file (`pe_core/adapters/devices/<name>.yaml`).
- `docs/logic/`: the plan and decision logic as flowcharts and tables (inputs, rules planner, optimiser, priorities, control, settings,
  decision log, review findings). Start at `docs/logic/README.md`. A PR that changes planning or `decide` updates the matching page.
- `docs/INSTALL.md`: the install guide. Keep it current: any release that changes setup updates it in the same PR.
- The one-button update and the watchdog are the integration's (0.9.218): `powerengine.update` (`updater.py`) and
  `watchdog.py`, which holds the failsafe the engine hands it as data (`pe_core/failsafe.py`, protocol request
  `failsafe`: service calls built from the inverter adapter, so nothing there names an inverter). There is no package
  file any more; `pe_core/hapackage.py` only retires the one earlier versions wrote.
- `tools/release.sh`: the release (`docs/RELEASING.md`). `tools/diag_summary.py`: summarises a diagnostics export (`--help`).
  `tools/build_demo_pack.py`: rebuilds the demo pack. `tools/sim/`: the simulator (stored days through the real app with other code or
  settings, and a GUI; `tools/sim/README.md`).
- `tests/`: pytest. `tests/test_replay.py` with `tests/replay_harness.py` replays a recorded night through the
  whole app (see "Replay safety net").
- The card is one JS file, `card/ha-powerengine-card.js`, with `card/tests/*.test.cjs` (node --test).

## Checks before any PR

```
tools/check.sh              # ruff + fast tests, about 40 s, one line on success
tools/check.sh --full       # adds the slow whole-app tests and the replay (about 4 min): before a release
tools/check.sh --card       # also node --check and node --test in card/
```

Slow tests are marked `slow` in `tests/conftest.py` (`SLOW_MODULES`); a new whole-app test module goes in that list. CI runs everything.
Main requires the "Lint and tests" check, so CI always starts that job: a change touching only `CLAUDE.md`, `release-notes/`, `docs/plans/`,
`docs/history/` or `docs/RELEASING.md` skips its slow steps (`tools/ci_changes.sh`). Never add a workflow-level `paths-ignore`: the check would
never report and the PR could not merge.

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

Full procedure: `docs/RELEASING.md` (read it when you release). **The single-CI release is the default** (used for 0.9.116): `tools/prepare_release.sh
<version> --notes <file>` puts the version bump on the branch, CI passes on that commit, then `release.yml` (`prepared` on by default; optional
`wait_main_ci`) merges after the owner's Approve and releases, in about a minute plus the wait. The card is part of this
repository, so it is released with the integration. In short (the steps below describe the full flow):

1. Branch from `main`, change with tests, run the checks, re-record the replay only if the change is meant to alter behaviour.
2. Commit the notes as `release-notes/<version>.md` (starts `### Behaviour changes`, plain words). Push the branch; no PR is needed (the script opens or reuses one).
3. `git fetch`, pick the next version after `main`'s `__version__`, tell the owner version, title and notes, and **wait for his yes in chat.**
4. Start `release.yml` (ref `main`) with `actions_run_trigger`; the owner presses **Approve and deploy**. Don't merge or create
   the release by hand. Tests-only or docs-only changes can merge without a release.
5. A cloud session can't run `tools/release.sh` itself (no `gh` login there).

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
  through the integration's own services (`powerengine.update`) or the card (an admin's browser).
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


## Rules that live in the history files

Read the named file before touching that area (they are one-liners here; the detail is there):

- **Publishing** (`docs/history/demo-mode.md`, B1): all entity output goes through `self._get_publisher()` (native entities under the integration, plain states in the demo and tests); never write an entity's state any other way.
- **Demo gate** (C2): in demo mode always call `self.call_service` / `self.fire_event` in `powerengine.py`, never on anything else; a test fails on a new call elsewhere.
- **Re-initialise** (C3): keep `initialize()` and what it sets in `self`; don't set attributes from the host's threads outside it.
- **Demo pack** (C1): numbers, times and flags only; never commit a pack from an unscrubbed source, never put private day files in tests.
- **Names** (`docs/history/phase0-generic.md`, step 6): never hard-code a supplier or device name in user text; use `N(term)` or `<<term>>`.
- **New adapter or definition** (Phase 0 step 8, Phase 1 wizard): needs a detect entry (`adapters/detect.py` or the definition's `detect:`).
- **Dashboard templates** (`docs/history/backlog-and-recent-fixes.md`): never read a key a window may lack (`w.get('manual')`).
- **Card contract** (`docs/history/engine-v2-and-comparison.md`, `phase1-generic-core.md`): sensors and attributes the card reads (`sensor.pe_diag_version` `wizard`, `site_options`, `demo`; the v2 sensors; `sensor.pe_state_engine` `choice` and `sensor.pe_diag_v2_settings` `engines`, which show engine 2.1 beta) must not change shape without the card and a `MIN_*_VERSION` bump.

## Where the history is

Don't read these unless the task needs them; grep rather than read whole.

- `docs/history/backlog-and-recent-fixes.md`: backlog items and the recent-fixes log (0.9.73 to date).
- `docs/history/engine-v2-and-comparison.md`: engine v2, engine pages, forecast snapshots, same-day comparison.
- `docs/history/phase0-generic.md` and `phase1-generic-core.md`: the adapter refactor, site, wizard, Your system, override, devices.
- `docs/history/demo-mode.md`: demo world, pack, gate, direct publishing, commands.
- `docs/plans/` (index `README.md`): designs. `engine-v2.md` is 75 KB: read its status box and the section you need only.
- **Never read whole:** `CHANGELOG.md` (160 KB), `engine/powerengine.py` (4,300 lines), the card's one JS file (7,000 lines).
  Start with `tools/outline.sh` (app) or `tools/outline.sh card` (card): each labelled section with its line range (`--defs` adds the classes and
  methods inside), then read just that range, or Grep. Run checks with `tools/check.sh` (one line on success).
