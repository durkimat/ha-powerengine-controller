# Changelog

Every release lists **Behaviour changes** (anything that changes what PowerEngine does to your system) first.

## 0.3.4 (beta)

### Behaviour changes
- None to your devices: Passive-only.

### Fixed
- Logbook entries failed with `got multiple values for argument 'domain'` (AppDaemon reserves `domain`). A test now
  guards the logbook call's arguments.

## 0.3.3 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- House-load history is now read from HA **one day at a time** in the background (values only). A busy power sensor
  logs ~15,000 readings a day, and asking for 14 days at once returned nothing ("0 house readings").
- Logbook entries are written without an entity reference (AppDaemon turned it into a target, which the logbook
  service rejected with `invalid_format`).

### Fixed
- Plan tab: costs read "this plan earns £0.52 … plain self-use costs £0.00" instead of "£-0.52 vs £0.0".
- No more "Excessive time spent in callback" warning while loading history.

## 0.3.2 (beta)

### Behaviour changes
- None to your devices: Passive-only.

### Fixed
- Load history failed to load when an input had no readings in the period (typically the car, if it hasn't
  charged recently): the error `zip() argument 2 is longer than argument 1` discarded the house-load history too.
  The load forecast now learns from HA history as intended.

## 0.3.1 (beta)

### Behaviour changes
- None to your devices: Passive-only.
- Until a load profile exists, the planner assumes a steady 500 W house load instead of the live house power
  (a momentary spike was being planned as a 36-hour load).
- PowerEngine keeps its own half-hourly record of house-only load in `/homeassistant/powerengine/load_history.json`
  (15 days), so the load forecast learns even if HA history can't be read, and survives restarts.

### Fixed
- Plan tab: the chart no longer overlaps the actions table.
- Reading load history from HA now copes with every shape AppDaemon returns, logs what it found, and retries hourly
  on failure.
- Plan tab warns while the load forecast is still learning.

## 0.3.0 (beta): "Plan"

### Behaviour changes
- None to your devices: still Passive-only.
- PowerEngine now **plans the next 24–48 hours** and the live decision follows the plan (live overrides first:
  Axle event now, free power now, car charging now). The fixed pre-Axle rule is replaced by the plan.
- New entities: `sensor.pe_plan` (full plan in attributes), `sensor.pe_plan_headline`, `sensor.pe_plan_next_mode`,
  `sensor.pe_plan_next_start`, `sensor.pe_plan_next_target_soc`, and `sensor.pe_state_sim_soc` (simulated battery).
- On start and daily at 00:10, PowerEngine reads 14 days of house-load history (and car power) from HA.

### Added
- Forecasts: half-hourly import prices (estimated from the previous day beyond published prices), Solcast solar,
  and a learned house-load profile (weekday/weekend, recency-weighted, car removed).
- Explainable planner: defaults per half-hour, forward battery simulation, then the cheapest earlier slot is chosen
  to fix each avoidable shortfall. Multi-hour Axle events are handled as a need to be met (top-ups worth doing at
  any price below £1/kWh). Every window has a reason.
- Simulated battery (Passive): the SoC PowerEngine would have produced, re-synced at midnight; shown next to the
  real battery on the Monitoring tab.
- Dashboard **Plan** tab: headline, 36-hour chart, actions table with reasons, expected cost vs plain self-use.

### Docs
- Install guide: ApexCharts Card is now needed (Step 5.1); Plan tab described; new troubleshooting rows
  (including HACS forgetting the repositories).

## 0.2.0 (beta): "Decide"

### Behaviour changes
- None to your devices: still Passive-only.
- PowerEngine now **decides** every 30 seconds what it would do, and why. The status sentence starts with it,
  e.g. *"PASSIVE. Would grid-charge to 100%: import is cheap (6.99p ≤ 10p)."*
- Each change of decision is added to an **Activity** log (last 20, kept across restarts) and to the HA logbook.
- New entities: `sensor.pe_state_decision` (self_use / grid_charge / hold / force_discharge) and `sensor.pe_state_activity`.
- *House power* only subtracts the car when **House load includes the car charger** is ticked (default: on).

### Added
- Rule stack, highest priority first: Axle event active → keep charge for an upcoming Axle event →
  free-power session → car charging (never let the battery charge the car) → cheap import → minimum reserve → self-use.
- Charge hysteresis so decisions don't flap near the target.
- Settings: minimum reserve, cheap-import threshold, grid-charge target, restart margin, Axle look-ahead and margin,
  house-load-includes-car.
- Dashboard: Decision tile, Activity list, and a **Config** tab (the config card now lives here).

### Docs
- Install guide: the config card is on the dashboard's Config tab (no separate dashboard); the dashboard step is now
  Step 5 with a copy-paste terminal method; Step 6 covers the new settings.

## 0.1.0 (beta): "See"

### Behaviour changes
- None to your devices: still Passive-only.
- PowerEngine now reads every mapped input each 30 seconds and publishes a normalised picture of the
  system: 13 new `sensor.pe_state_*` entities (status sentence, battery, grid, solar, house, car, rates,
  smart charge, Axle, free power). Power is in W with fixed signs: battery + = discharging, grid + = importing.
- The car's charging power is subtracted from the house load, so *House power* is the house only.
- PowerEngine writes its dashboard to `/homeassistant/powerengine/dashboard.yaml` on start.

### Added
- Status sentence, e.g. *"PASSIVE (monitoring only). Battery 71%, discharging 1.0 kW. Solar 0.1 kW,
  house 1.2 kW, exporting 0.2 kW. Import 30.28p, 6.99p from 21:00; export 15p. Next smart slot 21:00."*
- PowerEngine dashboard (Monitoring view): status, mode, battery, energy flow, rates, car, smart charge,
  Axle, free power, solar forecast, 24-hour history, health.

### Docs
- Install guide Step 7: register the dashboard (one-off) and install Power Flow Card Plus.

## 0.0.4 (beta)

### Behaviour changes
- None to your devices: still Passive-only.
- PowerEngine now reads `config.yaml` written by the config card, checks every mapped input
  (exists, right domain and unit, available, recently updated) and stays **unconfigured**
  until all required inputs are OK.
- New diagnostic entities: `sensor.pe_map_config` (mapping status and per-input checks) and
  `sensor.pe_map_catalogue` (the input catalogue the card uses).

### Added
- Input catalogue: every input's description, units, sign convention and suggested entity.
- Saving from the config card: validated, written atomically, previous version backed up
  (last 10 kept), logged in the HA logbook with who saved and what changed.
- Hard rule: control outputs can never be bump/boost entities.
- Features section in `config.yaml` (smart charge, arbitrage, Axle, free power).

### Docs
- Full installation guide (`docs/INSTALL.md`), including Step 6: configure PowerEngine.

## 0.0.3 (beta)

### Behaviour changes
- None to your devices: this build is Passive-only and never controls anything.
- New: PowerEngine now creates a **PowerEngine** device in HA (via MQTT) with five entities:
  `sensor.pe_diag_version`, `sensor.pe_diag_heartbeat`, `binary_sensor.pe_diag_config_ok`,
  `sensor.pe_cfg_operation_mode` and `sensor.pe_state_operation_mode`.

### Added
- Operation mode (`operation.mode: passive | active`, default passive). Active is refused by this build.
- Optional additional solar plants (`solar_plants:` list) in `config.yaml`.
- `remove_entities: true` removes every PowerEngine entity.

### Changed
- `operation.dry_run` replaced by `operation.mode`.

### Setup
- Requires AppDaemon's MQTT plugin (see README, "MQTT (one-off)").

## 0.0.2 (beta)

### Behaviour changes
- None. Still a scaffold that never controls anything.

### Fixed
- 0.0.1 read its own AppDaemon definition file as the settings file (AppDaemon passes every app a `config_path` argument). The optional override is now called `settings_file`, and unknown top-level keys in `config.yaml` are rejected.

### Docs
- README: AppDaemon add-on setup (`app_dir`) so it loads apps installed by HACS.

## 0.0.1 (beta)

### Behaviour changes
- None. Scaffold only: loads and validates `config.yaml` and logs the result. Never controls anything.

### Added
- Repository structure, HACS metadata, CI (lint, tests, HACS validation).
