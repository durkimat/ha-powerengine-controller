---
name: phase0-generic
description: Implementation status and file-level detail for PowerEngine's Phase 0 "making it generic" work (adapters for inverter/tariff/EV/forecast, neutral names, Solis as a YAML definition, the site section). Use when continuing, reviewing, or asking about Phase 0 / adapter / making-it-generic work on ha-powerengine-controller.
---

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
