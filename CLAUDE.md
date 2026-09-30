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
- `docs/INVERTERS.md`: how to add an inverter as a definition file (`pe_core/adapters/devices/<name>.yml`).
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

8. Site section (which plant this home has). **8a done** (branch `phase0/site-8`; byte-identical for his plant, replay and dashboard goldens unchanged); **8b next** (the card's "Your system" block):
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
   - 8b: the card's "Your system" block (reads the attributes above; saves with `pe_config_save`).

Each step is a small PR that passes the replay unchanged.

## Demo mode plan

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
  - Dashboard: with no real config a demo also writes `<save dir>/dashboard.yaml` (plus C2's `demo/` copy). Every view of
    `dashboard.lovelace` starts with `- type: custom:powerengine-demo-card` (full width, no options); the golden fixture
    has exactly those 24 lines more.
