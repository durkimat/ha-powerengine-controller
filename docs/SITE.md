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

## Switches to run during free power

List the switches that should run while EDF's free electricity is on offer under `free_power_loads` in `config.yaml`:

```yaml
free_power_loads:
  - entity: switch.immersion_heater
    name: Immersion heater        # optional, for the log
```

PowerEngine turns each one on when a free-power session starts and off when it ends, in Active mode only. A switch that
was already on is left alone, and a switch you turn off yourself during the session stays off until the next one.
Only `switch` and `input_boolean` entities are accepted. What PowerEngine has switched on is kept in
`free_loads.json` next to `config.yaml`, so a restart in the middle of a session still turns it off afterwards.
The Config page edits the same list under Free-power sessions, "Switches to run during free power" (card 0.9.192 or newer); an older card keeps the list when it saves.

## Notifications: channels and rules

Every message PowerEngine sends goes through one router (0.9.195). A **channel** is a place a message can go; the
**rules** say which kinds of message go to which channels. In `config.yaml`:

```yaml
notifications:
  channels:
    bell:                          # Home Assistant's notification area; each one clears itself when the problem is over
      type: bell
    phone:
      type: notify                 # a notify service, e.g. the companion app
      service: notify.mobile_app_my_phone
    alexa:
      type: alexa                  # Alexa Media Player speakers; a group or "everywhere" works too
      services: [notify.alexa_media_kitchen]
      intro: Daddy Bot here.       # put at the start of every message to this channel (optional)
      hours: {weekday_from: "07:30", weekend_from: "09:00", until: "21:00"}   # optional: when it may be used
      outside_hours: hold          # hold (send when the hours open, marked as held) or skip
    family:
      type: whatsapp               # needs a WhatsApp integration with whatsapp.send_message (FaserF's ha-whatsapp)
      targets: [120363012345678901@g.us]   # group ids (whatsapp.search_groups) or phone numbers with country code
      hours: {until: "21:00"}
  rules:
    - {events: [health, inputs, axle, free_power, simulator], to: [bell]}
    - {events: [grid_reminder, grid_summary], to: [alexa, family]}
  reminder_hours: [8, 5, 2, 1, 0]  # when the grid event reminders go, in hours before it starts
```

The kinds PowerEngine sends are `health`, `inputs`, `axle` (grid event scheduled), `free_power`, `daily`, `simulator`,
`grid_reminder` and `grid_summary`. A kind no rule names is not sent. The speakers and chats get one text (the reminders
and summary are written to be spoken); the bell and a phone get a title and a text. A channel with `hours` is used only
between them (the weekend start defaults to the weekday one, `until` to midnight). A message outside them is **held**
and sent when they open, with "Held message from 10:30 pm yesterday." after the opening words, one message per channel
each time round; or **skipped**. A held message is dropped if it is no longer worth sending (a reminder once the event
has started; the others after 12 to 24 hours). One channel failing never stops the others. The Config page edits all of
this under Notifications and announcements (card 0.9.195 or newer): a table of what goes where, each channel, a test
message for each, and the last messages sent. `sensor.pe_ui_notify` carries the same for the card.

The older `service`, `events` and `alexa` (with `whatsapp`, `whatsapp_targets`, `finish`, `hours_before`) settings still
load and are turned into the same channels and rules, so nothing changes until the Config page saves. They cannot be
mixed with `channels` and `rules`. WhatsApp may ban an account that sends automated messages, so keep the volume low.

### Messages from elsewhere

Anything in Home Assistant (the family dashboard's apps, an automation) can send through the same rules by firing the
event `pe_notify`:

```yaml
event: pe_notify
event_data:
  kind: bins                 # lowercase letters, digits and underscores; the rules are written against it
  text: Bins out tonight.    # required
  title: Bins                # optional (the default is the kind)
  speech: Black bin out tonight.   # optional: what the speakers say instead of "title. text"
  chat: "*Bins*: black bin out tonight."   # optional: what a chat gets
  key: bins:2026-11-05       # optional: the same key is sent once
  source: family             # optional: groups the kind on the Config page
  label: Bin collections     # optional: the name on the Config page
  hold_hours: 6              # optional: how long a held message stays worth sending (default 12)
```

A kind PowerEngine has not seen before is listed on the Config page after its first message, so a rule can be made
for it (until then it goes nowhere). Messages from elsewhere are limited to 30 a day.

## What the card reads

`sensor.pe_diag_version` attributes: `site` (the current values), `site_options` (per key, a list of
`{id, name, status, firmware_variants}`; inverters also carry `verified_firmware`), `firmware_detected` (what the
inverter reports, or null), `retest_required`, `wizard` (what the Your system card looks for: docs/WIZARD.md) and, only when the config has devices, `devices` (`{id, name, adapter, control, inputs}` each). `status` is `verified`, `community` or `draft`.
