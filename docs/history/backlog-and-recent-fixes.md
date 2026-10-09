# Backlog and recent fixes

Moved out of CLAUDE.md (Oct 2026) to keep every session's context small. Read this file only when the task touches this area. The rules that must always hold are listed in CLAUDE.md under "Rules that live in the history files".

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
- **Late grid events driven by sun and wind outlook (owner's idea, 7 Oct 2026; not built).** Axle events are run when the grid is short, and the grid
  depends on solar and wind, so a poor-sun, low-wind day makes a late event more likely (and the house is shortest of reserve then). Today's `late_events`
  (0.9.115, `value._late_events`) uses one flat rate (`late_events_per_week` x `late_event_hours`) for every future half hour. Idea: make that probability
  depend on an outlook for solar and wind (low generation, high demand hours, evening peaks), so a dull, still, cold day carries a higher chance and the plan fills
  the battery nearer 100% by itself, while a bright, windy day carries a lower one. Things to settle: (1) **the evidence**: count Axle events in the cost records
  (`v.event == "axle"`) against that day's solar (the Solcast actuals already kept), and find what predicts them; (2) **external data**: national wind and solar
  generation forecasts and grid carbon intensity or demand-flexibility signals (for example the National Energy System Operator's and the Carbon Intensity API's
  public forecasts) as inputs, subject to "don't hammer an external API" (one fetch a few times a day, cached) and the no-admin-token rule; (3) **shape**: a multiplier on
  the per-half-hour probability, learned like the scenario weights, with the flat rate as the fallback when there is no data; (4) **hours of day**: events weighted
  to the evening peak, not uniform; (5) the settings stay: `late_events` switches it all off. Plan doc first: `docs/plans/late-grid-events.md`. The owner also plans to
  try more cautious `late_events_per_week` and `late_event_hours` values and watch the plan.
  **Same outlook, smart-slot certainty (owner's idea, 9 Oct 2026; not built).** How likely an EDF smart slot is to be withdrawn or cut short probably depends
  on the same things as a late event (a short grid, so a poor-sun, low-wind or cold day), more than on the slot history alone. If the outlook above is built, feed
  it into `Certainty` too: a multiplier on the history-based figures (`score` for slots not yet started, `hold` for the later half-hours of a running window,
  added 0.9.132), with the history as the fallback when there is no outlook. Check first whether cancelled and cut-short slots in `SlotTracker` records line up
  with the day's solar and wind. Keep it a chance in the price (`slot_prob`), not a rule.
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

