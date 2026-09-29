# PowerEngine: notes for Claude

PowerEngine is a Home Assistant energy manager, run as an AppDaemon app. It plans and controls a home battery
(Solis S5-EH1P6K-L hybrid inverter, 18 kWh battery) against EDF tariffs (smart slots ~6.99p, overnight ~7p, peak
~30p, export 15p), a myenergi Zappi car charger and Solcast forecasts. It publishes its entities over MQTT, and a
companion Lovelace card (`../ha-powerengine-card`) provides the configuration, test, health, update and
diagnostics UI. The owner is Matthew. This is live on his house: mistakes cost money or inverter wear.

## Layout

- `apps/powerengine/powerengine.py`: the AppDaemon app (large; wiring, control, scheduling).
- `apps/powerengine/pe_core/`: pure logic, testable without Home Assistant. Planner, optimiser, decisions,
  readings, costs, learning, RAM control, damping, journal, releases, diagnostics and so on.
- `apps/powerengine/dashboard/dashboard.lovelace`: the dashboard. The app writes it to HA on start, so edit it
  here, never in HA.
- `docs/INSTALL.md`: the install guide. Keep it current: any release that changes setup updates it in the same PR.
- `docs/ha/powerengine_handover.yaml`: the HA package (handover scripts, update script, watchdog automations).
  The owner installs it into HA; see "HA config" below.
- `tests/`: pytest. `tests/test_replay.py` with `tests/replay_harness.py` replays a recorded night through the
  whole app (see "Replay safety net").
- The card repo has one JS file, `ha-powerengine-card.js`, and `tests/helpers.test.cjs` (node --test).

## Checks before any PR

```
export PATH=$HOME/.local/bin:$PATH
ruff check .                    # controller repo
python3 -m pytest -q            # about 70 s, including the replay
node --test tests/helpers.test.cjs   # card repo, when the card changes
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

Versions of app and card move together (0.9.x). For each release:

1. Branch from `main`. Make the change, with tests.
2. Bump the version:
   - `apps/powerengine/pe_core/__init__.py`: `__version__`.
   - `docs/INSTALL.md`: "Version this guide matches" and "PowerEngine x.y.z starting".
   - card: `CARD_VERSION` in `ha-powerengine-card.js`.
3. Update both `CHANGELOG.md` files:
   - App entry: `## x.y.z (beta)`, then **### Behaviour changes** first ("None." if none), then other headings.
   - Card entry: `## x.y.z`, or "No card changes; version kept in step with the app."
   - The update card shows these sections to the user as "what's new", so write them for the owner, in plain words.
4. Commit with these trailers:
   ```
   Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
   Claude-Session: <the session link given in the conversation>
   ```
5. Push with the credential helper. The token is a file; never print it, echo it, or store it in git config.
   ```
   export TF=$HOME/mnt/dev-secrets/github_token.txt
   HELPER='!f() { echo username=x-access-token; printf "password=%s\n" "$(tr -d "[:space:]" < "$TF")"; }; f'
   git -c credential.helper= -c credential.helper="$HELPER" push -u origin <branch>
   ```
6. Open a PR with the GitHub API (`curl -H "Authorization: Bearer $(tr -d '[:space:]' < $TF)" ...`). PR bodies end
   with `🤖 Generated with [Claude Code](https://claude.com/claude-code)` and the session link.
7. Wait about 100 s, check the check-runs (Lint and tests, HACS validation), then squash-merge.
8. Create the GitHub release `vx.y.z` on both repos (`prerelease: false`, `make_latest: "true"`). The body is that
   version's CHANGELOG section.
9. Run `git checkout main && git pull` in both repos.

Tests-only or docs-only changes can merge without a release.

The owner updates with the **Update** button on the dashboard's Configuration page: it refreshes HACS, installs
both and restarts AppDaemon. PowerEngine also checks GitHub for new versions every 5 minutes.

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
- **HA config:** a git copy lives at `$HOME/mnt/HA/config`, synced by the owner with `./ha-sync.sh pull` /
  `push --apply`. Commit there only right after he says he has pulled. He pushes and applies it.
- **Deleting files:** only when he asks. Put scratch files in `$HOME/mnt/powerengine/_to_delete`.

## Current work: making it generic (Phase 0)

Plan doc: "PowerEngine: making it generic" (Claude Docs). Phase 0 restructures the code behind adapters, with no
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
5. Solcast forecast adapter.
6. Neutral names internally; display names come from adapters.
7. Solis as a definition file (YAML plus a small driver; firmware variants; RAM first).

Each step is a small PR that passes the replay unchanged.
