# Changelog

Every release lists **Behaviour changes** (anything that changes what PowerEngine does to your system) first.

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
