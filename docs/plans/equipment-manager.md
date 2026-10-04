# Equipment manager (replaces the setup wizard)

> **Status (4 Oct 2026).** Design only; nothing built. Agreed with the owner in outline: the setup wizard goes, and the config
> page gets one **Your system** section: a read-only list of what is configured, and a **Change your system** button that opens a panel
> where equipment is added, changed, replaced and removed, for a new or an existing install. A click-through mock-up was made
> for review (not in the repo). The wizard's pure helpers and tests carry over; the app side needs no change for phase 1.

## Why

The wizard (`powerengine-wizard-card`), the "Your system" block, its "also found" list, the solar plants box and "Other devices"
all describe the same thing (what equipment PowerEngine uses) in five places, each with its own editing controls. Once
the wizard is folded away on a configured system it is mostly a second copy of "Your system". The multiple-devices plan
(`multiple-devices.md`) needs one place to add a device of any kind, so this is that place.

## What the page shows

**Your system** (one section, no wizard card above it):

- **Summary list**, read only, grouped: inverter and battery, solar, car charger, tariff, forecast, grid events, other devices.
  Each row: name, model, status badge (verified / community / draft), a live value. A system with nothing configured says so,
  and the setup checklist's button opens the panel.
- **Change your system** button. Nothing else on the page edits equipment, so there is one way to do each thing.
- Settings that are not equipment (features, limits, tariffs' numbers, inputs of the mapped parts) stay where they are.

## The panel

A full-size overlay inside the card (own element: fixed position, backdrop, Esc, focus kept inside; not `ha-dialog`, an HA
internal that changes between releases). It lists the same rows with **Edit** and **Remove**, and **+ Add** at the top.

### Add and Edit: four steps

1. **What is it?** Tiles: inverter with battery, solar-only inverter, battery-only unit, car charger, tariff, forecast, grid
   events. Each maps to capabilities (`solar`, `battery`, `drive`, `multiple-devices.md`) or to an adapter part.
2. **Find it.** What Home Assistant has for that kind (`wizardCandidates`, `wizardOthers`): manufacturer, model, integration;
   a search box; anything in use is greyed and says where. No match: pick the brand by hand, or **Not listed** (the candidate
   export and a link to a new GitHub issue, as the wizard has now).
3. **Which entities?** One row per input the kind needs, pre-filled by `wizardSuggest`, with the live value, the sign check
   (`wizardSignCheck`) and an Invert toggle. A solar-only device asks for two inputs, not six. Edit opens here.
4. **Review.** Name, control (a new device always starts read only), and what PowerEngine will do with it in plain words.

### Editing, draft, applied

Equipment changes go through three plainly named states:

1. **Editing**: inside the panel. The last step of Add says **Add to list**, of Edit or Replace **Done**, and the Remove confirmation **Mark for removal**; each puts the change in the working copy and returns to the list.  Each row carries a tag (*new*, *changed*, *will be removed*) and any change can be undone.
2. **Draft**: **Save draft** keeps the edits in the card and closes the panel. They are not live. The page shows a banner
   ("Draft: 3 changes, not applied to your system") with **Edit draft**, **Discard draft** (asks first) and **Apply to System**.
3. **Applied**: **Apply to System** shows a summary ("Add garage battery; remove solar plant 2; change tariff") and any mode
   warning, then writes the configuration. The draft is cleared.

Closing the panel (Close, Esc, the backdrop) with unsaved edits asks: **Save draft**, **Discard changes** or **Keep editing**. With
no edits it just closes. The word "Save" alone is not used for equipment, so it cannot be confused with the settings' own Save.

The draft lives in the card, not the app. Proposal: keep it in the browser's local storage so a reload does not lose it, with
a note "draft from earlier" when it is found (it would not show on another browser). Apply writes only the equipment keys
(`site`, `solar_plants`, `devices` and the mappings of the parts concerned) onto the saved configuration; the settings' own
draft is not applied by it. To check in E1: `pe_config_save` takes the whole config today, so the card would send the saved one
with those keys replaced.

### Replace

A required part (below) has no Remove, only **Replace**: the same four steps, then the existing `_site_guard` effects, stated
before the user confirms (switch to Passive, `retest_required`). The panel never touches Active, Passive or Pause itself.

## Removal warnings

The warning depends on what PowerEngine needs from the part. The data exists already: `config.required_roles`, `left_out_roles`
(`SKIPPED_PART_GROUPS`) and the card's feature list.

| Removing | What happens |
|---|---|
| **Inverter, tariff** (required; `site.inverter` and `site.tariff` accept no `none`, `config.py` `SITE_EXTRA`) | Remove is blocked. **Replace** is offered, with the Passive and retest warning. |
| **Car charger, forecast, grid events** (optional; the site key becomes `none`) | A confirm that lists the consequences in plain words ("No forecast: planning assumes no solar"; "No car charger: smart-charge slots and car-aware planning are off") and the input roles that become unmapped, from `left_out_roles`. |
| **Extra device or solar plant** | A lighter confirm: solar totals (and the forecast comparison) drop by this plant. |
| **A controlled device** (once M3 allows more than one) | As an extra device, plus: it stops being driven. |

Texts use the names map (`<<term>>`, `fillNames`), never a hard-coded supplier or device name. After a removal the panel offers to
clear the input mappings that nothing uses any more, in the same Apply.

## Card changes

- **New:** the Your system section, the panel, and `equipment*` pure helpers (impact of a removal, draft-change summary), tested in
  `tests/equipment.test.cjs`.
- **Reused:** `wizardFacts`, `wizardMatch`, `wizardCandidates`, `wizardOthers`, `wizardSuggest`, `wizardSignCheck`,
  `wizardUsedEntities`, `wizardPlantFromDevice`, `buildCandidateExport`, `scrubText`, `siteRows` and the device helpers. Rename
  away from "wizard" in the same change (the user-facing word goes; internal names can follow).
- **Removed:** `powerengine-wizard-card` and its dashboard entry (and the golden), "also found", the plants box, the "Other devices"
  block and the equipment selects in "Your system".
- **Config page order:** demo banner, update, setup checklist, Your system, handover, config.
- The wizard text in `docs/WIZARD.md` becomes this document's user guide; `docs/INSTALL.md` is updated in the same PR.

## Controller changes

- **Phase 1: none.** The panel writes what the card already writes: `site`, `solar_plants`, `devices` (app 0.9.93+), `inputs`.
  The app's `wizard` attribute stays as the data source for how to recognise each adapter (rename later, with a deprecation, not now).
- **Phase 2:** per-definition role lists (so a device can be asked for more than three inputs; `multiple-devices.md`), a battery-only
  definition (`validate` relaxed), plants folded into `devices` with a migration.

## Stages

- **E1, panel and list, with today's data model.** Your system section, panel, draft and apply, removal warnings, wizard retired.
  Card only; `MIN_APP_VERSION` unchanged (hidden pieces follow the app's published attributes as now).
- **E2, per-definition roles and a battery-only kind.** App and card; raises `MIN_CARD_VERSION` / `MIN_APP_VERSION` as needed.
- **E3, plants become devices.** Migration in the app; the list is unchanged to the user.

## Still open

- Whether the draft survives a reload (local storage, proposed).
- Whether the panel shows firmware detection for inverters (today's `firmware_detected` line) in step 3 or on the list row.
- Not yet run on a live HA: `hass.entities[].platform` and `hass.devices` are what the card expects (same open point as the wizard).
