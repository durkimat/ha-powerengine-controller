# Phase 1 (generic core)

Moved out of CLAUDE.md (Oct 2026) to keep every session's context small. Read this file only when the task touches this area. The rules that must always hold are listed in CLAUDE.md under "Rules that live in the history files".

## Current work: Phase 1 (generic core)

Plan status box: `docs/plans/making-it-generic.md`. Done so far:

- **Active guard for unverified definitions** (`pe_core/verification.py` `active_refusal(inverter, firmware)`, wired into
  `effective_mode(..., unverified=)` by the app's `_unverified()`). Active is refused (effective Passive, reason "Active
  refused: ...") unless the definition's `status` is `verified` and, when it lists `verified_firmware`, the firmware that applies
  (the site's, else the definition's default) is listed. A definition that won't load is refused too. Not applied in a demo.
  Byte-identical for his Solis on 420044 (replay unchanged). The config keeps saying `active`; only the effective mode is Passive.

- **Setup wizard and candidate export. Written on branch `ccr-6832e975-2wkm5i`, not released** (docs/WIZARD.md; needs card and
  app 0.9.88 or the next free version: `MIN_CARD_VERSION` is already 0.9.88, so release both together). The wizard is the card
  `powerengine-wizard-card` (one JS file, section "setup wizard and candidate export": pure `wizard*` helpers, then the class),
  placed on the Config tab (dashboard and its golden both carry it). The app only says what to look for:
  `pe_core/wizard.py` `wizard_info()`, published as attribute `wizard` of `sensor.pe_diag_version` (about 3.5 KB: keep it, the
  version sensor's attributes share 16 KB). Per part (inverter, tariff, ev_charger, forecast, events; the first two required, the
  rest skippable as `none`): its adapters with how to recognise them (`adapters/detect.py` for the non-inverters, the
  definition's `detect:` block for inverters, validated in `definition.py`), and its role keys (`GROUP_PART` / `ROLE_PART`).
  **Rule: a new adapter or definition needs a detect entry, or the wizard can't find it.** `config.required_roles` now leaves out
  the roles of a part the site sets to `none` (`SKIPPED_PART_GROUPS`, `left_out_roles`; no car charger also drops the
  smart-charge group), which is what makes "skip" work. The card hides the wizard when `wizard` is absent (no `MIN_APP_VERSION`
  bump). The candidate export (`buildCandidateExport` in the card, `pe_core/candidates.py` format and checks,
  `tools/candidates_summary.py`) is scrubbed in the card (long digits to `<n>`, emails and postcodes dropped; firmware keeps its
  digits) and checked again by `candidates.unscrubbed`; never commit an unscrubbed one. **Not yet run on a live HA**: the card uses
  `hass.entities[].platform/device_id` and `hass.devices`, and the Solis `detect:` values and other adapters' integration domains
  (`solax_modbus`, `octopus_energy`/`edf_energy`, `myenergi`, `solcast_solar`, Axle's unknown) are best knowledge. Not done: a second
  inverter's `suggest` regexes reaching the card (the catalogue holds only the default's), Octopus tariff suggestions.
  Since 0.9.89 (card and app): on a configured system the wizard starts folded away and reads what PowerEngine already uses (the
  device holding a saved mapped entity, else the site's name), so EDF stays chosen when the Octopus integration is also there; other
  candidates are listed as "also found"; "Your system" lists them too; the setup checklist card hides itself when everything is in
  place; the Config tab order is demo banner, update, setup checklist, wizard, handover, config. Config page wording now says
  **grid events** (run by `<<event>>`, i.e. Axle) in the role, setting, feature, notification and topic texts (`axle` stays the key).

- **Your system (replaces the setup wizard). E1 built on branch `ccr-b18fa4a5-ve20m1`, not released** (plan: `docs/plans/equipment-manager.md`; user guide:
  docs/WIZARD.md). Card only, plus the dashboard line: `custom:powerengine-wizard-card` became `custom:powerengine-system-card` in `dashboard.lovelace` and its
  golden (the card keeps the old name as an alias for a dashboard the app has not yet rewritten). The Config tab shows the configured equipment read only and a
  **Change your system** panel; changes are a draft (browser local storage) until **Apply to System**, which sends `pe_config_save` with the saved config plus
  only the equipment changes. Removing a required part is blocked (replace only); removing an optional part shows what goes with it. The config card no
  longer edits site, plants or devices. **To release:** card and app together; raise `MIN_CARD_VERSION` (`pe_core/version.py`) to the card's new version, because
  the dashboard now names the new card; the card's release notes (`--card-notes`) say the wizard is gone. The app needs nothing else: `wizard`, `site_options`
  and the save path are unchanged. **Not yet run on a live HA** (same open point as the wizard); the card was driven in Chromium against a fake `hass`.
  Open: the setup checklist's button could open the panel; E2 (per-definition roles, battery-only kind) and E3 (plants become devices) are in the plan.
- **Manual override, O1 app and O2 card built (not released, 0.9.97)** (`pe_core/override.py`, `_on_override` in `powerengine.py`, plan `docs/plans/mode-override.md`;
  card `powerengine-override-card`, section "Manual override" in the card). The owner picks Self-use, Hold ("home": the grid runs the house), Charge or Export, for the
  plan window, N half-hours, until a half-hour time (max 12 h) or permanently. Event `pe_override` `{"action":"set","mode":..., window|slots|until|permanent}` /
  `{"action":"clear"}`, answer `pe_override_result {ok,message}`; sensor `sensor.pe_state_override` (state `none` or the mode; attributes `text`, `until`,
  `set_at`); `override.json` beside the config. In `decide` it sits **below a grid event in progress (the event wins), above the plan**; the reserve stops Export
  and Self-use, Charge holds at the target (2-point latch). Active only (`_on_override` refuses otherwise; the app passes `override=` to `decide` only when
  effective mode is active). Pause, fuse limit, BMS limits and the write budget act as for any decision. O3: the planner makes the plan around it (`Slot.manual`, set by `_mark_manual` for
  the slots from now to the end, skipping grid-event slots; `optimiser._actions` and `planner._default` fix the action, the post-passes and arbitrage skip them; windows carry
  `manual: true`, the plan series a `manual` list, the dashboard a pink "Manual override" band); set, cancel and expiry replan at once (the plan signature
  holds the active override). `MIN_CARD_VERSION` is 0.9.97 because the dashboard names the new card.
- **A Hold does not store surplus solar in the plan** (#175, `planner.step`): on the inverter Hold is Force charge at 0 W, so with spare sun the battery sat idle while Self-use charged
  at the full surplus (4 Oct 2026, from the diagnostics history: Hold with ~1.9 kW surplus averaged +0.19 kW battery, Self-use -1.9 kW). The plan used to charge from surplus in Hold, so its SoC
  path ran ahead of the battery and the optimiser liked Hold in sunny half-hours. Now Hold exports the surplus. Replay re-recorded: the RAM night's decisions and service calls
  are unchanged, only the plan strings; the timed-window runs lose one afternoon export and one decision moves by 10 minutes.
- **A charge target reached early replans (#175, `_early_target` in `powerengine.py`, `pe_core/earlytarget.py`)**: when `_with_plan` would hold at the target ("reached", `details["reached"]`) with
  5 minutes or more left in the half-hour (`MIN_LEFT_MIN`) the app calls `_maybe_replan(force=True)` (no mid-slot stick) and decides again; one look per half-hour (`first_look`). Every look is a
  record in `early_target.json` beside the config (last 300): `outcome` `replanned_changed` / `replanned_still_hold` / `late` / `no_plan` / `error`, `left_min`, `soc`, `target`, `price_p`,
  `next_action`, `next_price_p`, `car_charging`, the new action and reason, and `after` (the decisions that followed in the rest of the half-hour, for churn). The diagnostics export has
  `early_target` (summary + last 60), and `tools/diag_summary.py` prints a line. **Review about 11 Oct 2026** (first released 0.9.99): how often it fires, what it chose, minutes of hold avoided,
  extra energy sold or charged in those minutes (cost records), churn in `after`, and the `late` / `still_hold` cases that still end in a hold. Not done: the same for non-charge holds.
- **Short missing readings are bridged** (`_bridge_data_gap`, `DATA_GAP_GRACE_S` 180 s): a `no_data` decision within 3 minutes of the last real one keeps it.

- **Licence and CONTRIBUTING. Done.** Apache-2.0 in both repos (`LICENSE`, `NOTICE`, README sections; copyright 2026 Matthew Durkin;
  they were MIT before, which stays true for copies already taken). `CONTRIBUTING.md` in each repo: what to send, how to add a
  definition, the PR checks and the replay, the rules the code keeps, safety on other people's hardware.
- **Step 7 "Left" items. Done** (byte-identical for his Solis, replay unchanged): the timed-slot count is a definition value
  (`timed_slots.count`, 1 to 8; `schedule.assign/programmed/desired_state(..., slots=)`), the update button role is a definition value
  (`timed_slots.button_role`, default `timed_update_button`; `writes_needed(..., button_role)`, `schedule.writes_for(..., button_role)`),
  and the remote-control mode the select reads is mapped back to the app's words (`DefinedInverter.app_option`, used by the supervised
  test's "still shows" check). The app reads `slot_count` and `button_role` from `self._inverter()`. Not done: a timed behaviour that
  isn't hour/minute numbers plus a button, and a remote control that isn't a mode select plus two powers (each is a new `behaviour:`).

Open: low-write mode for EEPROM-only inverters (#189), designed in `docs/plans/low-write-mode.md` (L0 study and L1 shadow study done, L2 to L4 to build).

