# PowerEngine card: notes for Claude

This is the Lovelace card for PowerEngine. It lives in the PowerEngine repository (`card/`) and is bundled into the
integration, which serves it and registers it as a Lovelace resource. **Read `../CLAUDE.md` first**: it has the
release routine and guardrails.

- One file: `ha-powerengine-card.js`. It holds several custom elements: config, toggle, test,
  diagnostics, update, health, log and sim cards.
- Tests: `node --test tests/helpers.test.cjs`. Pure helpers are exported via `module.exports` at the bottom for
  testing.
- The card is released with PowerEngine: the build sets the bundled copy's `CARD_VERSION` to the engine's version, so
  the two always match (the source's own `CARD_VERSION` is not used). There are no separate card releases.
- The card and the engine are one version, so the card checks nothing about the engine's version and there is no minimum
  to raise. A browser tab left open across an update still runs the old card: reload it (a save from an old card can leave
  out settings the new engine has).
- **Your system** (`PowerEngineSystemCard`, `custom:powerengine-system-card`; the old name `powerengine-wizard-card` is kept as an alias for a dashboard
  written before it) replaced the setup wizard and the config card's equipment blocks. Plan: `docs/plans/equipment-manager.md`. The page
  shows the configured equipment read only; "Change your system" opens a panel (an overlay inside the card) to add, edit, replace and remove. Rules:
  - The panel works on **ops** (`opsSet`, `opsRemove`, `opsUndoRemove`; one per target: a part's name, `plant:<id>`, `device:<id>`), applied to the saved
    config by `applyOps` / `buildApplyConfig`. **Save draft** keeps the ops in local storage (`systemDraftSave/Load`, keyed to a fingerprint of the saved
    equipment, so a draft of a system that has since changed is dropped); **Apply to System** sends `pe_config_save` with the saved config plus the ops, and
    only equipment keys change. Nothing is written to the saved config until Apply. Closing with edits asks Save draft / Discard changes / Keep editing.
  - Required parts (inverter, tariff: `site` takes no `none` for them) can only be replaced; removing an optional part (car charger, forecast, grid events)
    sets the site key to `none`, switches off only the features that need it (`SYSTEM_LEFT_OUT_FEATURES`) and shows `systemImpact` first. Adding a part never
    switches a feature on. An optional part never set up is saved as `none` (`buildApplyConfig`), as the wizard did.
  - Apply does not change Active, Passive or Pause; the app's `_site_guard` does when the inverter changes, and the panel says so before applying.
  - After Apply the card fires the window event `powerengine-config-applied`; the config card follows the saved plants, devices and changed inputs
    (`overlayEquipment`, in its `_refresh`) so its own Save never writes old equipment over new.
  - Device discovery and the candidate export stay as pure `wizard*` helpers (`wizardCandidates`, `wizardInUse`, `wizardOthers`, `wizardSuggest`,
    `wizardMissing`, `buildCandidateExport`, `scrubText`), tested in `tests/wizard.test.cjs`; they work from what the app publishes as
    `sensor.pe_diag_version` attribute `wizard` (no brand names in the card). New helpers: `tests/system.test.cjs`. Format and rules of the export: the app repo's
    `docs/WIZARD.md`. **Not yet run on a live Home Assistant** (`hass.entities[].platform` and `hass.devices` are what the card expects). To look at it
    without one, load the file in a page with a fake `hass` (states, entities, devices, `callWS`, `connection.subscribeEvents`) and the app's published JSON, and drive it with Playwright.
  - Use "grid events" for the concept in texts; the provider name comes from `<<event>>`. Extra solar plants (`solar_plants[1..]`, read only) and other devices
    are added in the same panel; a device already used by the config (`wizardUsedEntities`) is not offered again. The setup card hides itself when `setupSummary().allSet`.
- "Other devices" (section "Other devices" before the card class: `DEVICES_APP_VERSION`, `DEVICE_INPUTS`, `devicesSupported`, `deviceDraft`,
  `buildDevices`, `deviceReadout`; UI in the Your system panel, `_entitiesDevice`) edits the app's read-only `devices` (app 0.9.93+, see
  docs/SITE.md and docs/plans/multiple-devices.md). **Rules:** the list is shown, and `devices` sent, only when the app is 0.9.93
  or newer (an older app rejects the unknown key); once sent it is always a list (`[]` removes them; a save without the key keeps the saved
  ones, which is what an older card does); the entity-in-use check (`wizardUsedEntities`) counts device inputs. Tests: `tests/devices.test.cjs`.
- **Looking at the plan chart without Home Assistant:** `ha-card` is a Lit element there that renders its slot a moment after it is created, so a scroll box built in the same breath has
  no width (`clientWidth` 0) and ignores `scrollLeft`; a plain `ha-card` in a test page hides this (cause of the 0.9.126-0.9.130 "starts at the start of the history and jumps back" reports).
  `_whenLaidOut` waits for the width; the scroll handler ignores a replaced box. A faithful check runs the real dashboard in a local Home Assistant (pip `homeassistant`, a
  `lovelace` YAML dashboard, the card as a resource, states pushed with `POST /api/states/<id>`) in a phone-sized Playwright context with raw `Input.dispatchTouchEvent` swipes.
