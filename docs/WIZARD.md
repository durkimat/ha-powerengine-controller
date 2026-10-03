# The setup wizard and the candidate export

The wizard is a card on the dashboard's **Config** tab (`custom:powerengine-wizard-card`, card 0.9.88 or newer). It gets a new
home to a working **Passive** install without editing YAML: it says what you need first, finds your devices in Home
Assistant, fills PowerEngine's inputs from them, checks the signs against what you can see, and saves. It never turns
control on: Passive stays until the supervised tests have passed (see INSTALL.md).

## The five steps

1. **What you'll need.** Each part of a home (inverter and battery, tariff, car charger, solar forecast, grid events),
   marked **Required** or **Optional**, with whether the wizard found it in Home Assistant: found, installed in HACS but
   not added yet, or not found, with a link to install it. Required parts (inverter, tariff) must exist before the
   wizard can finish. Optional parts have an "I don't have this / skip it" tick, ticked by default when nothing was
   found. A skipped part is saved as `none` in `site:` and PowerEngine leaves it out (docs/SITE.md, "What none does");
   its inputs stop being required. The page also lists what to have to hand (battery size and rates, tariff details,
   an admin login).
2. **Your devices.** For each part that is in use, pick the device: Home Assistant's own devices that match, listed first (the one in use is marked).
   "My device isn't listed" opens the candidate export (below). The inverter's definition status (verified, community or
   draft) is shown, and its firmware is chosen here.
3. **Map inputs.** Each input is filled from the picked device's own entities, with the live value beside it. The picker is
   narrowed to that device; a tick shows all entities (a separate CT clamp, say). An input with no match is left empty and
   marked, never guessed. A suggestion that had to come from another device says so. Optional inputs and the
   controls needed later to go live are in a folded list. The wizard will not move on while a required input is empty.
4. **Live checks.** You say what the battery and the house are doing now (charging, discharging, importing, exporting); the
   card compares that with the sign of the mapped sensor and offers **Invert**. It also checks that house load is about
   grid + solar + battery. Advice only.
5. **Review and save.** What will be saved, with anything switched off because its inputs aren't set (smart-charge
   optimisation, grid events and free-power sessions are turned off when their inputs or their part is missing; the wizard
   never turns a feature on). Saved through the normal `pe_config_save` path (a backup is kept), in Passive.
   Changing the inverter or its firmware variant on an already configured system asks for confirmation and sets
   `retest_required`, as the "Your system" block does.

On a system that is already set up the wizard starts folded away (open it from its heading), reads the saved configuration, and
shows what PowerEngine **already uses** for each part ("In use now: EDF on Electricity meter"). The device PowerEngine uses is
the one holding an entity mapped in the saved config, so a home with both the EDF and the Octopus integrations is shown as EDF.
Anything else Home Assistant has for that part is listed as "Also found, not used by PowerEngine", and picking it is a choice
you make with **Change this part**. Only the ticked parts are asked about; the others are left exactly as they are.

**More than one of something.** Every matching device is offered, with the one in use marked. PowerEngine controls one inverter
with its battery, and you choose which; see docs/plans/multiple-devices.md for what more would take.

**Solar plants.** A second solar-only inverter, or plug-in panels, is a read-only *solar plant*: counted in total solar and drawn on
the energy-flow card, never controlled. The wizard shows the plants already configured ("Counted now: Main, Fox solar"), does not list
a configured plant's device as "also found", and offers other solar-looking devices (a power and an energy sensor, named like a solar
source) to add as a plant, guessing its power and today's-energy entities; each plant needs both, and has a forecast choice (none,
from the forecast service, or scaled from the main plant). On a configured system tick **Change solar plants** to add or remove one
without touching the other parts. A hybrid inverter with a battery that no adapter owns is listed as "not supported yet", with the
entity-list export.

## What the app provides

Only the card can see Home Assistant's entity and device registries, so the wizard runs there. The app tells it what to
look for, as the `wizard` attribute of `sensor.pe_diag_version` (about 3.5 KB; `pe_core/wizard.py`):

```
{"v": 1, "parts": [{"part": "inverter", "title", "why", "required": true, "skip": "none" (optional parts only),
                    "options": [{"id": "solis", "integration": {"name", "url"?}, "domains": [...],
                                 "manufacturers": [...], "models": [...], "entities": [...]}, ...],
                    "roles": [role keys the part's inputs are]}, ...]}
```

- The **options** are the adapters the registry holds. Detection data for an inverter is its definition's `detect:` block
  (docs/INVERTERS.md); for the tariff, car charger, forecast and grid-event adapters it is the table in
  `pe_core/adapters/detect.py`. `domains` are integration domains (the entity registry's platform); `manufacturers`,
  `models` and `entities` are regular expressions. Any one match counts as a find. Adding a definition file adds an
  option; the card needs no change.
- **Roles per part** come from the role catalogue's groups (`wizard.GROUP_PART`, with a few exceptions in `ROLE_PART`).
  Handover guards belong to no part: they stay on the config page.
- The app does not read the `wizard` attribute back. It changes nothing about control.

An app that publishes no `wizard` makes the card hide itself, so the card needs no `MIN_APP_VERSION` bump.

### Skipped parts are not required

`config.required_roles` leaves out the roles of any part the site sets to `none`: no car charger drops the car charger's
inputs and the smart-charge group (smart-charge slots are for the car), no forecast drops the solar forecast inputs, no grid-event
provider drops the event inputs. (Before this, a home with no car charger could never get out of "inputs missing".)
The inverter and the tariff are always required.

## The candidate export

For hardware PowerEngine has no definition for yet (or to attach to a support request), the wizard writes a **candidate
entities** file from the picked device: **Download candidate entities**, **Copy**, and a link to open a GitHub issue to
attach it to. It works before PowerEngine is configured and sends nothing anywhere by itself.

`pe_core/candidates.py` defines the format (`powerengine-candidates`, version 1) and checks a received file;
`tools/candidates_summary.py <file>` prints the problems, then a readable list of each device and its entities:

```
tools/candidates_summary.py powerengine-candidates-2026-10-03.json
```

It holds, for the chosen device: integration, manufacturer, model, firmware, and every entity with its domain, unit, device
class, state class, current value (at most 60 characters), the options of a select, the range of a number, and the names
(only) of other attributes. That is what a definition needs: which entity is which role, what the remote-control select's
options are called, and the limits of the number entities.

**Scrubbing.** In the card: long digit runs (6 or more: meter and account numbers, serials) in entity ids and states become
`<n>`; text that looks like an email address or a postcode is dropped; the note is limited to 300 characters. A device's
manufacturer, model and firmware keep their digits (`420044` is what matters). The tool refuses a file that still has
something it shouldn't (`candidates.unscrubbed`), and tells you not to commit it. Never commit an unscrubbed file.

## Writing a definition from an export

1. `tools/candidates_summary.py file.json` and read it.
2. Copy `pe_core/adapters/devices/solis.yml` and follow docs/INVERTERS.md: `roles:` suggestions from the entity list,
   `ram:` / `timed_slots:` entities and the select's option words, limits from the number entities, and a `detect:` block so
   the wizard finds the device. Leave `status: draft`.
3. The wizard then offers it for that integration. The owner of the hardware runs it in Passive, then the supervised
   tests, before it moves to `community` and `verified`.

## Not done yet

- **A second inverter's suggestions.** The role catalogue holds the default inverter's (Solis's) suggested entities;
  another definition's `roles:` suggestions are not yet shipped to the card, so a second brand's inputs are not
  pre-filled until the app publishes them per definition (a size question: `map_catalogue` is near 15 KB).
- **The tariff for Octopus homes.** The tariff suggestions are the EDF ones (`edf_energy` entity names); a fixed-rate
  tariff adapter and Octopus suggestions are Phase 2.
- The wizard has not been run on a live Home Assistant. It uses `hass.entities` (each entity's `platform` and `device_id`)
  and `hass.devices` (manufacturer, model, firmware), and falls back to entity-id patterns when the frontend gives no registry.
  The Solis `detect:` manufacturer and integration domain (`solax_modbus`) and the other adapters' integration domains are
  best knowledge, to be checked against a real install.
