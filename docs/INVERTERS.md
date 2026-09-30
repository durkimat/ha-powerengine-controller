# Adding an inverter

PowerEngine describes each inverter in a small YAML file and drives it with one generic driver. Support for another
inverter should mostly be a new file, not new Python. Today the Solis S5-EH1P6K-L (through the SolaX Modbus
integration, firmware 420044) is the only one; its file is `apps/powerengine/pe_core/adapters/devices/solis.yml` and is
the reference: copy it and change it.

**Name the file `<name>.yml`, not `.yaml`.** AppDaemon loads every `.yaml` under the apps folder as an app
configuration; `.yml` it leaves alone. The file ships with the app (it sits inside `apps/powerengine`, which is what
HACS installs). It is picked up by name: `registry.get("inverter", "<name>")`.

## What is data and what is code

The file holds facts about the inverter. The few things that are algorithms stay in Python; the file chooses them by
name with `behaviour:`:

| Section | Behaviour | What it does |
| --- | --- | --- |
| `ram` | `override_select` | A mode select plus force-charge and force-discharge power numbers (Solis: SolaX Modbus "Battery control override"). One command per decision, re-sent every minute. |
| `timed_slots` | `timed_hhmm` | Three charge and three discharge windows set as hour and minute numbers, applied with an update button. |
| `clock` | `drift_button` | A clock sensor whose drift is measured, and a button that syncs it. |

An unknown behaviour is refused when the file loads. A new behaviour is a small piece of Python (see
`pe_core/ramcontrol.py`, `control.py`, `schedule.py`, `clock.py`), then a new name here.

## The file

```yaml
definition: 1                 # the file format version
name: solis
brand: Solis
model: S5-EH1P6K-L (SolaX Modbus)
display_names: {inverter: Solis}     # words for the app's own text (the <<inverter>> name)
card_model: solis             # the Sunsynk Power Flow Card's "inverter: model:" key
status: verified              # verified | community | draft (default draft): shown in the card's "Your system"
verified_firmware: ["420044"] # the firmware versions the supervised tests were run on
# firmware_entity: {domain: sensor, tail: firmware_version}   # optional: where the inverter reports its firmware

capabilities:
  supports_ram: true          # RAM remote control: the preferred method, listed first
  supports_timed_slots: true  # the timed windows: the second method
  max_charge_w: 6000
  max_discharge_w: 6000
  actions: [grid_charge, hold, force_discharge, export, self_use]
  never_touch: [Backup, Off-Grid]      # options the app must never select
```

**`ram`** (needed when `supports_ram`): `behaviour`, `storage`, `failsafe_min` (minutes until the inverter drops the
command by itself), `max_power_w`, `lookup_minutes`, `prefer` (an entity id containing this wins when several match),
`entities` (for `rc_mode`, `rc_charge_power`, `rc_discharge_power`: the `domain` and how the entity id ends, `tail`),
`power_roles`, `options` (the app's words `Off`, `Force charge`, `Force discharge` on the left, the inverter select's
own words on the right), and `tests` (which option each supervised test forces).

**`timed_slots`** (needed when `supports_timed_slots`): `behaviour`, `count` (three), `suffix` (slot n's entity is
slot 1's plus this, `{n}` being the number), `first_slot_roles` (the roles that have one entity per slot),
`currents`, `self_use_option` (the `storage_mode` option that gives plain Self-Use), `write_only_match` (roles
containing this are never read, the button), `staged_parts` (role names containing these are kept in Home Assistant
until the button sends them, so setting them is not an inverter write), `recheck_seconds` and `test_roles`.

**`clock`** (optional): `behaviour`, `clock_role`, `sync_role`.

**`roles`**: for each role the config card offers, the suggested entity as regular expressions on the entity id
(`suggest`, and `suggest_not` to exclude). They are added to the role catalogue for this inverter
(`roles.roles_for("<name>")`); suggestions that belong to no brand (tariff, car charger, forecast) stay in `roles.py`.

## Firmware variants

Different firmware can behave differently. A `firmware:` section overrides keys for one version:

```yaml
firmware:
  default: "420044"           # assumed when the app isn't told the firmware
  variants:
    - match: "420044"         # the version exactly as the inverter reports it
      note: S5-EH1P6K-L as installed. The base definition applies unchanged.
      override: {}
    - match: "re:^FB"         # or "re:" and a regular expression
      note: FB00 and later
      override:
        ram: {max_power_w: 6000}
```

The first variant that matches wins. Its `override` is merged into the definition: mappings merge key by key, lists
and values are replaced. The merged result is validated again, so an override can't break the file.

## Checking a new file

`load_definition("<name>")` validates the file and names anything missing (`solis.yml: missing 'ram.failsafe_min'`).
Then copy `tests/test_solis_definition.py`: its parity test pins every output of the inverter for a spread of inputs, and
its "another inverter is only a YAML file" test shows the shape of a second definition. Test on the real inverter with
the config card's supervised tests before letting PowerEngine drive it.

## What is not generalised yet

The app builds the inverter the config's `site.inverter` names (see docs/SITE.md). The remote-control controller
works in the words `Off`, `Force charge`, `Force discharge` and the definition maps them to the inverter's own at the
service call, so an inverter whose remote control is not a mode select plus two powers needs a new behaviour. The timed
behaviour handles exactly three slots.
