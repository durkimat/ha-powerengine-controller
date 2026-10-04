# The site: which plant this home has

`config.yaml` has an optional top-level `site:` section. It names the parts of the home PowerEngine talks to, and the
app builds each adapter by that name (`pe_core/adapters/registry.py`) instead of assuming one brand. The Config page
writes it; a config without one is given today's plant the first time the app starts (see "Migration").

```yaml
site:
  inverter: solis           # a definition in pe_core/adapters/devices/<name>.yml (docs/INVERTERS.md)
  inverter_firmware: null   # text in quotes ("420044") or null: picks the definition's firmware variant
  ev_charger: zappi         # zappi | none
  car: none                 # none (kept for later)
  tariff: auto              # auto | edf | octopus; auto tells them apart by the rate sensor's integration
  forecast: solcast         # solcast | none
  events: axle              # axle | none
```

Unknown keys and unknown names are refused with a message that lists the choices. The choices are not written in
`config.py`: they are what the registry holds (every definition file, every registered adapter) plus `none` (and `auto`
for the tariff), so a new file or adapter appears by itself.

## What "none" does

The part is left out: its readings are empty (no charger power, no forecast, no grid events) and its words in the
app's texts fall back to the neutral ones ("car charger", "forecast", "grid-services"). Leaving a part out is a choice
for the owner; the migration never picks it.

Leaving a part out also stops its inputs being required: with `ev_charger: none` the car charger's and the smart-charge
inputs are not needed, with `forecast: none` the solar forecast inputs, with `events: none` the grid-event inputs
(`config.required_roles`, `left_out_roles`). The inverter and the tariff are always required. The card's Your system panel
(docs/WIZARD.md) sets these for you. One device of each kind is controlled for now; docs/plans/multiple-devices.md is the design for more, and
[Other devices](#other-devices-read-only) below is the first part of it.

## Migration

A real config with no `site` gets one on start: solis, the firmware from the definition's `firmware_entity` if it names
one, else the definition's default firmware, logged as assumed (the SolaX Modbus Solis plugin has no firmware entity, so solis gets "420044"), zappi, none, auto, solcast, axle. It is saved through the normal
save-with-backup path (`store.save_config`, so a `config.yaml.bak-<stamp>` is kept) and logged once at INFO as "Site
added to the configuration: ...". It never runs in demo mode or with no config, does nothing when a `site` exists, and
if the save fails the app carries on with the same choices in memory and warns.

## Changing the site

Send the whole config with the new `site` in the `pe_config_save` event, as any other config change. If `inverter` differs, or
`inverter_firmware` changes so that a different firmware variant applies (null and "420044" are the same for solis), the app switches to Passive whatever was asked, logs and notifies, and
marks the supervised tests as needing a re-run (`retest_required`, kept in `site_state.json` beside the config and
cleared when a supervised RC test passes). Other keys only rebuild their adapters. A save with no `site` key keeps the
saved one.

## Other devices (read only)

From 0.9.93 a config may list `devices:` beyond the main inverter (docs/plans/multiple-devices.md, M1). They are read only:
PowerEngine measures them and counts their solar, and never writes to them or plans their battery.

```yaml
devices:
  - id: garage                # lowercase letters, digits, _ (max 24); "main" is the main inverter
    adapter: solis            # an inverter definition (the same names as site.inverter)
    name: Garage              # optional, shown on the card
    firmware: "420044"        # optional
    control: read_only        # the only value for now
    inputs:                   # only the ones the device reports
      battery_soc: {entity: sensor.garage_battery_soc}
      battery_power: {entity: sensor.garage_battery_power, invert: true}   # + discharging, - charging
      solar_power: {entity: sensor.garage_pv_power}
```

Each mapped input becomes a sensor, `sensor.pe_state_dev_<id>_soc`, `_battery_power` and `_solar_power`, and a device's solar adds to
the total solar. A save that has no `devices` key keeps the saved ones (an older card); an empty list removes them. The card's
"Your system" block has an "Other devices" list to add, edit and remove them (app and card 0.9.93 or newer).

## What the card reads

`sensor.pe_diag_version` attributes: `site` (the current values), `site_options` (per key, a list of
`{id, name, status, firmware_variants}`; inverters also carry `verified_firmware`), `firmware_detected` (what the
inverter reports, or null), `retest_required`, `wizard` (what the Your system card looks for: docs/WIZARD.md) and, only when the config has devices, `devices` (`{id, name, adapter, control, inputs}` each). `status` is `verified`, `community` or `draft`.
