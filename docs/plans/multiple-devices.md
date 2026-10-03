# Multiple devices

> **Status (3 Oct 2026).** Design agreed in outline with the owner (see "Decided"); no control code yet. Detection and display are
> built: the setup wizard and the "Your system" block list every device Home Assistant has for each part, say which one PowerEngine
> uses ("in use now") and flag energy equipment no adapter owns. PowerEngine still reads, plans and controls **one inverter with its
> battery, one car charger, one tariff, one forecast and one grid-event provider**. Extra solar sources are supported now as
> `solar_plants` (read only). This plan is the design for the rest: any number of devices (hybrid inverters, solar-only inverters,
> battery-only units), a setting for which of them PowerEngine controls, and a plan of its own for each battery. It is written for
> other people's homes first; the owner may add devices of his own later. Nothing here changes how his battery is driven until M3,
> and every stage passes the replay unchanged for a one-inverter home.

## Why it matters

Several of these are normal homes, not edge cases:

| Part | Legitimate multiples | Today |
|---|---|---|
| Inverter and battery | Two hybrid inverters (same or different brand), each with a battery; a second battery stack; plug-in solar with storage | One controlled inverter and battery. A second inverter that is solar-only is a plant (below). |
| Solar | Several arrays; a second solar-only inverter; plug-in panels | Supported (`solar_plants`, read only). The owner's second inverter is one. |
| Car charger | A second charger; one charger and several cars | One charger. `site.car` is reserved (`none`). |
| Car | Two cars, each with its own battery size, target and ready-by time | Not modelled: the car is a load seen through the charger. |
| Tariff | A separate export tariff; an EV tariff on its own meter | Import and export rates are separate inputs and may come from any entity. One meter. |
| Grid events | More than one provider (an aggregator and the supplier's saving sessions) | One provider (`site.events`), plus free-power sessions from the tariff. |
| Forecast | Another forecast per array | `forecast` is chosen per plant. |

## What the code assumes today (why it isn't a settings change)

- `site` holds one name per part and the app builds one adapter per part (`_inverter()`, `_ev()`, ...).
- Inputs (roles) are keyed by role name only: there is one `battery_soc`, one `battery_power`, one `grid_power`.
- The planner, optimiser and learning work on one battery: one capacity, one SoC, one pair of power limits, one set of
  learned rates and conversion losses.
- Control writes one inverter: the write budget, damping, the RAM refresh and following check, the failsafe and the
  supervised tests are per install, not per device.

## Design

### A device is a capability set, not a type

A **device** is one piece of equipment with an adapter (an inverter definition file today) and an id. What it can do comes from
**capabilities** in its definition, not from a fixed "kind":

| Capability | Hybrid inverter | Solar-only inverter | Battery-only unit (AC-coupled, plug-in storage) |
|---|---|---|---|
| `solar` (reports PV power and energy) | yes | yes | no |
| `battery` (reports SoC and battery power) | yes | no | yes |
| `drive` (can be commanded: RAM remote control or timed windows) | yes, if the definition has it | no | yes, if the definition has it |

The user then sets, per device, **`control`**: `controlled` or `read only`. Only a device with `drive` may be `controlled`, and a
new device always starts `read only`. A read-only battery is still measured and shown; a read-only solar source is a plant today.
The wizard's "Your system" block becomes the one place to add a device and to choose which are controlled.

```yaml
site:
  inverter: solis            # unchanged: the first controlled hybrid; old configs load as they are
devices:                     # optional; absent = the one inverter above (identical to today)
  - {id: garage, adapter: solis, firmware: "420044", control: read only}
  - {id: shed,   adapter: some_battery_unit, control: controlled}
```

`site.inverter` stays readable as the device with id `main` so a one-inverter config does not change. The existing `solar_plants`
list keeps working and is shown as solar-only devices that need no definition (a power and an energy entity).

### Inputs per device

Roles are keyed by role name today (`battery_soc`, `battery_power`, ...). A device other than `main` uses `<id>.<role>`
(`shed.battery_soc`), so the catalogue, saved configs and the `main` device's entities do not change. The role catalogue is near its
15 KB limit, so a device's roles are published **per definition** (the card asks for the roles of the definition it is configuring),
not all at once in `map_catalogue`.

### One plan per battery, mirroring the main one

Every device with a battery gets its own `BatteryProfile`, the slice of `Params` that describes that battery: capacity, usable
window (reserve SoC, target SoC), maximum charge and discharge power, one-way efficiency, wear cost, learned taper and discharge
taper, and what the device's drive can do (RAM control or timed windows, write budget). Values come from the device's definition,
then the user's settings for that device, then what it learns, exactly as for the main battery. The optimiser is not rewritten:
it already plans one battery against a list of slots, so a second battery is a second call with its own profile.

What the batteries share, and so must be planned together, is the house: one load, one grid connection (import limit, export limit),
one tariff, one set of grid events and one car. The plan is built by **residual planning in priority order**:

1. Order the controlled batteries (default: larger usable capacity first; the user may reorder).
2. Plan the first against the house load, forecast and grid limits as today.
3. Subtract its planned flows from the load and the grid headroom, and plan the next against what is left. Repeat.
4. Check the sum against the fuse and the export limit; trim the last planned battery's flows, never the first's.
5. A grid event's export power (Axle: 4 kW) is shared: filled in priority order up to the connection's export limit.

A battery that is **read only** is not planned. If it is autonomous (does its own self-use) its expected flows are simulated with the
same self-use rule and added to the load the controlled batteries see, so the plan does not assume energy that the other battery
will take or give. A solar-only device is a forecast and a measured source: it adds to solar, never to the plan.

This is sequential, not jointly optimal, and the plan says so. It is the same trade the single-battery plan already makes between
the car and the battery (`hold_for_car`), it needs no new optimiser, and it makes one battery's behaviour independent of whether
a second one exists. A joint optimiser can replace it later without changing anything outside the planner.

### Control per device

Control is per install today (one write budget, one damping state, one RAM refresh, one failsafe, one set of supervised tests, one
Active guard). With several devices each of those becomes an instance **per controlled device**, and stays one per install where it is
about the house (the global Active/Passive/Pause modes, the journal):

- **Mode.** The global mode stays the owner's. A device acts only when the global mode is Active **and** the device is `controlled`
  **and** its definition and firmware are `verified` (the existing Active guard, per device). Passive shows the plan for every
  device without writing. Leaving Active/Passive/Pause to the owner stays a guardrail.
- **Writes.** Write budget, damping, read-back checks, the RAM refresh and the following check run per device with that device's
  limits. The low-write credit is per device (an EEPROM device has its own wear).
- **Failsafe.** One device failing safe (its RAM command lapses, it stops following) does not stop the others; each reverts to its
  own self-use. A device that cannot be read is treated as read only until it can.
- **Changing the set.** Adding a device, or changing which are controlled, switches to Passive and sets `retest_required` for that
  device, the way a changed inverter does now (`_site_guard`); the supervised test passes per device before it can act.

### Publishing and the card

Entities are published per device (`pe_<id>_...`) with the existing publisher; nothing is added to the version sensor's attributes
(16 KB shared). The card gets a devices list (add, remove, control on/off, order), a per-device plan row on the Plan tab and a
per-device tile on Monitoring; a one-device home sees exactly today's pages. The wizard recognises a device the same way it
recognises an inverter now (its definition's `detect:` block) and offers it as a device, not only as a plant.

### Costs and learning

The cost book's ledger of stored energy is per battery, because each battery holds energy bought at its own prices. The waterfall
sums over devices; "PowerEngine" is the sum of what the controlled devices did. Learned rates, conversion losses and capacity
are per device. Records stay in the old fields for `main`, so history is unchanged.

### Testing without hardware

The owner has no second controllable battery, so M3 cannot be proved on his house. The demo world (`pe_core/demo/world.py`) already
simulates one battery and the real adapters read its entities; it gains a second, differently sized device, which gives a two-device
replay fixture and a way to test failsafe, trim and priority before any real hardware is involved. The first real trial is a long
Passive run on someone's install, then one device Active.

## Stages

Each stage is shippable and passes the replay unchanged for a single-inverter home.

- **M0, detect and list. Done.** The wizard and "Your system" show every candidate per part and mark the one in use; unsupported
  energy devices are listed with a way to send their entity list (docs/WIZARD.md).
- **M1, the device model, read only.** `devices` in the config (`config.Device`: id, adapter, firmware, control), capabilities in
  definitions (`solar`, `battery`, `drive`), per-device role mapping (`<id>.<role>`) with per-definition role catalogues in the card,
  and the card's devices list. A device contributes its readings only: battery power and SoC on Health and Monitoring, solar in
  the totals. Solar plants are shown as solar-only devices. No planning or control change. Needs: a definition for a battery-only unit
  (a `capabilities` block and no timed or RC section), and the wizard offering a device.
- **M2, a plan per battery, shown not executed.** `BatteryProfile`, residual planning in priority order, the shared grid limits and
  the shared grid event, and the autonomous-battery simulation. Each controlled-to-be battery shows its plan in the Plan tab; nothing
  is written to any device except through the one existing controller. Two-device replay fixture from the demo world. This is where
  the plan is judged against reality, in Passive.
- **M3, control of more than one device.** Per-device controller instances (RAM refresh, following check, failsafe, write budget,
  damping, Active guard, supervised tests, low-write credit). It needs the demo world's second device, a two-device replay, a long
  Passive trial, then one device Active at a time. A battery-only unit with timed windows only is the low-write case and uses its
  credit.
- **Cars.** `site.car` becomes a list of cars (battery size, target, ready-by) linked to a charger; `site.ev_chargers` a list of
  chargers. Smart-charge requests then target a car. Independent of the inverter stages.
- **Grid events.** `site.events` becomes a list; events from all providers are merged by time with a stated priority.
  Independent of the inverter stages.

## Decided

- **Select which devices PowerEngine controls.** Every device is read; only the ones the user marks `controlled` (and whose
  definition and firmware are verified) are driven. The others are read only.
- **Any mix of devices:** hybrid inverters, solar-only inverters and battery-only units, with more than one of each allowed. Which
  of these a home has is capabilities in the definitions, not code paths per kind.
- **Each battery has its own plan** that mirrors the main one, with the device's variations (battery size, power rates, reserve,
  efficiency, what its drive can do). Built as residual planning in priority order (above), not a joint optimiser.
- **Design now, build in the stages above.** For other people's homes first; the owner may add devices of his own later.

## Still open (decide when M2 starts)

- The default priority between batteries (largest usable capacity first is the proposal; or the one with the cheaper cycling cost).
- How a grid event's export is split between batteries when one is nearly empty (the proposal fills in priority order and moves on).
- Whether a read-only battery should be planned around when it is not autonomous, for example another system that follows its own
  schedule (the proposal: simulate it as autonomous and say so).
- Cars: how many, and do they share the Zappi?
- Plug-in solar: which products (with or without storage) and which integrations are people likely to have, so the wizard's
  "looks like solar" hints and the first definitions can cover them?
