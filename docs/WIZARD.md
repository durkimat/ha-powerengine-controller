# Your system, device discovery and the candidate export

> The step-by-step setup wizard was replaced by the **Your system** card (card version after 0.9.93; plan:
> docs/plans/equipment-manager.md). This file keeps its name because the data the app publishes for it, and the candidate export,
> are unchanged.

**Your system** is a card on the dashboard's **Config** tab (`custom:powerengine-system-card`; the old
`powerengine-wizard-card` name still works as an alias). It lists the equipment PowerEngine uses (the inverter and battery, the
tariff, the car charger, the solar forecast, grid events, extra solar plants and other devices) and nothing on it is editable.
**Change your system** opens a panel where a new or an existing install adds, edits, replaces and removes equipment. It never
turns control on: Active, Passive and Pause stay the owner's, and Passive stays until the supervised tests have passed (INSTALL.md).

## The panel

- **Add** has four steps. *What is it?* (the parts the app offers, a solar-only inverter or panels, another inverter or battery),
  *Find it* (the devices Home Assistant has for it, with the one in use marked, a search box, and "choose its type by hand" or
  "send us its entity list" when nothing matches), *Which entities?* (each input filled from the picked device's own entities,
  with its live value, the sign check and an Invert tick, and for an inverter the firmware and a does-it-add-up check), and
  *Review* (what will happen in plain words).
- **Edit** opens at the entities step. **Replace** is for the required parts (inverter, tariff), which cannot be removed.
- **Remove** (optional parts, extra plants, other devices) says what goes with it first: the features that switch off, the inputs
  that stop being used, and what planning loses. A removed optional part is saved as `none` in `site:` (docs/SITE.md, "What none
  does"), and its inputs stop being required.
- Changes are a **draft** until **Apply to System**. **Save draft** keeps them in the browser (local storage, tied to the saved
  system, so a draft of a system that has since changed is dropped); closing with unsaved edits asks Save draft, Discard changes
  or Keep editing. **Apply to System** shows a summary and sends the saved configuration with only the equipment changed (a backup
  is kept). Changing the inverter or its firmware variant switches to Passive and sets `retest_required`, as before. Applying
  never turns a feature on, and switches off only the features that need a part that was removed. An optional part that was
  never set up is saved as `none`.
- **More than one of something.** PowerEngine controls one inverter with its battery. Extra solar sources are read-only *solar
  plants* (counted in total solar and the energy-flow card; each needs a power and a today's-energy entity and has a forecast
  choice) and other inverters or batteries are read-only *devices* (app 0.9.93 or newer; docs/plans/multiple-devices.md).

## What the app provides

Only the card can see Home Assistant's entity and device registries, so the card does the finding. The app tells it what to
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
