# Mode override and the top panel

> **Status:** proposed 4 Oct 2026, nothing built. Needs the owner's answers to the open questions at the end.

A way to switch the inverter's behaviour by hand for a while, from the Monitoring page, and a clearer top panel.

## What the owner asked for

- A button in the top panel to switch temporarily to force export, home, self-use, charge and so on.
- It offers the options and a period: the **current plan period**, or a time the owner picks, **aligned with plan slots**.
- The existing "Mode" tile becomes **Power Engine** (Active or Passive), so it is not confused with the inverter's mode.

## Design

**Choices** (each is a `Decision` action the controller already knows, so RAM control, damping, the write budget and
read-back work unchanged): Self-use (`self_use`), Charge (`grid_charge`, to the charge target), Export (`export`, forced
discharge to the grid), Hold (`hold`: the grid covers the house, the battery is kept), plus **Resume plan** to cancel.
"Home" needs the owner's meaning (below).

**Period**: all end on a half-hour boundary, so they line up with plan slots: *this plan window* (until the current
window ends), *this half-hour*, *N half-hours* (1 to 12) or *until HH:MM*. Longest 12 hours. It expires by itself and
the plan resumes; it is stored in the save dir (`override.json`) so an AppDaemon restart keeps it.

**Where it sits in `decide`**: above the plan and the rules, below everything that protects the house and the hardware.
Still applied over it: Pause (an override does nothing while paused), the fuse limit, the minimum reserve (Export and
Self-use stop at the floor, then Hold), BMS limits and the RAM write budget. Active only: in Passive it is refused with a
message (the app never acts in Passive). It is a manual action by the owner, so it does not change Active, Passive or Pause.
A charge or export override never overrides the rule that a forced charge must not buy dear grid energy unannounced: the
card shows the price it will pay for the period before asking for confirmation.

**Transport** (same pattern as the demo: admin event over the websocket, answer event): `pe_override`
`{"action": "set", "mode": "export", "until": "<ISO half-hour boundary>"}` / `{"action": "clear"}`, answer
`pe_override_result {ok, message}`. Validation in the app (`pe_core/override.py`: parse, align, expire, describe); the
card only offers valid choices. New sensor `sensor.pe_state_override` (state `none` or the mode; attributes `mode`,
`until`, `set_at`, `price_p`), small.

**Planning**: stage 1 leaves the plan alone (it replans as usual and the override simply wins now). Stage 3 makes the
planner treat the overridden half-hours as fixed, so the plan after it starts from the real battery level.

**Top panel** (card + `dashboard.lovelace`, golden re-rendered):
- Tile "Mode" becomes **Power Engine**: Active / Paused / Passive / Blocked, same colours.
- New tile **Inverter**: what the inverter is doing now (Self-use, Charging, Exporting, Hold), and when an override is on,
  "Override: Export until 14:30" with a cancel button.
- Button **Override** opens a small dialog: mode, period (the choices above), the price, **Apply**.
- Needs `MIN_APP_VERSION` raised on the card and `MIN_CARD_VERSION` on the app, released together.

## Stages

- **O1 app**: `pe_core/override.py`, `decide` hook, event handler, sensor, restart persistence, tests (including the
  replay with an override), docs. No card change needed to test it (fire the event).
- **O2 card and dashboard**: the tiles, the dialog, the rename.
- **O3 plan-aware**: fixed override slots in the planner; optional "return to plan at the next window".

## Open questions

1. What is "Home"? Candidates: *Hold* (grid runs the house, battery saved) or *Self-use* (battery runs the house). If both
   are wanted, Hold could be labelled "Grid runs the house".
2. Should an override be allowed to run into an Axle event (the event normally wins), or should the event always take over?
3. Is 12 hours the right longest period?
