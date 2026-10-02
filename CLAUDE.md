# PowerEngine: notes for Claude

PowerEngine is a Home Assistant energy manager, run as an AppDaemon app. It plans and controls a home battery
(Solis S5-EH1P6K-L hybrid inverter, 18 kWh battery) against EDF tariffs (smart slots ~6.99p, overnight ~7p, peak
~30p, export 15p), a myenergi Zappi car charger and Solcast forecasts. It publishes its entities over MQTT, and a
companion Lovelace card (`../ha-powerengine-card`) provides the configuration, test, health, update and
diagnostics UI. The owner is Matthew. This is live on his house: mistakes cost money or inverter wear.

## Layout

- `apps/powerengine/powerengine.py`: the AppDaemon app (large; wiring, control, scheduling).
- `apps/powerengine/pe_core/`: pure logic, testable without Home Assistant. Planner, optimiser, decisions,
  readings, costs, learning, RAM control, damping, journal, releases, diagnostics and so on.
- `apps/powerengine/dashboard/dashboard.lovelace`: the dashboard. The app writes it to HA on start, so edit it
  here, never in HA.
- `docs/INVERTERS.md`: how to add an inverter as a definition file (`pe_core/adapters/devices/<name>.yml`).
- `docs/INSTALL.md`: the install guide. Keep it current: any release that changes setup updates it in the same PR.
- `docs/ha/powerengine_handover.yaml`: the HA package (handover scripts, update script, watchdog automations).
  The owner installs it into HA; see "HA config" below.
- `tools/release.sh`: the release (below). `tools/diag_summary.py`: summarises a diagnostics export (below).
  `tools/build_demo_pack.py`: rebuilds the demo pack.
- `tests/`: pytest. `tests/test_replay.py` with `tests/replay_harness.py` replays a recorded night through the
  whole app (see "Replay safety net").
- The card repo has one JS file, `ha-powerengine-card.js`, and `tests/helpers.test.cjs` (node --test).

## Checks before any PR

```
export PATH=$HOME/.local/bin:$PATH
ruff check .                    # controller repo
python3 -m pytest -q            # about 70 s, including the replay
node --check ha-powerengine-card.js && node --test tests/*.test.cjs   # card repo, when the card changes
```

## Replay safety net

`tests/test_replay.py` replays 27-28 Sep (scrubbed fixture `tests/replay/day_2026_09_27.json`) through
`PowerEngine.initialize()` and `_cycle()`, with Home Assistant and the clock faked. It runs on RAM control and on
timed windows, and compares every plan, decision, service call and warning with `tests/replay/expected_*.json`.

- **Refactoring must pass it unchanged.** If it fails, the refactor changed behaviour: fix the refactor, don't
  re-record.
- **An intended behaviour change** re-records with `PE_REPLAY_UPDATE=1 python3 -m pytest tests/test_replay.py`,
  and the PR explains the diff of `tests/replay/expected_*.json`.
- More fixtures can be built from a diagnostics export with `tests/replay/build_fixture.py`, which scrubs
  account numbers, meter IDs and serials. Never commit an unscrubbed export.

## Releases (every user-visible change)

Load the `release` skill for the full routine (branching, `tools/release.sh`, minimum-version rules). Tests-only or docs-only changes can merge without a release.

The owner updates with the **Update** button on the dashboard's Configuration page: it refreshes HACS, installs
both and restarts AppDaemon. PowerEngine also checks GitHub for new versions every 5 minutes.

## Working routines

- **Routine check of a diagnostics export** (Health tab's export): run `tools/diag_summary.py <export.json>` (add
  `--since <ISO>` for a window, `--json` for machine-readable output). It prints one screen: versions, mode, log
  levels, deduplicated warnings, decisions per hour and flip-flops, RAM commands, smart-slot requests and slots, Axle
  events, restarts, attributes above 12 KB, the next 12 plan slots and the write budget. Open the full JSON only for
  what the summary flags.
- Prefer fresh sub-agents per task over resuming long-lived ones.

## Guardrails (don't break these)

- **Never write to Backup or Off-Grid modes, or to any entity with "bump" or "boost" in its name.** The app
  enforces this too.
- **Don't hammer EDF's API**, or any external API.
- **No admin HA token for PowerEngine.** Things that need admin rights (restarting add-ons, HACS actions) go
  through the handover package's scripts or the card (an admin's browser).
- **Leave Active / Passive / Pause to the owner.** Don't change them on his behalf.
- **Inverter writes are precious** (EEPROM wear). The default for this install is RAM remote control (43135 mode,
  43136 charge W, 43129 discharge W, capped at 5000 W; failsafe about 5 min). Any change to control must keep the
  write budget, dampening, read-back checks and the RAM refresh behaviour intact.
- **Attributes of published sensors must stay under 16 KB** (HA's recorder skips larger ones). `_publish_state`
  measures them and warns above 15,000 bytes. The role catalogue is about 14.5 KB.
- **HA config:** a git copy lives at `/home/matthew/HA/config`, synced by the owner with `./ha-sync.sh pull` /
  `push --apply`. Commit there only right after he says he has pulled. He pushes and applies it.
- **Never hard-code a supplier or device name in user text** (EDF, Zappi, Solcast, Solis, Axle): use the names map
  (`pe_core/names.py`, `N(term)` or a `<<term>>` placeholder). Stored names (entity ids, topics, keys) never change.
- **A forced charge must never buy grid energy at a dear rate that the plan didn't count.** The plan drops a
  grid-charge that needs no grid energy at a non-cheap price (`_solar_only_charges`, planner.py): with RAM control a
  "charge" is a fixed-power Force charge, and less sun than forecast meant importing at 30.28p (29 Sep 2026). Sentences
  must name the price the decision used (`_car_slot_p`: "tariff still 30.28p" for a planned slot not yet priced).
- **Decisions at a charge target latch for the half-hour** (`_held_at_target`, decide.py). The inverter's SoC reads a
  point lower while charging than while holding, so without it Force charge and Hold alternated every 30 s.
  `Decision.details["reached"]` carries the latch; nothing else may use `details` for other purposes without care.
- **Display wording vs mode keys:** `ModeDecision.label` ("waiting for inputs") is for logs and the summary only;
  `effective == "unconfigured"` stays the key the entity, the card and the code use.
- **Deleting files:** only when he asks. Put scratch files in `~/Projects/powerengine/_to_delete`.

## Backlog

- Custom date range for the Costs savings waterfall: tried in 0.9.72-0.9.74 (relative-day From/To selects + a `custom`
  period), removed in 0.9.75 because it didn't work as the owner wanted; re-ask him what he wants before building it
  again. Ideas: real calendar date pickers; per-day data summed in the card.

## Recent fixes

- **#121, RAM control and BMS limits. Done in 0.9.73** (`pe_core/bms.py`, `ramcontrol.py`). Optional roles
  `battery_bms_charge_limit` / `battery_bms_discharge_limit` (Solis suggestions) cap commands at limit A x 52 V
  (`BATTERY_VOLTS`; no battery-voltage role, because it pushed `map_catalogue` over 15,000 bytes: it is 14.9 KB now, so
  any new role needs a size check). Limit 0 = Hold on a charge, Off on a discharge. Cold-caution fallback when the charge
  limit is unmapped. The follow check uses the expected power. A command not followed (battery under 20% of it, after the
  3-minute alarm) steps down 1000 W to a floor of 3000 W, for up to 60 minutes. The diagnostics export has a `bms` section
  (in-memory ring, lost on restart). **Not done:** Modbus read-back of 43136/43129 (no entity for it). To watch in the
  first cold spell: the export's `bms` rows against the actual battery power; the conversion assumes battery-side watts.

## Current work: making it generic (Phase 0)

Plan: `docs/plans/making-it-generic.md` (index: `docs/plans/README.md`). Load the `phase0-generic` skill for the
step-by-step implementation status (steps 0-8, file-level detail, what's left per step).

## Demo mode plan

Plan: `docs/plans/demo-and-easier-install.md`. A2, A3 and D1 are parked; the options are in #192. Load the
`demo-mode` skill for the implementation detail (publisher adapter, the DemoGate rule, the data pack, the
`pe_demo`/`pe_demo_result` event contract).
