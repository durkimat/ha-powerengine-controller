# 4. Priorities and overrides: who wins

Code: `pe_core/decide.py` (`_decide`, `_with_plan`, `fuse_limited`), `pe_core/override.py`, `pe_core/modes.py`
(`effective_mode`), `planner._default` and `optimiser._actions` (the same ordering inside the plan), and in the app
`_active_override`, `_mark_manual`, `_leave_active`.

There are **two places** where priority is decided, and they have to agree:

1. **Inside the plan**, per half-hour in the future (`_default` and `_actions`).
2. **Live**, for the half-hour running now (`decide`).

## 4.1 Which mode PowerEngine is in at all

`effective_mode` (`modes.py`). The first line that applies wins. Nothing below this is acted on unless the result is
**Active**; decisions are still *made* in Passive and Paused, they are just labelled "Would ..." and not sent.

```mermaid
flowchart TD
    A{"Config file has an error?"} -- yes --> A1["UNCONFIGURED"]
    A -- no --> B{"No config at all?"}
    B -- yes --> A1
    B -- no --> C{"Required inputs not ready?"}
    C -- yes --> C1["UNCONFIGURED (waiting).<br/>The inverter keeps its programmed windows for 10 min,<br/>then is returned to Self-Use"]
    C -- no --> D{"Configured mode is Passive?"}
    D -- yes --> D1["PASSIVE: monitor and simulate only"]
    D -- no --> E{"This build supports Active?"}
    E -- no --> D1
    E -- yes --> F{"Inverter definition not verified<br/>for this firmware?"}
    F -- yes --> D1b["PASSIVE: 'Active refused: ...'"]
    F -- no --> G{"A handover guard tripped?<br/>(another controller may be in charge)"}
    G -- yes --> D1c["PASSIVE: 'Active refused'.<br/>No writes to the timed windows"]
    G -- no --> H{"Pause switch on?"}
    H -- yes --> H1["PAUSED: inverter returned to Self-Use,<br/>no changes until resumed"]
    H -- no --> I["ACTIVE"]
```

Leaving Active hands the inverter back to Self-Use once (RAM control: remote control is switched Off, always). Going Active
(or resuming) starts a **5-minute restart hold-off** for timed-window writes (page 6).

## 4.2 The live ladder (with a plan)

`_decide` then `_with_plan`. This is what runs every 30 seconds once a plan exists, which is nearly always.

```mermaid
flowchart TD
    S["Readings"] --> R0{"No config / no SoC / no import rate?"}
    R0 -- yes --> R0a["NONE: 'no reading for ...'<br/>(a gap of 3 minutes or less keeps the last decision)"]
    R0 -- no --> R1{"1. Grid event in progress,<br/>feature on?"}
    R1 -- yes --> R1a["FORCE-DISCHARGE at full battery power<br/>rule: axle_active"]
    R1 -- no --> R2{"2. Owner's override set?<br/>(Active only)"}
    R2 -- yes --> R2a["The override's decision<br/>rule: override (4.4)"]
    R2 -- no --> R3{"3. Free-power session active?"}
    R3 -- yes --> R3a["GRID-CHARGE to 100%<br/>rule: free_power"]
    R3 -- no --> R4{"4. Car charging now?<br/>(house load includes the car)"}
    R4 -- yes --> R4a["Car rules (4.3)<br/>rule: car_charging"]
    R4 -- no --> R5{"5. Plan says FORCE-DISCHARGE<br/>but the event has not started?"}
    R5 -- yes --> R5a["HOLD<br/>rule: plan"]
    R5 -- no --> R6{"6. Plan says SELF-USE and<br/>SoC is at or below the reserve?"}
    R6 -- yes --> R6a["HOLD<br/>rule: reserve"]
    R6 -- no --> R7{"7. A 'target reached' hold was made<br/>earlier in this half-hour<br/>and nothing has changed?"}
    R7 -- yes --> R7a["Keep that HOLD (the latch)"]
    R7 -- no --> R8{"8. Plan says GRID-CHARGE and<br/>SoC is at or above its target?"}
    R8 -- yes --> R8a["HOLD 'reached the N% target'<br/>starts the latch"]
    R8 -- no --> R9["9. The plan's action for this half-hour<br/>rule: plan"]
    R1a --> Z
    R2a --> Z
    R3a --> Z
    R4a --> Z
    R5a --> Z
    R6a --> Z
    R7a --> Z
    R8a --> Z
    R9 --> Z["Fuse limit: cap a grid-charge so house + car + battery<br/>stay under 90% of the main fuse"]
    Z --> Y["Bridge a short data gap, early-target replan (page 5)<br/>then the control path (page 6)"]
```

Things worth noticing:

* **Free power and the car outrank the plan, but not the owner's override.** Override is above them.
* The **reserve check (6)** applies only to a planned *Self-use*. A planned Export or Force-discharge is not checked against
  the reserve live ([09, F5](09-review-findings.md#f5)); neither is a grid event in progress (rule 1).
* The **target-reached hold (8)** comes *after* the car rule, so it does not apply while the car is charging
  ([09, F3](09-review-findings.md#f3)).

## 4.3 When the car is charging

Inside `_with_plan` (only when the house load includes the car, `house_load_includes_ev`, default on). The aim is "the
battery must never feed the car".

```mermaid
flowchart TD
    A["Car is charging now"] --> B{"A target-reached hold is already on?"}
    B -- yes --> B1["Keep it"]
    B -- no --> C{"Plan says GRID-CHARGE this half-hour?"}
    C -- yes --> C1["GRID-CHARGE as planned<br/>'car is charging; (the plan's reason)'"]
    C -- no --> D{"Import price at or below<br/>the cheap threshold?"}
    D -- yes --> E{"top-up when cheap on,<br/>and SoC below the top?"}
    E -- yes --> E1["GRID-CHARGE up to the top<br/>(target, or the arbitrage ceiling<br/>outside the overnight window)"]
    E -- no --> E2["HOLD 'car charging at a cheap rate;<br/>the battery holds'"]
    D -- no --> F["HOLD 'car is charging at Xp;<br/>the battery holds and the grid<br/>covers house and car'"]
```

So with the car charging, the plan's Self-use, Export and Hold all become **Hold**. A plan that has Hold and a cheap
price becomes a charge (top-up). A 7.4 kW car charge is larger than the house load, so covering the house but not the car
was tried in 0.5.2 and dropped.

The cheap test here is the live threshold (`decide.cheap_limit`), not the plan's ([09, F4](09-review-findings.md#f4)).

## 4.4 The owner's override

Event `pe_override`, state `sensor.pe_state_override`, file `override.json`, code `override.py`.

| | |
|---|---|
| Choices | **Self-use**, **Hold** (grid runs the house), **Charge** (to the charge target), **Export** |
| Period | The running plan window; N half-hours (1 to 12); until a half-hour time (at most 12 h 30 min ahead); or permanent |
| Works in | **Active only**. In Passive or Paused it is refused/ignored (`_active_override`) |
| Sits | Below a grid event in progress, above everything else live and above the plan |
| Still applied over it | Pause, the fuse limit, the BMS limits (page 6), the RAM write budget |
| Safety inside it | Self-use and Export stop at the minimum reserve and hold. Charge holds when it reaches the target (resumes 2 points lower) |
| Not applied over it | Free power, the car rule, the "plan wants a force discharge but no event" rule. The owner chose it |

```mermaid
flowchart TD
    O["Override set"] --> M{"Mode?"}
    M -- "Hold" --> H["HOLD: grid runs the house"]
    M -- "Self-use or Export" --> R{"SoC at or below the reserve?"}
    R -- yes --> H2["HOLD 'but the battery is at its reserve'"]
    R -- no --> M2{"Which?"}
    M2 -- "Self-use" --> SU["SELF-USE"]
    M2 -- "Export" --> EX["EXPORT (force discharge)"]
    M -- "Charge" --> C{"SoC at or above the target,<br/>or held and still within 2 points of it?"}
    C -- yes --> C1["HOLD 'reached the target'<br/>(latch)"]
    C -- no --> C2["GRID-CHARGE to the target"]
```

**It also changes the plan** (O3). `_mark_manual` flags every slot from now to the end of the override with the override's
action (skipping a grid-event slot, which keeps its event). Inside the plan the override sits **between a grid event and
free power**, the same order as live. The optimiser then has no choice in those slots, and plans the rest from what the
battery will hold afterwards. Setting, cancelling and expiry replan at once.

## 4.5 The same ordering, inside the plan

| Priority | Rules planner (`_default`) | Optimiser (`_actions`) | Live (`_decide`) |
|---|---|---|---|
| 1 | Grid event slot: force-discharge | Only force-discharge | Event in progress: force-discharge |
| 2 | Override slot: its action | Only its action | Override: its decision |
| 3 | Free power: grid-charge to 100% | Only grid-charge | Free power: grid-charge to 100% |
| 4 | Cheap and top-up on: grid-charge to the buffer | Choice (below) | Car charging rules |
| 5 | Car smart slot with car expected: hold | Hold or grid-charge only (grid-charge only if cheap and top-up on) | Plan's action |
| 6 | Cheap: hold | Choice | Plan's action |
| 7 | Otherwise self-use | Choice among self-use, hold, grid-charge (and export if arbitrage) | Plan's action |

The three agree on the first three rungs. Below that, the plan assumes **no car except the running half-hour while the car is
charging** (0.9.103) and live reacts to the actual car. A change in the car's state remakes the plan, so the two stay close.

## 4.6 When there is no plan (fallback ladder)

If `plan` is `None` (just after a start, or planning failed) `_decide` falls through to a **reactive rule stack**. Rules 1
and the override are as above; then:

```mermaid
flowchart TD
    A["No plan"] --> B{"2. Event scheduled within the look-ahead<br/>(6 h) and SoC below what it needs?"}
    B -- "yes, import cheap" --> B1["GRID-CHARGE to the target, or the need if higher<br/>rule: pre_axle"]
    B -- "yes, not cheap" --> B2["HOLD at the need<br/>rule: pre_axle"]
    B -- no --> C{"3. Free power active?"}
    C -- yes --> C1["GRID-CHARGE 100%"]
    C -- no --> D{"4. Car charging?"}
    D -- yes --> D1["Cheap and below target: GRID-CHARGE<br/>otherwise HOLD"]
    D -- no --> E{"5. Import cheap?"}
    E -- yes --> E1{"SoC below the target<br/>(minus the restart margin, unless already charging)?"}
    E1 -- yes --> E2["GRID-CHARGE to the target<br/>rule: cheap_rate"]
    E1 -- no --> E3["HOLD: use the grid, save the battery"]
    E -- no --> F{"6. SoC at or below the reserve?"}
    F -- yes --> F1["HOLD rule: reserve"]
    F -- no --> F2["SELF-USE rule: default"]
```

**Need for an event** (`pre_axle_reserve`): `reserve + (event power x event hours / capacity x 100) + event margin`, capped
at 100%.

The settings `pre_axle_lookahead_h` (look-ahead), `axle_margin_soc` (margin) and `charge_hysteresis_soc` (restart margin)
are read **only** by this fallback. With a plan they have no effect ([09, F1b](09-review-findings.md#f1b)).

## 4.7 Conflict cheat-sheet

| Situation | What happens | Where |
|---|---|---|
| Grid event active, and an override | Event (override resumes after) | 4.2 rung 1 |
| Grid event active, and the car charging | Event (force-discharge) | rung 1 comes first |
| Grid event active, and the pause switch on | Paused: nothing is sent | 4.1 |
| Override, and a free-power session | Override | override is above free power |
| Override, and the car charging | Override. A Self-use or Export override can feed the car | no car check in `override.decide` |
| Free power, and the car charging | Grid-charge to 100% | rung 3 before 4 |
| Plan says Export, car charging | Hold | 4.3 |
| Plan says Self-use, car charging, import cheap, top-up on | Grid-charge up to the top | 4.3 |
| Plan says Self-use, battery at reserve | Hold | rung 6 |
| Plan says Force-discharge, event not yet active | Hold | rung 5 |
| Plan says Grid-charge, target reached, car not charging | Hold for the rest of the half-hour; replan if 5+ min left | page 5 |
| Plan says Grid-charge, target reached, car charging | Still Grid-charge | F3 |
| Any grid-charge, and the fuse near its limit | Charging power is cut to the room left | fuse limit |
| Any command, BMS says the battery can take less | Capped to the BMS limit (0 means Hold or Off) | page 6 |
