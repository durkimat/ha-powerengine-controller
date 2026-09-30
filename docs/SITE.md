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

## Migration

A real config with no `site` gets one on start: solis, the firmware from the definition's `firmware_entity` if it names
one (the SolaX Modbus Solis plugin has none, so null), zappi, none, auto, solcast, axle. It is saved through the normal
save-with-backup path (`store.save_config`, so a `config.yaml.bak-<stamp>` is kept) and logged once at INFO as "Site
added to the configuration: ...". It never runs in demo mode or with no config, does nothing when a `site` exists, and
if the save fails the app carries on with the same choices in memory and warns.

## Changing the site

Send the whole config with the new `site` in the `pe_config_save` event, as any other config change. If `inverter` or
`inverter_firmware` differs from the saved site, the app switches to Passive whatever was asked, logs and notifies, and
marks the supervised tests as needing a re-run (`retest_required`, kept in `site_state.json` beside the config and
cleared when a supervised RC test passes). Other keys only rebuild their adapters. A save with no `site` key keeps the
saved one.

## What the card reads

`sensor.pe_diag_version` attributes: `site` (the current values), `site_options` (per key, a list of
`{id, name, status, firmware_variants}`; inverters also carry `verified_firmware`), `firmware_detected` (what the
inverter reports, or null) and `retest_required`. `status` is `verified`, `community` or `draft`.
