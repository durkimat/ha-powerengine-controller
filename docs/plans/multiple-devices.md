# Multiple devices

> **Status (3 Oct 2026).** Design only for control; detection and display are built. What exists: the setup wizard and the
> "Your system" block list every device Home Assistant has for each part, say which one PowerEngine uses ("in use now") and
> which it doesn't, and flag energy equipment no adapter owns (for example a second inverter of another brand). PowerEngine
> still reads, plans and controls **one inverter, one car charger, one tariff, one forecast and one grid-event provider**.
> Nothing here changes how the battery is driven.

## Why it matters

Several of these are normal homes, not edge cases:

| Part | Legitimate multiples | Today |
|---|---|---|
| Inverter and battery | Two inverters (same or different brand), each with its own battery or only solar; a second battery stack | One inverter. The owner has two in Home Assistant. |
| Solar | Several arrays | Supported (`solar_plants`). |
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

## Stages

Each stage is shippable and passes the replay unchanged for a single-inverter home.

- **M0, detect and list. Done.** The wizard and "Your system" show every candidate per part and mark the one in use; unsupported
  energy devices are listed with a way to send their entity list (docs/WIZARD.md).
- **M1, a second device that is read, not controlled.** `site.inverters` becomes a list of `{id, adapter, firmware}`; the first
  is the controlled one (`site.inverter` stays readable as that, so old configs load). Inputs are keyed per device
  (`battery_soc` for the first, `<id>.battery_soc` for the others, so the catalogue and saved configs don't change for one
  inverter). A device that is not controlled contributes its readings only: solar generation, house load and the grid
  check, battery power and SoC shown on the Health and Monitoring pages. No planning or control change. Needs: per-device
  role catalogue in the card (the size limit on `map_catalogue` means roles are published per definition, not all at once).
- **M2, a pooled battery in the plan.** The planner sees one virtual battery: summed capacity and power limits, SoC weighted by
  capacity, learned rates per device. One plan, still one controlled device. Safe because the plan is still executed by the
  one device, but wrong if the second battery then discharges on its own, so M2 only applies where the second inverter is
  configured to follow (an inverter that does its own self-use is a load/source the plan reads, not a battery it can plan).
- **M3, control of more than one device.** The command is split across devices (by headroom and SoC); each device has its own
  RAM refresh, following check, failsafe and write budget; each must be `verified` on its firmware (the Active guard is
  per device) and have passed the supervised tests. The write budget, damping and read-back checks apply per device. This is
  the large step: it needs a two-device replay fixture, a long Passive trial, and the owner's hardware.
- **Cars.** `site.car` becomes a list of cars (battery size, target, ready-by) linked to a charger; `site.ev_chargers` a list of
  chargers. Smart-charge requests then target a car. Independent of the inverter stages.
- **Grid events.** `site.events` becomes a list; events from all providers are merged by time with a stated priority.
  Independent of the inverter stages.

## Questions for the owner

- What is the second inverter (brand, model, integration), and what does it do: a second battery that should be planned
  with the first, or a generation-only inverter, or something that runs by itself?
- Do you want M1 (read it, show it, count its solar and house load) first? It is the cheapest step and needs no control risk.
- Cars: how many, and do they share the Zappi?
